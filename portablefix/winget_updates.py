import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

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


def export_package_list(packages: list[OutdatedPackage], path: Path) -> None:
    data = [{"id": p.id, "name": p.name} for p in packages]
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def import_package_ids(path: Path) -> set[str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return {item["id"] for item in data if isinstance(item, dict) and "id" in item}


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


def _find_install_location(package_name: str) -> str | None:
    # A handful of packages (Blizzard's Battle.net is the common one) don't
    # publish an install location winget can infer on its own and refuse to
    # upgrade without --location. The registry Uninstall entry for the same
    # program (matched by display name) already has it.
    from . import uninstaller

    target = package_name.strip().lower()
    for program in uninstaller.list_installed_programs():
        if program.name.strip().lower() == target and program.install_location:
            return program.install_location
    return None


def _run_winget_upgrade(package_id: str, timeout_sec: int, extra_args: list[str] | None = None) -> tuple[bool, str]:
    args = [
        "winget", "upgrade", "--id", package_id, "--silent", "--include-unknown",
        "--accept-package-agreements", "--accept-source-agreements",
        "--disable-interactivity",
    ]
    if extra_args:
        args.extend(extra_args)
    process = subprocess.Popen(
        args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        creationflags=subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP,
    )
    try:
        output, _ = process.communicate(timeout=timeout_sec)
    except subprocess.TimeoutExpired:
        # winget can spawn a child installer/MSI that outlives winget.exe
        # itself - a plain process.kill() (what subprocess.run's own timeout
        # handling does) only kills winget.exe, leaving that child running
        # and possibly holding a file lock. taskkill /T reaps the whole tree.
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(process.pid)],
            capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW,
        )
        raise
    return process.returncode == 0, (output or "").strip()


def update_package(package: OutdatedPackage, timeout_sec: int = _UPDATE_TIMEOUT_SEC) -> tuple[bool, str]:
    try:
        ok, output = _run_winget_upgrade(package.id, timeout_sec)
        if not ok and "install location is required" in output.lower():
            location = _find_install_location(package.name)
            if location:
                ok, output = _run_winget_upgrade(package.id, timeout_sec, ["--location", location])
        return ok, output
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

    def __init__(self, packages: list[OutdatedPackage], parent=None):
        super().__init__(parent)
        self._packages = packages
        self.finished.connect(self.deleteLater)

    def run(self) -> None:
        for package in self._packages:
            self.package_started.emit(package.id)
            ok, output = update_package(package)
            self.package_finished.emit(package.id, ok, output)
        self.all_finished.emit()
