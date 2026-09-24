import glob
import itertools
import json
import os
import re
import shutil
import subprocess
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QThread, Signal

_SCAN_TIMEOUT_SEC = 60
# The PowerShell module is only a first try - it gets a slice of the scan
# budget so a hung module still leaves the CLI fallback time to run, and the
# whole scan stays inside the 65 s closeEvent waits for it.
_MODULE_SCAN_TIMEOUT_SEC = 25
_UPDATE_TIMEOUT_SEC = 300

# winget reports failures as HRESULTs in its exit code (docs: winget-cli
# doc/windows/package-manager/winget/returnCodes.md). Only the ones that
# change what the technician should do next are named here - every other
# non-zero code is still a failed scan, shown with its hex value.
_NO_APPLICATIONS_FOUND = 0x8A150014
_UPDATE_NOT_APPLICABLE = 0x8A15002B
_INVALID_CL_ARGUMENTS = 0x8A150002
_SOURCE_ERROR_CODES = {
    0x8A15000B,  # SOURCES_INVALID
    0x8A15000F,  # SOURCE_DATA_MISSING
    0x8A15003F,  # SOURCE_DATA_INTEGRITY_FAILURE
    0x8A150045,  # SOURCE_OPEN_FAILED
    0x8A150046,  # SOURCE_AGREEMENTS_NOT_ACCEPTED
    0x8A15004B,  # FAILED_TO_OPEN_ALL_SOURCES
}
# NTSTATUS codes of a winget.exe that cannot even start: App Installer's
# VCLibs/UI.Xaml dependencies missing or broken (typical of LTSC, Server and
# images with the Store stripped out).
_BROKEN_INSTALL_CODES = {
    0xC0000135,  # STATUS_DLL_NOT_FOUND
    0xC0000142,  # STATUS_DLL_INIT_FAILED
    0xC0000139,  # STATUS_ENTRYPOINT_NOT_FOUND
}

_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
_SEPARATOR_RE = re.compile(r"^-{10,}$")
# Filler that marks a wide (two-column) character's second column, so string
# indexes line up with the console columns winget padded the table to.
_WIDE_FILLER = "\x00"
# Five columns whose labels are at most a few words each; the column split
# tries every combination of header words, so the count is capped.
_MAX_HEADER_WORDS = 16


@dataclass
class OutdatedPackage:
    name: str
    id: str
    installed_version: str
    available_version: str
    source: str


def format_exit_code(code: int) -> str:
    # Python hands a Windows exit code over unsigned, but a caller (or a
    # test) may pass the signed form winget's docs also list - both mean
    # the same HRESULT.
    return f"0x{code & 0xFFFFFFFF:08X}"


class WingetScanError(Exception):
    """The scan could not tell whether updates exist.

    kind is "unavailable" (winget missing or unable to start - nothing to
    retry until App Installer is fixed) or "error" (winget ran and failed).
    reason narrows it for the panel's hint; packages holds any rows winget
    still listed before failing, so they are not hidden by the error.
    """

    def __init__(self, kind: str, reason: str, exit_code: int | None = None, detail: str = "",
                 packages: list[OutdatedPackage] | None = None):
        super().__init__(f"{kind}/{reason}" + (f" {format_exit_code(exit_code)}" if exit_code is not None else ""))
        self.kind = kind
        self.reason = reason
        self.exit_code = exit_code
        self.detail = detail
        self.packages = list(packages or [])

    @property
    def exit_code_hex(self) -> str:
        return format_exit_code(self.exit_code) if self.exit_code is not None else ""


def _to_columns(line: str) -> str:
    # winget pads its table in console columns, and an East Asian wide
    # character takes two of them - a filler after each one keeps every
    # string index equal to its console column.
    if line.isascii():
        return line
    out = []
    for ch in line:
        out.append(ch)
        if unicodedata.east_asian_width(ch) in ("W", "F"):
            out.append(_WIDE_FILLER)
    return "".join(out)


def _cell(line: str, start: int, end: int | None) -> str:
    return line[start:end].replace(_WIDE_FILLER, "").strip()


def _has_space(text: str) -> bool:
    return any(ch.isspace() for ch in text)


