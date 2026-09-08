import subprocess
from dataclasses import dataclass

from PySide6.QtCore import QThread, Signal

_SCAN_TIMEOUT_SEC = 60
_UPDATE_TIMEOUT_SEC = 300


@dataclass
class OutdatedPackage:
    name: str
    id: str
    installed_version: str
    available_version: str
    source: str


def parse_winget_upgrade_table(text: str) -> list[OutdatedPackage]:
    # winget's "upgrade" output is a fixed-width text table (no --output
    # json support for this subcommand) - the header row's column start
    # offsets are the only reliable way to slice data rows, since names
    # and versions can contain arbitrary spaces.
    lines = text.splitlines()
    header_idx = None
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("Name") and "Id" in line and "Version" in line:
            header_idx = i
            break
    if header_idx is None:
        return []
    header = lines[header_idx]

    def col_start(label: str) -> int | None:
        idx = header.find(label)
        return idx if idx >= 0 else None

    name_start = col_start("Name")
    id_start = col_start("Id")
    version_start = col_start("Version")
    available_start = col_start("Available")
    source_start = col_start("Source")
    if None in (name_start, id_start, version_start, available_start):
        return []

    def slice_col(line: str, start: int | None, end: int | None) -> str:
        if start is None:
            return ""
        return line[start:end].strip()

    packages: list[OutdatedPackage] = []
    for line in lines[header_idx + 1:]:
        if not line.strip():
            break
        if set(line.strip()) <= {"-"}:
            continue
        if line.strip().endswith("upgrades available.") or line.strip().endswith("upgrade available."):
            break
        name = slice_col(line, name_start, id_start)
        pkg_id = slice_col(line, id_start, version_start)
        version = slice_col(line, version_start, available_start)
        available = slice_col(line, available_start, source_start)
        source = slice_col(line, source_start, None) if source_start is not None else ""
        if not name or not pkg_id:
            continue
        packages.append(
            OutdatedPackage(
                name=name, id=pkg_id, installed_version=version,
                available_version=available, source=source,
            )
        )
    return packages


def list_outdated_packages() -> list[OutdatedPackage]:
    try:
        result = subprocess.run(
            ["winget", "upgrade", "--include-unknown", "--accept-source-agreements"],
            capture_output=True, text=True, timeout=_SCAN_TIMEOUT_SEC,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    return parse_winget_upgrade_table(result.stdout)


def update_package(package_id: str, timeout_sec: int = _UPDATE_TIMEOUT_SEC) -> tuple[bool, str]:
    try:
        result = subprocess.run(
            [
                "winget", "upgrade", "--id", package_id, "--silent", "--include-unknown",
                "--accept-package-agreements", "--accept-source-agreements",
                "--disable-interactivity",
            ],
            capture_output=True, text=True, timeout=timeout_sec,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        output = (result.stdout + result.stderr).strip()
        return result.returncode == 0, output
    except subprocess.TimeoutExpired:
        return False, "Update timed out."
    except OSError as exc:
        return False, str(exc)


class WingetScanRunner(QThread):
    scan_finished = Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.finished.connect(self.deleteLater)

    def run(self) -> None:
        self.scan_finished.emit(list_outdated_packages())


class WingetUpdateRunner(QThread):
    # Each winget upgrade is a blocking subprocess call - runs off the GUI
    # thread so the window stays responsive, mirroring uninstaller.py.
    package_started = Signal(str)
    package_finished = Signal(str, bool, str)
    all_finished = Signal()

    def __init__(self, package_ids: list[str], parent=None):
        super().__init__(parent)
        self._package_ids = package_ids
        self.finished.connect(self.deleteLater)

    def run(self) -> None:
        for package_id in self._package_ids:
            self.package_started.emit(package_id)
            ok, output = update_package(package_id)
            self.package_finished.emit(package_id, ok, output)
        self.all_finished.emit()
