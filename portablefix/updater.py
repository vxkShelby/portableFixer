import json
import os
import subprocess
import tempfile
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from PySide6.QtCore import QThread, Signal

from . import elevation
from .integrity import compute_sha256

GITHUB_API_LATEST_RELEASE = "https://api.github.com/repos/vxkShelby/portableFixer/releases/latest"
_TRUSTED_DOWNLOAD_HOSTS = {"github.com", "objects.githubusercontent.com"}
# urlretrieve has no timeout at all - a stalled connection hangs the download
# thread forever. This bounds each individual socket read/connect instead.
DOWNLOAD_TIMEOUT_SEC = 30
_DOWNLOAD_CHUNK_SIZE = 65536


def _is_trusted_download_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme == "https" and parsed.hostname in _TRUSTED_DOWNLOAD_HOSTS


class UpdateVerificationError(Exception):
    pass


@dataclass
class UpdateInfo:
    version: str
    package_url: str
    sha256_url: str | None
    notes: str


def parse_version(v: str) -> tuple[int, ...]:
    v = v.lstrip("vV")
    parts = []
    for p in v.split("."):
        digits = ""
        for ch in p:
            if not ch.isdigit():
                break
            digits += ch
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


def is_newer(remote: str, local: str) -> bool:
    return parse_version(remote) > parse_version(local)


