"""Client handoff package: one ZIP with everything about a single run.

The technician emails or archives it after a visit - the run's HTML/JSON
report, its audit log, its undo script (when one was written) and a short
bilingual README explaining the files. Only files of that one run are ever
included: never Data/settings.json, never another run's logs, and never
anything that resolves outside the state directory (symlinks/junctions).

Optionally (research G19, off by default) the technician adds an evidence
bundle under diagnostics/: Windows' own built-in reports (msinfo32,
systeminfo, battery report, dxdiag, winget export, the last 7 days of
System/Application errors, driverquery, ipconfig) generated at handoff
time, so proof of the PC's state stays with the technician instead of on
the client's machine. Every report has its own timeout and a failed or
hung one is only noted in diagnostics/README.txt - it never fails the
handoff. Nothing is redacted: the README says plainly what each file holds
and that it contains personal data, which is why it is opt-in.
"""

import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QThread, Signal

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
  diagnostics/     (len ak ho technik pribalil) Vstavané reporty Windows
                   o stave počítača - obsahujú osobné údaje, pozri
                   diagnostics/README.txt.

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
  diagnostics/     (only if the technician included it) Windows' built-in
                   reports on the state of the PC - they contain personal
                   data, see diagnostics/README.txt.

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


DIAG_DIR = "diagnostics"
ARC_DIAG_README = f"{DIAG_DIR}/README.txt"

# Placeholder in a report's argv for the path of the file it writes itself.
_OUT = "{out}"
# Critical (1) and Error (2) events of the last 7 days (604800000 ms). An
# XPath filter on numeric levels and a timediff - never the localized level
# names - so it works the same on every Windows display language.
_EVENT_QUERY = "/q:*[System[(Level=1 or Level=2) and TimeCreated[timediff(@SystemTime) <= 604800000]]]"
# A report bigger than this is left out rather than bloating the zip; a
# filtered 7-day evtx or an .nfo is a few MB, so only something broken hits it.
MAX_DIAG_FILE_BYTES = 200 * 1024 * 1024
# How often the wait loop checks for a stop request while a report runs.
_POLL_SEC = 0.25

REQUIRES_BATTERY = "battery"
REQUIRES_WINGET = "winget"

STATUS_OK = "ok"
STATUS_SKIPPED = "skipped"
STATUS_FAILED = "failed"
STATUS_TIMEOUT = "timeout"
_STOPPED = "stopped"


@dataclass(frozen=True)
class DiagnosticSpec:
    arcname: str  # file name inside diagnostics/
    argv: tuple[str, ...]  # _OUT is replaced by the output path
    timeout_sec: float
    # True: the command prints the report, which is saved as-is (raw bytes
    # in the console code page - nothing is decoded or parsed). False: the
    # command writes the file named by _OUT itself.
    stdout_report: bool
    sk: str
    en: str
    requires: str | None = None


