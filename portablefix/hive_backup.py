"""Optional full registry hive backup before a DESTRUCTIVE batch (research G24).

A second safety net for when the restore point fails or System Protection
is off: `reg save` copies HKLM\\SOFTWARE and HKLM\\SYSTEM into
Backups/<run_id>/hives-<time>/. It is never restored automatically - a live
hive cannot be swapped under a running Windows, and rolling it back undoes
every registry change since the backup, not just PortableFix's - so
undo.ps1 only names the folder and how to restore it offline.

Qt-free core (save_hives) so tests drive it with a fake `run`; the runner
thread is a thin wrapper like restore_point.RestorePointRunner.
"""

import os
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from PySide6.QtCore import QThread, Signal

# The two machine hives that decide whether Windows boots and what is
# installed. NTUSER.DAT is left out: it is locked by the logged-on user's
# session and is not what a DESTRUCTIVE system action breaks.
HIVES = ("SOFTWARE", "SYSTEM")
# Per hive. SOFTWARE is often 100-300 MB; on a slow USB stick or HDD the
# copy takes a while, but reg.exe never waits for input, so this only
# catches a genuinely hung call.
HIVE_SAVE_TIMEOUT_SEC = 300
HIVE_FILE_SUFFIX = ".hiv"


def reg_executable() -> str:
    # Absolute path when it is where Windows always puts it - a broken PATH
    # is exactly the kind of machine this tool is pointed at (same reasoning
    # as paths.powershell_executable).
    system_root = os.environ.get("SystemRoot") or os.environ.get("WINDIR")
    if system_root:
        candidate = os.path.join(system_root, "System32", "reg.exe")
        if os.path.isfile(candidate):
            return candidate
    return "reg"


def backup_dir(base_dir: Path, run_id: str, now: datetime | None = None) -> Path:
    # A timestamped folder per backup: a second DESTRUCTIVE batch in the same
    # session must never overwrite the older (pre-everything) copy.
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%d-%H%M%S")
    candidate = Path(base_dir) / "Backups" / run_id / f"hives-{stamp}"
    counter = 2
    while candidate.exists():
        candidate = Path(base_dir) / "Backups" / run_id / f"hives-{stamp}-{counter}"
        counter += 1
    return candidate


def save_commands(dest_dir: Path) -> list[list[str]]:
    reg = reg_executable()
    return [[reg, "save", f"HKLM\\{hive}", str(Path(dest_dir) / f"{hive}{HIVE_FILE_SUFFIX}"), "/y"] for hive in HIVES]


def command_text(dest_dir: Path) -> str:
    # For the audit log: what ran, without the absolute reg.exe path noise.
    return "; ".join(f'reg save HKLM\\{hive} "{Path(dest_dir) / (hive + HIVE_FILE_SUFFIX)}" /y' for hive in HIVES)


def estimate_bytes() -> int | None:
    """Size of the live hive files - roughly what the backup will take.
    None when unknown (not Windows, files unreadable); the review screen
    then just leaves the size out."""
    system_root = os.environ.get("SystemRoot") or os.environ.get("WINDIR")
    if not system_root:
        return None
    total = 0
    try:
        for hive in HIVES:
            # stat() works on a hive file Windows holds open.
            total += os.stat(os.path.join(system_root, "System32", "config", hive)).st_size
    except OSError:
        return None
    return total


@dataclass
class HiveBackupResult:
    success: bool
    detail: str
    dest_dir: Path
    files: list[Path] = field(default_factory=list)


def save_hives(dest_dir: Path, run=subprocess.run) -> HiveBackupResult:
    """Save every hive in HIVES; success only if all of them were written.
    Only exit codes and the written files are trusted - reg.exe's text is
    localized and in the OEM code page, so it is never parsed."""
    dest_dir = Path(dest_dir)
    try:
        dest_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return HiveBackupResult(False, f"Cannot create {dest_dir}: {exc}", dest_dir)
    saved: list[Path] = []
    for hive, argv in zip(HIVES, save_commands(dest_dir)):
        target = dest_dir / f"{hive}{HIVE_FILE_SUFFIX}"
        try:
            result = run(
                argv, capture_output=True, timeout=HIVE_SAVE_TIMEOUT_SEC,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except subprocess.TimeoutExpired:
            return HiveBackupResult(False, f"reg save HKLM\\{hive} timed out after {HIVE_SAVE_TIMEOUT_SEC}s.", dest_dir, saved)
        except OSError as exc:
            return HiveBackupResult(False, f"reg save HKLM\\{hive} could not start: {exc}", dest_dir, saved)
        if result.returncode != 0 or not target.is_file():
            # Exit 1 is what a missing SeBackupPrivilege (not elevated) or a
            # full disk looks like.
            return HiveBackupResult(
                False, f"reg save HKLM\\{hive} failed (exit {result.returncode}).", dest_dir, saved,
            )
        saved.append(target)
    return HiveBackupResult(True, "", dest_dir, saved)


class HiveBackupRunner(QThread):
    # (success, detail, HiveBackupResult)
    result_ready = Signal(bool, str, object)

    def __init__(self, dest_dir: Path, parent=None):
        super().__init__(parent)
        self._dest_dir = Path(dest_dir)
        self.finished.connect(self.deleteLater)

    def run(self) -> None:
        result = save_hives(self._dest_dir)
        self.result_ready.emit(result.success, result.detail, result)
