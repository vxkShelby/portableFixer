import os
import re
import subprocess
import winreg
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath

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
# reg.exe's names for the only hives _UNINSTALL_REG_PATHS reads.
_HIVE_REG_NAMES = {
    winreg.HKEY_LOCAL_MACHINE: "HKLM",
    winreg.HKEY_CURRENT_USER: "HKCU",
}
_REG_EXPORT_TIMEOUT_SEC = 30


@dataclass
class InstalledProgram:
    name: str
    publisher: str
    version: str
    estimated_size_kb: int | None
    install_location: str | None
    install_date: str | None
    uninstall_string: str | None
    quiet_uninstall_string: str | None
    display_icon: str | None
    registry_hive: int
    registry_path: str


def _query(key, name: str):
    try:
        return winreg.QueryValueEx(key, name)[0]
    except OSError:
        return None


def _format_install_date(raw: object) -> str | None:
    # Windows stores this as an unpunctuated "YYYYMMDD" string (not a real
    # date type) - reformat to something readable, but never invent a date
    # out of a value that doesn't actually match that shape.
    text = str(raw) if raw else ""
    if len(text) == 8 and text.isdigit():
        return f"{text[0:4]}-{text[4:6]}-{text[6:8]}"
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
                                install_date=_format_install_date(_query(sk, "InstallDate")),
                                uninstall_string=_query(sk, "UninstallString"),
                                quiet_uninstall_string=_query(sk, "QuietUninstallString"),
                                display_icon=_query(sk, "DisplayIcon") or None,
                                registry_hive=hive,
                                registry_path=full_path,
                            )
                        )
                except OSError:
                    continue
    programs.sort(key=lambda p: p.name.lower())
    return programs


def find_program_by_name(name: str) -> InstalledProgram | None:
    target = name.strip().lower()
    for program in list_installed_programs():
        if program.name.strip().lower() == target:
            return program
    return None


def launch_program(program: InstalledProgram) -> bool:
    # DisplayIcon is usually "C:\...\app.exe" or "C:\...\app.exe,0" (icon
    # index suffix) - strip it. Falls back to the first .exe directly under
    # InstallLocation when there's no usable DisplayIcon.
    exe_path: str | None = None
    if program.display_icon:
        candidate = program.display_icon.rsplit(",", 1)[0].strip('"')
        if Path(candidate).is_file():
            exe_path = candidate
    if exe_path is None and program.install_location:
        install_dir = Path(program.install_location)
        if install_dir.is_dir():
            exes = list(install_dir.glob("*.exe"))
            if len(exes) == 1:
                exe_path = str(exes[0])
    if exe_path is None:
        return False
    try:
        os.startfile(exe_path)
        return True
    except OSError:
        return False


def program_command(program: InstalledProgram) -> str | None:
    # The quiet string wins: it is what uninstall_program runs, and the
    # confirmation and the audit log must show exactly that command.
    return program.quiet_uninstall_string or program.uninstall_string or None


def uninstall_program(program: InstalledProgram, timeout_sec: int = 300) -> tuple[bool, str]:
    command = program_command(program)
    if not command:
        return False, "No uninstall command found for this program."
    try:
        result = subprocess.run(
            ["cmd", "/c", command],
            capture_output=True, text=True, timeout=timeout_sec,
            creationflags=subprocess.CREATE_NO_WINDOW,
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


def registry_key_name(hive: int, path: str) -> str:
    """The key as reg.exe spells it, e.g. HKLM\\SOFTWARE\\..."""
    return f"{_HIVE_REG_NAMES.get(hive, str(hive))}\\{path}"


def backup_registry_key(hive: int, path: str, dest_file: Path, timeout_sec: int = _REG_EXPORT_TIMEOUT_SEC) -> bool:
    # A leftover entry is only ever deleted once this .reg copy exists, so
    # a wrongly flagged program can be put back with "reg import".
    root = _HIVE_REG_NAMES.get(hive)
    if root is None:
        return False
    dest = Path(dest_file)
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        # Output is left undecoded: reg.exe prints in the OEM code page, and
        # only the exit code and the written file matter here.
        result = subprocess.run(
            ["reg", "export", f"{root}\\{path}", str(dest), "/y"],
            capture_output=True, timeout=timeout_sec,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0 and dest.is_file()


def orphan_backup_path(backup_dir: Path, program_name: str) -> Path:
    # The name comes from the registry - keep letters (diacritics too),
    # digits, "." and "-" and replace the rest. Two leftovers can share a
    # DisplayName (HKLM + WOW6432Node), so an existing file is never reused.
    stem = re.sub(r"[^\w.-]+", "_", program_name).strip("._")[:80] or "entry"
    candidate = Path(backup_dir) / f"uninstall_{stem}.reg"
    counter = 2
    while candidate.exists():
        candidate = Path(backup_dir) / f"uninstall_{stem}_{counter}.reg"
        counter += 1
    return candidate


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


def _clean_location(install_location: str) -> str:
    # Some installers write InstallLocation wrapped in quotes - as written,
    # a path that never exists, which alone would flag it as a leftover.
    return install_location.strip().strip('"').strip()


def orphan_location_is_verifiable(install_location: str) -> bool:
    # A missing folder only means "uninstalled" when the drive (or network
    # share) it lives on is actually there. A program on an unplugged USB
    # disk, a BitLocker-locked volume or an unmapped share looks exactly
    # like a leftover otherwise - and its registry entry would get deleted.
    # Relative or unexpanded (%ProgramFiles%\...) paths can't be checked.
    location = PureWindowsPath(_clean_location(install_location))
    if not location.drive or not location.root:
        return False
    try:
        return Path(location.anchor).exists()
    except OSError:
        return False


def find_orphaned_uninstall_entries(reg_paths=_UNINSTALL_REG_PATHS) -> list[InstalledProgram]:
    # A classic leftover-junk signal: the uninstaller ran (or the user
    # deleted the install folder by hand) but never cleaned up its own
    # registry entry, so it keeps showing up as "installed" forever.
    orphans = []
    for program in list_installed_programs(reg_paths):
        if not program.install_location or not orphan_location_is_verifiable(program.install_location):
            continue
        try:
            missing = not Path(_clean_location(program.install_location)).exists()
        except OSError:
            # Unreadable (e.g. access denied) is not the same as gone.
            continue
        if missing:
            orphans.append(program)
    return orphans