# Fixed order: quick and most useful first, so a technician who gives up
# waiting still got the basics. powercfg /energy is deliberately missing:
# it observes the system for 60 s and adds little the others do not.
DIAGNOSTIC_REPORTS: tuple[DiagnosticSpec, ...] = (
    DiagnosticSpec(
        "systeminfo.csv", ("systeminfo", "/fo", "csv"), 120, True,
        "systeminfo – verzia Windows, hardvér, hotfixy, sieťové karty, doména, registrovaný vlastník.",
        "systeminfo - Windows version, hardware, hotfixes, network cards, domain, registered owner.",
    ),
    DiagnosticSpec(
        "ipconfig.txt", ("ipconfig", "/all"), 60, True,
        "ipconfig /all – IP adresy, MAC adresy, DNS servery, DHCP, názov počítača.",
        "ipconfig /all - IP and MAC addresses, DNS servers, DHCP, computer name.",
    ),
    DiagnosticSpec(
        "driverquery.csv", ("driverquery", "/v", "/fo", "csv"), 120, True,
        "driverquery /v – nainštalované ovládače, ich stav a cesty k súborom.",
        "driverquery /v - installed drivers, their state and file paths.",
    ),
    DiagnosticSpec(
        "System-errors-7d.evtx",
        ("wevtutil", "epl", "System", _OUT, _EVENT_QUERY, "/ow:true"), 180, False,
        "Kritické udalosti a chyby z denníka System za posledných 7 dní (otvorte v Zobrazovači udalostí).",
        "Critical and error events from the System log, last 7 days (open in Event Viewer).",
    ),
    DiagnosticSpec(
        "Application-errors-7d.evtx",
        ("wevtutil", "epl", "Application", _OUT, _EVENT_QUERY, "/ow:true"), 180, False,
        "Kritické udalosti a chyby z denníka Application za posledných 7 dní – môžu obsahovať "
        "názvy súborov a používateľov.",
        "Critical and error events from the Application log, last 7 days - may contain file and "
        "user names.",
    ),
    DiagnosticSpec(
        "battery-report.html", ("powercfg", "/batteryreport", "/output", _OUT), 120, False,
        "powercfg /batteryreport – kapacita a opotrebenie batérie, história nabíjania (len notebooky).",
        "powercfg /batteryreport - battery capacity and wear, charge history (laptops only).",
        requires=REQUIRES_BATTERY,
    ),
    DiagnosticSpec(
        "winget-export.json",
        ("winget", "export", "-o", _OUT, "--include-versions", "--accept-source-agreements"), 180, False,
        "winget export – zoznam aplikácií, ktoré pozná winget/Store, aj s verziami.",
        "winget export - list of installed applications known to winget/Store, with versions.",
        requires=REQUIRES_WINGET,
    ),
    DiagnosticSpec(
        "dxdiag.txt", ("dxdiag", "/t", _OUT), 180, False,
        "dxdiag /t – grafika, zvuk, monitory, ovládače DirectX.",
        "dxdiag /t - graphics, sound, displays, DirectX drivers.",
    ),
    # Last: the slowest (often a minute or more) and the most complete one.
    # "+all-loadedmodules" is the documented msinfo32 /categories form for
    # "everything except Loaded Modules" - that category (every DLL of every
    # process) is what usually drags msinfo32 on for minutes.
    DiagnosticSpec(
        "msinfo32.nfo", ("msinfo32", "/nfo", _OUT, "/categories", "+all-loadedmodules"), 300, False,
        "msinfo32 /nfo – úplné Systémové informácie (otvorte v msinfo32): hardvér, ovládače, "
        "služby, spustené programy, premenné prostredia.",
        "msinfo32 /nfo - full System Information (open in msinfo32): hardware, drivers, services, "
        "running programs, environment variables.",
    ),
)


@dataclass
class DiagnosticResult:
    arcname: str
    status: str  # STATUS_*
    detail: str = ""  # short and language-neutral: "exit 5", "300 s", "no battery"
    path: Path | None = None  # the collected file when status is ok


class HandoffCancelled(Exception):
    """The collection was stopped (the app is closing) - no zip is written."""


def _has_battery() -> bool | None:
    """True/False from GetSystemPowerStatus, None when it cannot tell (the
    report is then simply attempted and a failure noted)."""
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes

    class _Status(ctypes.Structure):
        _fields_ = [
            ("ACLineStatus", ctypes.c_ubyte),
            ("BatteryFlag", ctypes.c_ubyte),
            ("BatteryLifePercent", ctypes.c_ubyte),
            ("SystemStatusFlag", ctypes.c_ubyte),
            ("BatteryLifeTime", wintypes.DWORD),
            ("BatteryFullLifeTime", wintypes.DWORD),
        ]

    status = _Status()
    if not ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(status)):
        return None
    # BatteryFlag 128 = no system battery (a desktop), 255 = unknown.
    if status.BatteryFlag == 255:
        return None
    return status.BatteryFlag != 128