def _is_version_cell(cell: str) -> bool:
    # A range such as "< 3.0.1" is the only version with a space in it.
    if cell[:1] in ("<", ">"):
        cell = cell[1:].lstrip()
    return bool(cell) and not _has_space(cell)


def _is_id_cell(cell: str) -> bool:
    # Publisher.Package for the winget source, a 12-14 character code for
    # msstore - never a space, always at least one letter. This is what
    # tells a real Id column apart from a word of a localized header.
    return bool(cell) and not _has_space(cell) and any(ch.isalpha() for ch in cell)


def _row_cells(line: str, starts: tuple[int, ...]) -> list[str] | None:
    # Every column is preceded by at least one padding space on a real row;
    # a summary line under the table ("3 upgrades available.", in whatever
    # language) runs text straight across those positions.
    for start in starts:
        if len(line) > start and line[start - 1] != " ":
            return None
    bounds = (0,) + starts
    cells = [_cell(line, bounds[i], bounds[i + 1] if i + 1 < len(bounds) else None) for i in range(len(bounds))]
    name, pkg_id, version, available = cells[:4]
    source = cells[4] if len(cells) > 4 else ""
    if not name or not _is_id_cell(pkg_id) or not _is_version_cell(version) or not _is_version_cell(available):
        return None
    if _has_space(source):
        return None
    return cells


def _header_word_starts(header: str) -> list[int]:
    return [
        i for i in range(1, len(header))
        if header[i] not in (" ", _WIDE_FILLER) and header[i - 1] == " "
    ]


def _parse_upgrade_output(text: str) -> tuple[bool, list[OutdatedPackage]]:
    """Parse `winget upgrade` output without reading any of its labels.

    Returns (table_found, packages). The header labels, the summary line and
    the "no updates" message are all localized (German "ID ... Verfügbar",
    Slovak "Názov ... K dispozícii"), so the table is located by its dashed
    separator line instead, and the column order - Name, Id, Version,
    Available[, Source] - which winget keeps in every language. Which header
    words start real columns (a label like "K dispozícii" has a space in it)
    is decided by the data rows: the split under which the most rows have a
    valid Id and version cells wins.
    """
    lines = [_to_columns(_ANSI_RE.sub("", line).rstrip()) for line in text.lstrip("\ufeff").splitlines()]
    sep_idx = None
    for i, line in enumerate(lines):
        # The spinner winget draws while it searches ends in "\r", which
        # splitlines() already turned into its own junk lines - a lone "-"
        # frame is far too short to pass for the separator.
        if _SEPARATOR_RE.match(line.strip()) and any(lines[j].strip() for j in range(i)):
            sep_idx = i
            break
    if sep_idx is None:
        return False, []
    header = next(lines[j] for j in range(sep_idx - 1, -1, -1) if lines[j].strip())

    rows: list[str] = []
    for line in lines[sep_idx + 1:]:
        if not line.strip():
            break
        if _SEPARATOR_RE.match(line.strip()):
            # A second table follows (packages that need explicit
            # targeting); the line before its dashes is its header.
            if rows:
                rows.pop()
            break
        rows.append(line)

    word_starts = _header_word_starts(header)
    if len(word_starts) > _MAX_HEADER_WORDS:
        # Not a winget table header (some prose above a dashed line) - an
        # unreadable table, never "no updates".
        return True, []
    best: tuple[tuple[int, float, int], list[list[str]]] | None = None
    for column_count in (3, 4):
        for starts in itertools.combinations(word_starts, column_count):
            parsed = [cells for cells in (_row_cells(row, starts) for row in rows) if cells is not None]
            if not parsed:
                continue
            # Ties (only possible with one- or two-character values) go to
            # the split whose header gaps are widest - winget pads real
            # columns, a space inside a label is a single one - then to the
            # one that keeps the Source column.
            gap = sum(start - len(header[:start].rstrip()) for start in starts) / column_count
            key = (len(parsed), gap, column_count)
            if best is None or key > best[0]:
                best = (key, parsed)
    if best is None:
        return True, []
    packages = [
        OutdatedPackage(
            name=cells[0], id=cells[1], installed_version=cells[2], available_version=cells[3],
            source=cells[4] if len(cells) > 4 else "",
        )
        for cells in best[1]
    ]
    return True, packages


