import subprocess
import winreg
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QThread, Signal

_UNINSTALL_REG_PATHS = (
    (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
    (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
    (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
)
# Release types Windows itself tags on Uninstall entries that are not a
# real, user-facing "program" (a patch/hotfix has no sensible standalone
# uninstall flow here) - filtered out alongside SystemComponent=1 entries.
_NON_PROGRAM_RELEASE_TYPES = {"Update", "Hotfix", "Security Update", "ServicePack"}


@dataclass
class InstalledProgram:
    name: str
    publisher: str
    version: str
    estimated_size_kb: int | None
    install_location: str | None
    uninstall_string: str | None
    quiet_uninstall_string: str | None
    registry_hive: int
    registry_path: str


def _query(key, name: str):
    try:
        return winreg.QueryValueEx(key, name)[0]
    except OSError:
        return None


def list_installed_programs(reg_paths=_UNINSTALL_REG_PATHS) -> list[InstalledProgram]:
    programs: list[InstalledProgram] = []
    seen_paths: set[tuple[int, str]] = set()
    for hive, base in reg_paths:
        try:
            root = winreg.OpenKey(hive, base)
        except OSError:
            continue
        with root:
            index = 0
            while True:
                try:
                    subkey_name = winreg.EnumKey(root, index)
                except OSError:
                    break
                index += 1
                full_path = f"{base}\\{subkey_name}"
                if (hive, full_path) in seen_paths:
                    continue
                try:
                    with winreg.OpenKey(hive, full_path) as sk:
                        name = _query(sk, "DisplayName")
                        if not name:
                            continue
                        if _query(sk, "SystemComponent") == 1:
                            continue
                        if _query(sk, "ReleaseType") in _NON_PROGRAM_RELEASE_TYPES:
                            continue
                        seen_paths.add((hive, full_path))
                        programs.append(
                            InstalledProgram(
                                name=str(name),
                                publisher=str(_query(sk, "Publisher") or ""),
                                version=str(_query(sk, "DisplayVersion") or ""),
                                estimated_size_kb=_query(sk, "EstimatedSize"),
                                install_location=_query(sk, "InstallLocation") or None,
                                uninstall_string=_query(sk, "UninstallString"),
                                quiet_uninstall_string=_query(sk, "QuietUninstallString"),
                                registry_hive=hive,
                                registry_path=full_path,
                            )
                        )
                except OSError:
                    continue
    programs.sort(key=lambda p: p.name.lower())
    return programs


def uninstall_program(program: InstalledProgram, timeout_sec: int = 300) -> tuple[bool, str]:
    command = program.quiet_uninstall_string or program.uninstall_string
    if not command:
        return False, "No uninstall command found for this program."
    try:
        result = subprocess.run(
            ["cmd", "/c", command],
            capture_output=True, text=True, timeout=timeout_sec,
        )
        output = (result.stdout + result.stderr).strip()
        return result.returncode == 0, output
    except subprocess.TimeoutExpired:
        return False, "Uninstaller timed out."
    except OSError as exc:
        return False, str(exc)


def registry_key_still_exists(program: InstalledProgram) -> bool:
    try:
        with winreg.OpenKey(program.registry_hive, program.registry_path):
            return True
    except OSError:
        return False


def remove_registry_key(hive: int, path: str) -> bool:
    try:
        winreg.DeleteKey(hive, path)
        return True
    except OSError:
        return False


class UninstallRunner(QThread):
    # Uninstallers are blocking subprocess calls (some show their own UI
    # and wait for the user) - runs off the GUI thread so the window stays
    # responsive, mirroring sysinfo.py's runner pattern.
    program_finished = Signal(str, bool, str)
    all_finished = Signal()

    def __init__(self, programs: list[InstalledProgram], parent=None):
        super().__init__(parent)
        self._programs = programs
        self.finished.connect(self.deleteLater)

    def run(self) -> None:
        for program in self._programs:
            ok, output = uninstall_program(program)
            self.program_finished.emit(program.name, ok, output)
        self.all_finished.emit()


def find_orphaned_uninstall_entries(reg_paths=_UNINSTALL_REG_PATHS) -> list[InstalledProgram]:
    # A classic leftover-junk signal: the uninstaller ran (or the user
    # deleted the install folder by hand) but never cleaned up its own
    # registry entry, so it keeps showing up as "installed" forever.
    orphans = []
    for program in list_installed_programs(reg_paths):
        if program.install_location and not Path(program.install_location).exists():
            orphans.append(program)
    return orphans
