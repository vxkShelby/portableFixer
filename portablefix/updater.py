import json
import os
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from PySide6.QtCore import QThread, Signal

from . import elevation
from .sha256sums import compute_sha256

# The Qt-free core lives in update_swap; these names are re-exported because
# main.py, the GUI and the tests have always reached them through updater.
from .update_swap import (  # noqa: F401
    REASON_BLOCKED,
    REASON_CANCELLED,
    REASON_EXITED,
    REASON_SPAWN_ERROR,
    REASON_TIMEOUT,
    SWAP_FOLDERS,
    UPDATE_STATUS_ABORTED,
    UPDATE_STATUS_HANDED_OFF,
    UPDATE_STATUS_IN_PROGRESS,
    UPDATE_STATUS_OK,
    UPDATE_STATUS_OK_SUMS_STALE,
    UPDATE_STATUS_ROLLED_BACK,
    LaunchResult,
    StagedUpdate,
    UpdateStageCancelled,
    UpdateStageError,
    launch_swap,
    stage_update,
    update_log_dir,
    update_status_path,
)

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
        manifest = resp.read().decode("utf-8", errors="replace").split()
    # An empty or garbled manifest must fail as a verification error (which
    # the UI reports), not an IndexError, and never be compared as-is.
    expected = manifest[0].lower() if manifest else ""
    if len(expected) != 64 or any(c not in "0123456789abcdef" for c in expected):
        zip_path.unlink(missing_ok=True)
        raise UpdateVerificationError("SHA256 manifest is empty or malformed - refusing to install.")
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
    # fails for real on every rename. Rather than trying to out-clever
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


def recover_interrupted_swap(install_dir: Path) -> list[str]:
    """Puts back any App/Modules/Vendor folder that an interrupted update
    swap (USB stick pulled, power lost) left behind only as X.old. Runs at
    startup, before modules load. App.old itself can only be restored by
    PortableFix.cmd - without App\\ there is no exe to run this - so in
    practice this covers Modules/Vendor. Returns the restored folder names."""
    restored = []
    for name in SWAP_FOLDERS:
        live = install_dir / name
        backup = install_dir / f"{name}.old"
        try:
            if backup.is_dir() and not live.exists():
                backup.rename(live)
                restored.append(name)
        except OSError:
            continue
    return restored


def consume_update_status(install_dir: Path) -> str | None:
    """Reads and deletes the status the last swap script left behind, so each
    outcome is reported exactly once."""
    path = update_status_path(install_dir)
    try:
        status = path.read_text(encoding="utf-8-sig", errors="replace").strip()
    except OSError:
        return None
    try:
        path.unlink()
    except OSError:
        pass
    return status or None


def update_status_message_key(status: str | None, restored: list[str]) -> str | None:
    """i18n key describing the last update's outcome, or None when there's
    nothing to tell (no update ran, or it succeeded cleanly)."""
    if status == UPDATE_STATUS_IN_PROGRESS or restored:
        return "update_status_interrupted"
    if status in (UPDATE_STATUS_ABORTED, UPDATE_STATUS_ROLLED_BACK):
        return "update_status_failed"
    if status == UPDATE_STATUS_HANDED_OFF:
        # The app handed off and quit, but the swap never even started
        # working - it was killed or died after proving it was running.
        return "update_status_incomplete"
    if status == UPDATE_STATUS_OK_SUMS_STALE:
        return "update_status_sums_stale"
    return None


def apply_update(zip_path: Path, install_dir: Path) -> bool:
    """The whole hand-off in one blocking call: stage, start the swap, wait
    for its handshake. True only when the updater proved it is running - the
    caller quits the app on True. Kept for callers that have not moved to
    UpdateStageRunner/UpdateLaunchRunner, which do the same off the GUI
    thread and can show why it failed."""
    if not is_writable(install_dir):
        return False
    try:
        staged = stage_update(zip_path, install_dir)
    except UpdateStageError:
        return False
    return launch_swap(staged, install_dir).ok


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


class UpdateStageRunner(QThread):
    """Extracting and hashing ~130 MB on a slow USB stick takes a while -
    off the GUI thread, interruptible so closing the app never has to wait
    for it (or destroy it while it runs)."""

    stage_finished = Signal(object, str)
    progress = Signal(int, int)

    def __init__(self, zip_path: Path, install_dir: Path, version: str | None = None, parent=None):
        super().__init__(parent)
        self._zip_path = zip_path
        self._install_dir = install_dir
        self._version = version
        self.finished.connect(self.deleteLater)

    def _emit_progress(self, done: int, total: int) -> None:
        # The signal carries C ints; a package over 2 GB is reported in KiB.
        if total > 0x7FFFFFFF:
            done, total = done >> 10, total >> 10
        self.progress.emit(done, total)

    def run(self) -> None:
        try:
            staged = stage_update(
                self._zip_path, self._install_dir,
                should_stop=self.isInterruptionRequested, progress=self._emit_progress, version=self._version,
            )
        except Exception as exc:
            # Anything else escaping run() would end the thread without a
            # signal and leave the GUI waiting forever.
            self.stage_finished.emit(None, str(exc) or type(exc).__name__)
            return
        self.stage_finished.emit(staged, "")


class UpdateLaunchRunner(QThread):
    """Waits (up to update_swap.HANDSHAKE_TIMEOUT_SEC) for the swap script to
    prove it is running. Emits a LaunchResult; on ok the caller must quit
    right away - the script is waiting for this process to exit."""

    launch_finished = Signal(object)

    def __init__(self, staged: StagedUpdate, install_dir: Path, parent=None):
        super().__init__(parent)
        self._staged = staged
        self._install_dir = install_dir
        self.finished.connect(self.deleteLater)

    def run(self) -> None:
        try:
            result = launch_swap(self._staged, self._install_dir, should_stop=self.isInterruptionRequested)
        except Exception as exc:
            result = LaunchResult(ok=False, reason=REASON_SPAWN_ERROR, detail=f"{type(exc).__name__}: {exc}")
        self.launch_finished.emit(result)