def _find_winget() -> str | None:
    # Imported here: the lookup in winget_updates already handles the
    # elevated session whose PATH lacks WindowsApps.
    from .winget_updates import find_winget_executable

    return find_winget_executable()


def _run_report(argv: list[str], timeout_sec: float, stdout_path: Path | None,
                should_stop: Callable[[], bool]) -> tuple[int | None, str]:
    """Run one report command: (exit code, "") when it finished, (None,
    STATUS_TIMEOUT or _STOPPED) when it was killed. OSError propagates."""
    out = open(stdout_path, "wb") if stdout_path is not None else None
    try:
        proc = subprocess.Popen(
            argv, stdin=subprocess.DEVNULL,
            stdout=out if out is not None else subprocess.DEVNULL,
            # Never into the report file: stderr is warnings in the display
            # language and would break the CSVs; failure is the exit code.
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        deadline = time.monotonic() + timeout_sec
        # A polling wait instead of one proc.wait(timeout): closing the app
        # must not sit out the rest of a 5-minute msinfo32.
        while True:
            try:
                return proc.wait(timeout=_POLL_SEC), ""
            except subprocess.TimeoutExpired:
                pass
            stopped = should_stop()
            if stopped or time.monotonic() >= deadline:
                proc.kill()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    pass
                return None, _STOPPED if stopped else STATUS_TIMEOUT
    finally:
        if out is not None:
            out.close()


def collect_diagnostics(
    work_dir: Path,
    progress: Callable[[int, int, str], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
    reports: tuple[DiagnosticSpec, ...] = DIAGNOSTIC_REPORTS,
) -> list[DiagnosticResult]:
    """Generate every report into `work_dir`, one result per report, in
    order. A single report's failure never raises - only HandoffCancelled
    when `should_stop` turns true."""
    should_stop = should_stop or (lambda: False)
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    results: list[DiagnosticResult] = []
    total = len(reports)
    for index, spec in enumerate(reports, start=1):
        if should_stop():
            raise HandoffCancelled()
        if progress is not None:
            progress(index, total, spec.arcname)
        argv = list(spec.argv)
        if spec.requires == REQUIRES_BATTERY and _has_battery() is False:
            results.append(DiagnosticResult(spec.arcname, STATUS_SKIPPED, "no battery"))
            continue
        if spec.requires == REQUIRES_WINGET:
            winget = _find_winget()
            if not winget:
                results.append(DiagnosticResult(spec.arcname, STATUS_SKIPPED, "winget not found"))
                continue
            argv[0] = winget
        target = work_dir / spec.arcname
        argv = [str(target) if arg == _OUT else arg for arg in argv]
        try:
            code, killed = _run_report(
                argv, spec.timeout_sec, target if spec.stdout_report else None, should_stop
            )
        except (OSError, subprocess.SubprocessError) as exc:
            target.unlink(missing_ok=True)
            results.append(DiagnosticResult(spec.arcname, STATUS_FAILED, type(exc).__name__))
            continue
        if killed == _STOPPED:
            raise HandoffCancelled()
        if killed == STATUS_TIMEOUT:
            # A half-written report would look complete to whoever reads it.
            _discard(target)
            results.append(DiagnosticResult(spec.arcname, STATUS_TIMEOUT, f"{spec.timeout_sec:g} s"))
            continue
        try:
            size = target.stat().st_size if target.is_file() and not target.is_symlink() else 0
        except OSError:
            size = 0
        # Success is the exit code plus a non-empty file - never the text a
        # command prints, which is localized. winget export exits non-zero
        # when some package has no source but still writes the list, so a
        # file a command wrote itself is kept and the exit code noted.
        if size == 0 or (code != 0 and spec.stdout_report):
            _discard(target)
            results.append(DiagnosticResult(spec.arcname, STATUS_FAILED, f"exit {code}"))
            continue
        if size > MAX_DIAG_FILE_BYTES:
            _discard(target)
            results.append(DiagnosticResult(spec.arcname, STATUS_SKIPPED, f"{size // (1024 * 1024)} MB"))
            continue
        detail = "" if code == 0 else f"exit {code}"
        results.append(DiagnosticResult(spec.arcname, STATUS_OK, detail, target))
    return results


def _discard(path: Path) -> None:
    # A killed process may still hold its file for a moment on Windows; the
    # temp folder is removed afterwards anyway, so a failed unlink is fine.
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


_STATUS_TEXT = {
    STATUS_OK: ("zahrnuté", "included"),
    STATUS_SKIPPED: ("vynechané", "skipped"),
    STATUS_FAILED: ("zlyhalo", "failed"),
    STATUS_TIMEOUT: ("vypršal čas", "timed out"),
}


def diagnostics_readme(results: list[DiagnosticResult], hostname: str, run_id: str,
                       dry_run: bool = False,
                       reports: tuple[DiagnosticSpec, ...] = DIAGNOSTIC_REPORTS) -> str:
    """Bilingual diagnostics/README.txt: the personal data warning, what
    each file holds and what happened to every report."""
    specs = {spec.arcname: spec for spec in reports}
    width = max((len(r.arcname) for r in results), default=0)

    def _rows(lang: int) -> list[str]:
        rows = []
        for result in results:
            status = _STATUS_TEXT[result.status][lang]
            if result.detail:
                status += f" ({result.detail})"
            rows.append(f"  {result.arcname.ljust(width)}  [{status}]")
            spec = specs.get(result.arcname)
            if spec is not None:
                rows.append(f"  {' ' * width}  {spec.sk if lang == 0 else spec.en}")
        return rows

    lines = [
        "PortableFix - diagnostika Windows / Windows diagnostics",
        "=======================================================",
        "",
        f"Počítač / Computer:   {hostname}",
        f"Beh / Run ID:         {run_id}",
        f"Vytvorené / Created:  {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "",
        "SLOVENSKY",
        "---------",
        "Tieto súbory vytvorili vstavané nástroje Windows v čase uloženia balíka.",
        "POZOR: nič v nich nebolo anonymizované. Obsahujú osobné a citlivé údaje:",
        "názov počítača, mená používateľov a cesty k ich priečinkom, sieťové",
        "nastavenia (IP a MAC adresy, DNS, Wi-Fi), zoznam programov a udalosti",
        "systému. Balík odovzdajte len klientovi alebo osobe, ktorej to klient",
        "dovolil.",
        "Textové súbory (.txt, .csv) sú uložené presne tak, ako ich Windows",
        "vypísal (kódová stránka konzoly) - diakritika sa v inom editore môže",
        "zobraziť nesprávne.",
    ]
    if dry_run:
        lines += [
            "Beh bol v režime DRY-RUN (nič sa neopravovalo). Diagnostika sa aj tak",
            "zozbierala, lebo tieto príkazy systém iba čítajú.",
        ]
    lines += [
        "",
        *_rows(0),
        "",
        "ENGLISH",
        "-------",
        "These files were produced by Windows' built-in tools when the package",
        "was saved.",
        "WARNING: nothing in them was anonymized. They contain personal and",
        "sensitive data: the computer name, user names and their folder paths,",
        "network configuration (IP and MAC addresses, DNS, Wi-Fi), the list of",
        "programs and system events. Hand the package only to the client or to",
        "someone the client allowed.",
        "Text files (.txt, .csv) are stored exactly as Windows printed them (the",
        "console code page) - accented letters may look wrong in another editor.",
    ]
    if dry_run:
        lines += [
            "The run was a DRY-RUN (nothing was repaired). Diagnostics were still",
            "collected because these commands only read the system.",
        ]
    lines += ["", *_rows(1), ""]
    return "\n".join(lines)


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


def _write_text(zf: zipfile.ZipFile, arcname: str, text: str) -> None:
    info = zipfile.ZipInfo(arcname, date_time=datetime.now().timetuple()[:6])
    info.compress_type = zipfile.ZIP_DEFLATED
    # CRLF: the client most likely opens it in Notepad on Windows.
    zf.writestr(info, text.replace("\n", "\r\n").encode("utf-8-sig"))


def build_handoff_zip(
    state_dir: Path, hostname: str, run_id: str, dest_path: Path,
    include_diagnostics: bool = False, dry_run: bool = False,
    progress: Callable[[int, int, str], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> Path:
    """Write PortableFix_<host>_<run_id>.zip-style package to `dest_path`.

    Written to a temp file next to the destination and moved into place with
    os.replace, so a full/yanked USB stick never leaves a truncated zip under
    the final name. Raises ValueError for an unsafe hostname/run_id or when
    none of the run's files exist, OSError when writing fails, and
    HandoffCancelled when `should_stop` ends the diagnostics collection.

    include_diagnostics runs the Windows reports (minutes - call it from a
    worker thread) and adds them under diagnostics/; with it off no command
    is started at all.
    """
    sources = package_sources(state_dir, hostname, run_id)
    if not sources:
        raise ValueError(f"no files found for run {run_id!r}")
    dest_path = Path(dest_path)
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    # Reports go to a private temp folder on the client PC, not next to the
    # zip: other programs write them, and a slow USB stick would only make
    # msinfo32/dxdiag slower. Removed again right after zipping.
    diag_dir = Path(tempfile.mkdtemp(prefix="pf-diag-")) if include_diagnostics else None
    try:
        diag_results: list[DiagnosticResult] = []
        if diag_dir is not None:
            diag_results = collect_diagnostics(diag_dir, progress, should_stop)
        fd, tmp_name = tempfile.mkstemp(prefix=".handoff-", suffix=".tmp", dir=dest_path.parent)
        tmp_path = Path(tmp_name)
        try:
            with os.fdopen(fd, "wb") as raw, zipfile.ZipFile(raw, "w", zipfile.ZIP_DEFLATED) as zf:
                _write_text(zf, ARC_README, README_TEXT.format(hostname=hostname, run_id=run_id))
                for arcname, source in sources:
                    zf.write(source, arcname=arcname)
                if diag_dir is not None:
                    _write_text(zf, ARC_DIAG_README, diagnostics_readme(diag_results, hostname, run_id, dry_run))
                    for result in diag_results:
                        if result.status == STATUS_OK and result.path is not None:
                            zf.write(result.path, arcname=f"{DIAG_DIR}/{result.arcname}")
            os.replace(tmp_path, dest_path)
        except BaseException:
            tmp_path.unlink(missing_ok=True)
            raise
    finally:
        if diag_dir is not None:
            # A report process killed on timeout may still hold its file for
            # a moment - a leftover in %TEMP% is harmless, a cleanup error
            # failing the handoff is not.
            shutil.rmtree(diag_dir, ignore_errors=True)
    return dest_path


class HandoffRunner(QThread):
    """Builds the package off the GUI thread when diagnostics are included."""

    # (index, total, report file name)
    progress = Signal(int, int, str)
    # (saved Path or None, error: "" | "no_files" | "failed" | "cancelled", detail)
    result_ready = Signal(object, str, str)

    def __init__(self, state_dir: Path, hostname: str, run_id: str, dest_path: Path,
                 dry_run: bool = False, parent=None):
        super().__init__(parent)
        self._args = (Path(state_dir), hostname, run_id, Path(dest_path))
        self._dry_run = dry_run
        self.finished.connect(self.deleteLater)

    def run(self) -> None:
        try:
            saved = build_handoff_zip(
                *self._args, include_diagnostics=True, dry_run=self._dry_run,
                progress=self.progress.emit, should_stop=self.isInterruptionRequested,
            )
        except HandoffCancelled:
            self.result_ready.emit(None, "cancelled", "")
        except ValueError as exc:
            self.result_ready.emit(None, "no_files", str(exc))
        except OSError as exc:
            self.result_ready.emit(None, "failed", str(exc))
        else:
            self.result_ready.emit(saved, "", "")
