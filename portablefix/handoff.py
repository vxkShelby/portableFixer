"""Client handoff package: one ZIP with everything about a single run.

The technician emails or archives it after a visit - the run's HTML/JSON
report, its audit log, its undo script (when one was written) and a short
bilingual README explaining the files. Only files of that one run are ever
included: never Data/settings.json, never another run's logs, and never
anything that resolves outside the state directory (symlinks/junctions).
"""

import os
import re
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

from . import history
from .audit_log import audit_log_path

# Fixed arc names, independent of hostname/run_id, so the README can refer
# to them literally and the archive layout is the same for every client.
ARC_REPORT_HTML = "report.html"
ARC_REPORT_JSON = "report.json"
ARC_AUDIT_LOG = "audit_log.jsonl"
ARC_UNDO = "undo.ps1"
ARC_README = "README.txt"

# A hostname or run_id ends up in a file name inside state_dir - reject
# anything that could name a different directory (separators, drive colons,
# "..", control characters) before a path is ever built from it.
_UNSAFE_NAME = re.compile(r'[\\/:*?"<>|\x00-\x1f]')

README_TEXT = """\
PortableFix - balík pre klienta / client package
=================================================

Počítač / Computer: {hostname}
Beh / Run ID:       {run_id}

SLOVENSKY
---------
Tento balík obsahuje záznam o servisnom zásahu nástrojom PortableFix na
vyššie uvedenom počítači.

  report.html      Prehľadný report - otvorte v prehliadači. Obsahuje
                   zoznam vykonaných akcií, ich výsledok a stav systému
                   pred a po zásahu.
  report.json      Ten istý report v strojovo čitateľnej podobe (pre
                   archiváciu alebo ďalšie spracovanie).
  audit_log.jsonl  Podrobný auditný záznam - každý príkaz, jeho výstup,
                   návratový kód a potvrdenia technika, v poradí vykonania.
  undo.ps1         (len ak bol vytvorený) Skript, ktorý vráti vratné
                   zmeny z tohto behu.

Ako bezpečne použiť undo.ps1:
  1. Spúšťajte ho IBA na tom istom počítači ({hostname}) - na inom PC
     môže napáchať škodu.
  2. Najprv si ho otvorte v Poznámkovom bloku a skontrolujte obsah.
     Časť "NOT reversible" vypisuje zmeny, ktoré vrátiť nejde.
  3. Spustite ho v PowerShelli ako správca:
       powershell -ExecutionPolicy Bypass -File .\\undo.ps1
  4. Vracia len zmeny z tohto jedného behu. Ak sa odvtedy robili ďalšie
     zásahy, poraďte sa najskôr s technikom.
  5. Ak si nie ste istí, nespúšťajte ho - kontaktujte technika.

ENGLISH
-------
This package records a service visit made with PortableFix on the
computer named above.

  report.html      Human-readable report - open it in a web browser. Lists
                   the actions that ran, their results and the system state
                   before and after.
  report.json      The same report in machine-readable form (for archiving
                   or further processing).
  audit_log.jsonl  Detailed audit trail - every command, its output, exit
                   code and the technician's confirmations, in order.
  undo.ps1         (only if one was created) Script that reverts the
                   reversible changes made in this run.

How to use undo.ps1 safely:
  1. Run it ONLY on the same computer ({hostname}) - on another PC it
     can do damage.
  2. Open it in Notepad first and review it. The "NOT reversible"
     section lists changes that cannot be rolled back.
  3. Run it from PowerShell as Administrator:
       powershell -ExecutionPolicy Bypass -File .\\undo.ps1
  4. It only reverts changes from this one run. If more work was done on
     the PC since, ask the technician first.
  5. If in doubt, do not run it - contact the technician.
"""


def default_package_name(hostname: str, run_id: str) -> str:
    return f"PortableFix_{hostname}_{run_id}.zip"


def _check_name(value: str, what: str) -> None:
    if (
        not value
        or value in (".", "..")
        or ".." in value
        or _UNSAFE_NAME.search(value)
        or value != value.strip()
        or len(value) > 200
    ):
        raise ValueError(f"unsafe {what}: {value!r}")


def _inside(path: Path, root: Path) -> Path | None:
    """The resolved regular file behind `path`, or None when it is missing,
    not a regular file, a symlink, or resolves outside `root`."""
    try:
        if path.is_symlink() or not path.is_file():
            return None
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError):
        return None
    if not resolved.is_relative_to(root):
        return None
    return resolved


def package_sources(state_dir: Path, hostname: str, run_id: str) -> list[tuple[str, Path]]:
    """(arc name, resolved source) of every file of this run that exists and
    is safe to include, in a fixed order. Raises ValueError on a hostname or
    run_id that could escape state_dir."""
    _check_name(hostname, "hostname")
    _check_name(run_id, "run_id")
    root = Path(state_dir).resolve()
    html_path, json_path = history.run_report_paths(root / "Reports", hostname, run_id)
    candidates = [
        (ARC_REPORT_HTML, html_path),
        (ARC_REPORT_JSON, json_path),
        (ARC_AUDIT_LOG, audit_log_path(root, run_id)),
        (ARC_UNDO, root / "Backups" / run_id / "undo.ps1"),
    ]
    found: list[tuple[str, Path]] = []
    for arcname, path in candidates:
        resolved = _inside(path, root)
        if resolved is not None:
            found.append((arcname, resolved))
    return found


def build_handoff_zip(state_dir: Path, hostname: str, run_id: str, dest_path: Path) -> Path:
    """Write PortableFix_<host>_<run_id>.zip-style package to `dest_path`.

    Written to a temp file next to the destination and moved into place with
    os.replace, so a full/yanked USB stick never leaves a truncated zip under
    the final name. Raises ValueError for an unsafe hostname/run_id or when
    none of the run's files exist, OSError when writing fails.
    """
    sources = package_sources(state_dir, hostname, run_id)
    if not sources:
        raise ValueError(f"no files found for run {run_id!r}")
    dest_path = Path(dest_path)
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".handoff-", suffix=".tmp", dir=dest_path.parent)
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as raw, zipfile.ZipFile(raw, "w", zipfile.ZIP_DEFLATED) as zf:
            readme = zipfile.ZipInfo(ARC_README, date_time=datetime.now().timetuple()[:6])
            readme.compress_type = zipfile.ZIP_DEFLATED
            # CRLF: the client most likely opens it in Notepad on Windows.
            text = README_TEXT.format(hostname=hostname, run_id=run_id).replace("\n", "\r\n")
            zf.writestr(readme, text.encode("utf-8-sig"))
            for arcname, source in sources:
                zf.write(source, arcname=arcname)
        os.replace(tmp_path, dest_path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise
    return dest_path