def parse_winget_upgrade_table(text: str) -> list[OutdatedPackage]:
    return _parse_upgrade_output(text)[1]


def export_package_list(packages: list[OutdatedPackage], path: Path) -> None:
    data = [{"id": p.id, "name": p.name} for p in packages]
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def import_package_ids(path: Path) -> set[str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return {item["id"] for item in data if isinstance(item, dict) and "id" in item}


def _app_installer_version_key(path: str) -> tuple[int, ...]:
    # ...\WindowsApps\Microsoft.DesktopAppInstaller_1.25.340.0_x64__8wekyb3d8bbwe\winget.exe
    folder = os.path.basename(os.path.dirname(path))
    parts = folder.split("_")
    if len(parts) < 2:
        return ()
    return tuple(int(p) for p in parts[1].split(".") if p.isdigit())


def find_winget_executable() -> str | None:
    found = shutil.which("winget")
    if found:
        return found
    # An elevated session, a fresh profile or a service account often has
    # no WindowsApps folder on PATH although App Installer is installed
    # (UniGetUI fixed the same miss in 2026.3.0). The per-user alias is a
    # reparse point that os.path.exists() may not follow - lexists does.
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        alias = os.path.join(local_app_data, "Microsoft", "WindowsApps", "winget.exe")
        if os.path.lexists(alias):
            return alias
    # The package folder itself is readable only as an administrator; glob
    # simply finds nothing otherwise.
    program_files = os.environ.get("ProgramFiles")
    if program_files:
        pattern = os.path.join(
            program_files, "WindowsApps", "Microsoft.DesktopAppInstaller_*__8wekyb3d8bbwe", "winget.exe",
        )
        matches = glob.glob(pattern)
        if matches:
            return max(matches, key=_app_installer_version_key)
    return None


# Microsoft.WinGet.Client returns typed objects - nothing in them is
# localized, so where the module is installed the text table is not needed
# at all. Exit 3 = module not installed, 4 = it failed: both fall back to
# the CLI. Output is forced to UTF-8 so program names keep their diacritics.
_MODULE_SCAN_SCRIPT = (
    "$ErrorActionPreference = 'Stop'; "
    "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; "
    "if (-not (Get-Module -ListAvailable -Name Microsoft.WinGet.Client)) { exit 3 }; "
    "try { Import-Module Microsoft.WinGet.Client; "
    "$rows = @(Get-WinGetPackage | Where-Object { $_.IsUpdateAvailable } | ForEach-Object { "
    "[pscustomobject]@{ Name = [string]$_.Name; Id = [string]$_.Id; "
    "InstalledVersion = [string]$_.InstalledVersion; Available = [string]@($_.AvailableVersions)[0]; "
    "Source = [string]$_.Source } }); "
    "ConvertTo-Json -InputObject $rows -Compress } catch { exit 4 }"
)


def _scan_with_powershell_module(timeout_sec: float) -> list[OutdatedPackage] | None:
    from .paths import powershell_executable

    try:
        result = subprocess.run(
            [powershell_executable(), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
             "-Command", _MODULE_SCAN_SCRIPT],
            capture_output=True, encoding="utf-8", errors="replace", timeout=timeout_sec,
            stdin=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    try:
        data = json.loads((result.stdout or "").strip() or "[]")
    except ValueError:
        return None
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        return None
    packages = []
    for item in data:
        if not isinstance(item, dict) or not item.get("Id"):
            continue
        packages.append(
            OutdatedPackage(
                name=str(item.get("Name") or item["Id"]), id=str(item["Id"]),
                installed_version=str(item.get("InstalledVersion") or ""),
                available_version=str(item.get("Available") or ""), source=str(item.get("Source") or ""),
            )
        )
    return packages


def _output_tail(text: str, max_lines: int = 4) -> str:
    # Shown to the technician as-is (never parsed): the last lines are where
    # winget puts its own explanation of the failure.
    lines = [_ANSI_RE.sub("", line).strip() for line in text.splitlines()]
    lines = [line for line in lines if len(line) > 1]
    return "\n".join(lines[-max_lines:])[:400]


def _scan_with_cli(winget_exe: str, timeout_sec: float) -> list[OutdatedPackage]:
    try:
        # winget writes UTF-8 even when the console code page is 1250/852;
        # decoding it as the ANSI code page mangled every accented header or
        # program name and shifted all the columns after it.
        # No stdin: a prompt winget still shows (a new source's terms) must
        # fail at once instead of waiting out the timeout for an answer.
        result = subprocess.run(
            [winget_exe, "upgrade", "--include-unknown", "--accept-source-agreements"],
            capture_output=True, encoding="utf-8", errors="replace", timeout=timeout_sec,
            stdin=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except subprocess.TimeoutExpired:
        raise WingetScanError("error", "timeout") from None
    except OSError as exc:
        # The alias exists but App Installer is not registered for this
        # account (ERROR_CANT_ACCESS_FILE and friends).
        raise WingetScanError("unavailable", "cannot_start", detail=str(exc)) from None
    stdout = result.stdout or ""
    code = result.returncode & 0xFFFFFFFF
    table_found, packages = _parse_upgrade_output(stdout)
    detail = _output_tail(stdout + "\n" + (result.stderr or ""))
    if code in (0, _NO_APPLICATIONS_FOUND, _UPDATE_NOT_APPLICABLE):
        if table_found and not packages:
            # A table was printed but no row made sense - saying "no
            # updates" here would be exactly the false all-clear this
            # module exists to avoid.
            raise WingetScanError("error", "unparsed", detail=detail)
        return packages
    if code in _BROKEN_INSTALL_CODES:
        raise WingetScanError("unavailable", "cannot_start", exit_code=code, detail=detail)
    if code in _SOURCE_ERROR_CODES:
        reason = "sources"
    elif code == _INVALID_CL_ARGUMENTS:
        # --include-unknown / --accept-source-agreements unknown: an App
        # Installer from before 2021.
        reason = "outdated"
    else:
        reason = "failed"
    raise WingetScanError("error", reason, exit_code=code, detail=detail, packages=packages)


def list_outdated_packages() -> list[OutdatedPackage]:
    """Outdated packages, or WingetScanError when the answer is unknown.

    Never returns an empty list for a scan that did not really run - an
    empty list means winget itself said there is nothing to update.
    """
    deadline = time.monotonic() + _SCAN_TIMEOUT_SEC
    winget_exe = find_winget_executable()
    if winget_exe is None:
        raise WingetScanError("unavailable", "not_found")
    packages = _scan_with_powershell_module(_MODULE_SCAN_TIMEOUT_SEC)
    if packages is not None:
        return packages
    return _scan_with_cli(winget_exe, max(10.0, deadline - time.monotonic()))


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
    # Same lookup as the scan: a winget the scan found outside PATH must
    # not then fail to update with "file not found".
    args = [
        find_winget_executable() or "winget", "upgrade", "--id", package_id, "--silent", "--include-unknown",
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
    # WingetScanError - winget missing, broken, or the scan failed.
    scan_failed = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.finished.connect(self.deleteLater)

    def run(self) -> None:
        try:
            packages = list_outdated_packages()
        except WingetScanError as exc:
            self.scan_failed.emit(exc)
            return
        except Exception as exc:  # an escaped crash left the panel on "scanning..." forever
            self.scan_failed.emit(WingetScanError("error", "failed", detail=f"{type(exc).__name__}: {exc}"))
            return
        self.scan_finished.emit(packages)


class WingetUpdateRunner(QThread):
    # Each winget upgrade is a blocking subprocess call - runs off the GUI
    # thread so the window stays responsive, mirroring uninstaller.py.
    package_started = Signal(str)
    package_finished = Signal(str, bool, str)
    all_finished = Signal()

    def __init__(self, packages: list[OutdatedPackage], parent=None):
        super().__init__(parent)
        self._packages = packages
        self._stop_requested = False
        self.finished.connect(self.deleteLater)

    def request_stop(self) -> None:
        # The in-flight package's subprocess call can't be interrupted (and
        # now has its own taskkill-on-timeout bound), but this stops the
        # loop from starting any further package - the app can shut down
        # after at most one more package's worst-case duration instead of
        # the whole remaining batch's.
        self._stop_requested = True

    def run(self) -> None:
        for package in self._packages:
            if self._stop_requested:
                break
            self.package_started.emit(package.id)
            ok, output = update_package(package)
            self.package_finished.emit(package.id, ok, output)
        self.all_finished.emit()