def check_for_update(current_version: str, timeout: float = 5.0) -> UpdateInfo | None:
    try:
        req = urllib.request.Request(
            GITHUB_API_LATEST_RELEASE,
            headers={"Accept": "application/vnd.github+json", "User-Agent": "PortableFix-Updater"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        tag = data.get("tag_name", "")
        if not tag or not is_newer(tag, current_version):
            return None
        assets = data.get("assets", [])
        zip_asset = next((a for a in assets if a.get("name", "").lower() == "portablefix-portable.zip"), None)
        if not zip_asset or not _is_trusted_download_url(zip_asset["browser_download_url"]):
            return None
        sha_asset = next(
            (a for a in assets if a.get("name", "").lower() == "portablefix-portable.zip.sha256"), None
        )
        sha256_url = sha_asset["browser_download_url"] if sha_asset else None
        if sha256_url and not _is_trusted_download_url(sha256_url):
            sha256_url = None
        return UpdateInfo(
            version=tag.lstrip("vV"),
            package_url=zip_asset["browser_download_url"],
            sha256_url=sha256_url,
            notes=data.get("body", ""),
        )
    except Exception:
        return None


def download_update(
    info: UpdateInfo,
    dest_dir: Path,
    on_progress: Callable[[int, int], None] | None = None,
) -> Path:
    # Fail closed: a release published without a .sha256 asset (CI mishap, or
    # a tampered release that simply omits it) must not be trusted silently -
    # an unverified zip is about to be swapped in as the running application.
    if not info.sha256_url:
        raise UpdateVerificationError("Release has no SHA256 manifest - refusing to install.")
    dest_dir.mkdir(parents=True, exist_ok=True)
    zip_path = dest_dir / "PortableFix-update.zip"
    try:
        with urllib.request.urlopen(info.package_url, timeout=DOWNLOAD_TIMEOUT_SEC) as resp, zip_path.open("wb") as f:
            try:
                total = int(resp.getheader("Content-Length") or 0)
            except (TypeError, ValueError):
                # A missing/malformed header means unknown size, not fatal -
                # the caller shows an indeterminate progress bar instead.
                total = 0
            downloaded = 0
            while True:
                chunk = resp.read(_DOWNLOAD_CHUNK_SIZE)
                if not chunk:
                    break
                f.write(chunk)
                downloaded += len(chunk)
                if on_progress is not None:
                    on_progress(downloaded, total)
    except Exception:
        zip_path.unlink(missing_ok=True)
        raise
    with urllib.request.urlopen(info.sha256_url, timeout=10) as resp:
        expected = resp.read().decode("utf-8").strip().split()[0].lower()
    actual = compute_sha256(zip_path)
    if actual.lower() != expected:
        zip_path.unlink(missing_ok=True)
        raise UpdateVerificationError("Downloaded package does not match expected SHA256.")
    return zip_path


# Deliberately just the classic UAC-virtualized legacy-app roots (this is
# what actually fools a naive write-probe), not every conceivable protected
# path. A custom folder an admin locked down by hand isn't virtualized, so
# is_writable()'s own write-then-read-back probe already fails on it
# correctly without needing to be listed here - this list only needs to
# cover the paths where that probe can be silently fooled.
_PROTECTED_ROOT_ENV_VARS = ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432", "ProgramData", "WinDir")


def needs_elevation_for_update(directory: Path) -> bool:
    # A non-admin process's writes under Program Files/Windows are often
    # silently redirected by UAC file virtualization to
    # %LOCALAPPDATA%\VirtualStore\... instead of actually failing - a naive
    # write-then-read-back probe in is_writable() would "succeed" against
    # that shadow copy while the real target stays untouched, and the swap
    # script (running as powershell.exe, which is NOT virtualized) then
    # fails for real on every Move-Item, silently, thanks to
    # $ErrorActionPreference. Rather than trying to out-clever
    # virtualization, refuse outright when installed under a protected
    # system path and not elevated.
    if elevation.is_admin():
        return False
    try:
        resolved = str(directory.resolve()).casefold()
    except OSError:
        resolved = str(directory).casefold()
    for env_var in _PROTECTED_ROOT_ENV_VARS:
        root = os.environ.get(env_var)
        if not root:
            continue
        root = root.rstrip("\\").casefold()
        if resolved == root or resolved.startswith(root + "\\"):
            return True
    return False


def is_writable(directory: Path) -> bool:
    if needs_elevation_for_update(directory):
        return False
    probe = directory / ".update_write_test"
    try:
        if not directory.exists():
            return False
        probe.write_text("x", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return True
    except OSError:
        return False


def _ps_quote(value: str) -> str:
    # Single-quoted PowerShell strings never interpolate $variables, unlike
    # the double-quoted strings this previously used - a literal '$' in a
    # path (a legal NTFS character, e.g. a username) would otherwise be
    # misread as a variable reference and silently truncate the path.
    return "'" + value.replace("'", "''") + "'"


def build_swap_script(current_pid: int, install_dir: Path, zip_path: Path) -> str:
    # The downloaded zip's contract (produced by scripts/build_release_zip.ps1):
    # exactly one top-level folder containing App/, Data/, Modules/, Vendor/,
    # PortableFix.cmd.
    app_dir = _ps_quote(str(install_dir / "App"))
    app_bak = _ps_quote(str(install_dir / "App.old"))
    app_exe = _ps_quote(str(install_dir / "App" / "PortableFix.exe"))
    modules_dir = _ps_quote(str(install_dir / "Modules"))
    modules_bak = _ps_quote(str(install_dir / "Modules.old"))
    vendor_dir = _ps_quote(str(install_dir / "Vendor"))
    vendor_bak = _ps_quote(str(install_dir / "Vendor.old"))
    data_dir = _ps_quote(str(install_dir / "Data"))
    settings_json = _ps_quote(str(install_dir / "Data" / "settings.json"))
    cmd_path = _ps_quote(str(install_dir / "PortableFix.cmd"))
    zip_p = _ps_quote(str(zip_path))
    stage = _ps_quote(str(zip_path.parent / "PortableFixUpdateStage"))
    settings_bak = _ps_quote(str(zip_path.parent / "settings.json.bak"))
    log_dir = _ps_quote(str(Path(tempfile.gettempdir()) / "PortableFixUpdate"))
    log_file = _ps_quote(str(Path(tempfile.gettempdir()) / "PortableFixUpdate" / f"update_log_{current_pid}.txt"))
    return (
        '$ErrorActionPreference = "SilentlyContinue"\n'
        # No diagnostics existed here before - every step below is silent by
        # design ($ErrorActionPreference), so this log is the only evidence
        # left behind if a swap fails. Written under %TEMP%, never under
        # install_dir, so it's captured even when the install itself is on
        # an unwritable/protected path.
        f"New-Item -ItemType Directory -Force -Path {log_dir} | Out-Null\n"
        f"function Log([string]$msg) {{ Add-Content -Path {log_file} -Value ((Get-Date -Format o) + ' ' + $msg) -EA SilentlyContinue }}\n"
        f"Log 'update swap started, waiting for pid {current_pid} to exit'\n"
        f"for ($i = 0; $i -lt 30; $i++) {{\n"
        f"    if (-not (Get-Process -Id {current_pid} -EA SilentlyContinue)) {{ break }}\n"
        "    Start-Sleep -Milliseconds 500\n"
        "}\n"
        "Start-Sleep -Milliseconds 300\n"
        # If the old process is still alive after the wait, its exe (and
        # anything it has open under App\) is still locked - proceeding
        # would make Move-Item fail silently and leave a half-swapped
        # install. Nothing has been touched on disk yet, so aborting here
        # is a clean no-op, not a rollback.
        f"if (Get-Process -Id {current_pid} -EA SilentlyContinue) {{\n"
        f"    Log 'ABORT: pid {current_pid} did not exit in time, files likely still locked'\n"
        "    exit 1\n"
        "}\n"
        f"Log 'old process exited, proceeding with swap'\n"
        f"if (Test-Path {settings_json}) {{ Copy-Item -Path {settings_json} -Destination {settings_bak} -Force }}\n"
        f"Log \"settings.json backed up: $(Test-Path {settings_bak})\"\n"
        f"Expand-Archive -Path {zip_p} -DestinationPath {stage} -Force\n"
        f"Log \"expanded update zip: $(Test-Path {stage})\"\n"
        # Zip-slip guard: refuse to proceed if any extracted entry landed
        # outside the staging directory (a crafted zip with '../' entries).
        f"$stageFull = (Resolve-Path {stage}).Path\n"
        f"$escaped = Get-ChildItem -Path {stage} -Recurse -File | Where-Object {{ -not $_.FullName.StartsWith($stageFull) }}\n"
        f"if ($escaped) {{ Log 'ABORT: zip-slip guard tripped'; Remove-Item -Path {stage} -Recurse -Force -EA SilentlyContinue; exit 1 }}\n"
        f"$stagedRoot = (Get-ChildItem -Path {stage} -Directory | Select-Object -First 1).FullName\n"
        f"if (Test-Path {app_dir}) {{ Move-Item -Path {app_dir} -Destination {app_bak} -Force }}\n"
        f"if (Test-Path {modules_dir}) {{ Move-Item -Path {modules_dir} -Destination {modules_bak} -Force }}\n"
        f"if (Test-Path {vendor_dir}) {{ Move-Item -Path {vendor_dir} -Destination {vendor_bak} -Force }}\n"
        f"Log \"old folders backed up: App.old=$(Test-Path {app_bak}) Modules.old=$(Test-Path {modules_bak}) Vendor.old=$(Test-Path {vendor_bak})\"\n"
        # Root-cause fix: if App/Modules/Vendor is STILL present here, the
        # move-away above silently failed (a locked .exe/DLL - AV scanning,
        # or the process that just exited not having released the handle
        # yet - swallowed by $ErrorActionPreference). Move-Item into an
        # *existing* destination directory does not overwrite it, it nests
        # the source folder one level deeper instead (verified empirically),
        # so the old exe would stay exactly where it is. The verification
        # check further down would then find that old exe still there and
        # report "swap verified OK" - a false positive on an update that
        # never actually happened. Refuse to swap into an occupied
        # directory rather than nesting into it.
        f"$backupOk = (-not (Test-Path {app_dir})) -and (-not (Test-Path {modules_dir})) -and (-not (Test-Path {vendor_dir}))\n"
        f"if (-not $backupOk) {{ Log 'ABORT: old App/Modules/Vendor folder still present after backup move - likely locked, refusing to swap into an occupied directory' }}\n"
        "if ($backupOk) {\n"
        f"    Move-Item -Path \"$stagedRoot\\App\" -Destination {app_dir} -Force\n"
        f"    Move-Item -Path \"$stagedRoot\\Modules\" -Destination {modules_dir} -Force\n"
        f"    if (Test-Path \"$stagedRoot\\Vendor\") {{ Move-Item -Path \"$stagedRoot\\Vendor\" -Destination {vendor_dir} -Force }}\n"
        f"    Copy-Item -Path \"$stagedRoot\\Data\\*\" -Destination {data_dir} -Recurse -Force\n"
        f"    Copy-Item -Path \"$stagedRoot\\PortableFix.cmd\" -Destination {cmd_path} -Force\n"
        f"    if (Test-Path {settings_bak}) {{ Copy-Item -Path {settings_bak} -Destination {settings_json} -Force }}\n"
        f"    Log \"new files in place: App.exe=$(Test-Path {app_exe}) Modules=$(Test-Path {modules_dir}) Vendor=$(Test-Path {vendor_dir})\"\n"
        f"    if ((Test-Path {app_exe}) -and (Test-Path {modules_dir}) -and (Get-ChildItem -Path {modules_dir} -EA SilentlyContinue) -and (Test-Path {vendor_dir}) -and (Get-ChildItem -Path {vendor_dir} -EA SilentlyContinue)) {{\n"
        "        Log 'swap verified OK, removing backups'\n"
        f"        Remove-Item -Path {app_bak} -Recurse -Force -EA SilentlyContinue\n"
        f"        Remove-Item -Path {modules_bak} -Recurse -Force -EA SilentlyContinue\n"
        f"        Remove-Item -Path {vendor_bak} -Recurse -Force -EA SilentlyContinue\n"
        "    } else {\n"
        "        Log 'swap FAILED verification, rolling back to backups'\n"
        f"        Remove-Item -Path {app_dir} -Recurse -Force -EA SilentlyContinue\n"
        f"        Remove-Item -Path {modules_dir} -Recurse -Force -EA SilentlyContinue\n"
        f"        Remove-Item -Path {vendor_dir} -Recurse -Force -EA SilentlyContinue\n"
        f"        if (Test-Path {app_bak}) {{ Move-Item -Path {app_bak} -Destination {app_dir} -Force }}\n"
        f"        if (Test-Path {modules_bak}) {{ Move-Item -Path {modules_bak} -Destination {modules_dir} -Force }}\n"
        f"        if (Test-Path {vendor_bak}) {{ Move-Item -Path {vendor_bak} -Destination {vendor_dir} -Force }}\n"
        f"        Log \"rollback done, App.exe present=$(Test-Path {app_exe})\"\n"
        "    }\n"
        "}\n"
        # Relaunch first, then clean up temp files - a freshly-downloaded
        # zip can sit under active AV scanning for many seconds, and that
        # must never delay the user seeing their updated app come back.
        f"Log \"relaunching via {cmd_path}\"\n"
        f"Start-Process -FilePath {cmd_path}\n"
        f"Remove-Item -Path {stage} -Recurse -Force -EA SilentlyContinue\n"
        "for ($i = 0; $i -lt 30; $i++) {\n"
        f"    if (-not (Test-Path {zip_p})) {{ break }}\n"
        f"    Remove-Item -Path {zip_p} -Force -EA SilentlyContinue\n"
        "    Start-Sleep -Milliseconds 1000\n"
        "}\n"
        f"Remove-Item -Path {settings_bak} -Force -EA SilentlyContinue\n"
        "Log 'update swap script finished'\n"
    )


def apply_update(zip_path: Path, install_dir: Path) -> bool:
    if not is_writable(install_dir):
        return False
    current_pid = os.getpid()
    script_text = build_swap_script(current_pid, install_dir, zip_path)
    fd, script_path_str = tempfile.mkstemp(prefix=f"portablefix_update_{current_pid}_", suffix=".ps1")
    script_path = Path(script_path_str)
    try:
        os.close(fd)
        script_path.write_text(script_text, encoding="utf-8-sig")
        subprocess.Popen(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden", "-File", str(script_path)],
            creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
            close_fds=True,
        )
        return True
    except OSError:
        return False


class UpdateCheckRunner(QThread):
    check_finished = Signal(object)

    def __init__(self, current_version: str, parent=None):
        super().__init__(parent)
        self._current_version = current_version
        self.finished.connect(self.deleteLater)

    def run(self) -> None:
        info = check_for_update(self._current_version)
        self.check_finished.emit(info)


class UpdateDownloadRunner(QThread):
    download_finished = Signal(object, str)
    progress = Signal(int, int)

    def __init__(self, info: UpdateInfo, dest_dir: Path, parent=None):
        super().__init__(parent)
        self._info = info
        self._dest_dir = dest_dir
        self.finished.connect(self.deleteLater)

    def run(self) -> None:
        try:
            path = download_update(self._info, self._dest_dir, on_progress=self.progress.emit)
            self.download_finished.emit(path, "")
        except Exception as exc:
            self.download_finished.emit(None, str(exc))
