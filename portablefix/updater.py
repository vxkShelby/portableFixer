import json
import os
import re
import tempfile
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from PySide6.QtCore import QThread, Signal

from . import elevation
from .sha256sums import _sha256_unless_stopped
from .version import APP_VERSION

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
    STAGE_DIR_NAME,
    LaunchResult,
    StagedUpdate,
    UpdateStageCancelled,
    UpdateStageError,
    _remove_tree,
    launch_swap,
    stage_update,
    update_log_dir,
    update_status_path,
)

GITHUB_API_LATEST_RELEASE = "https://api.github.com/repos/vxkShelby/portableFixer/releases/latest"
# Shown with every update failure: the way out when the in-app update
# cannot work on this machine.
RELEASES_PAGE_URL = "https://github.com/vxkShelby/portableFixer/releases/latest"
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


class UpdateDownloadCancelled(Exception):
    """The app is closing - the partial download was deleted."""


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
    should_stop: Callable[[], bool] | None = None,
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
                # Checked per chunk: closing the app must not have to wait
                # for (or destroy the thread of) a 55 MB download.
                if should_stop is not None and should_stop():
                    raise UpdateDownloadCancelled()
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
    _verify_zip_sha256(zip_path, expected, should_stop)
    return zip_path


def _verify_zip_sha256(zip_path: Path, expected: str, should_stop) -> None:
    try:
        actual = _sha256_unless_stopped(zip_path, should_stop)
    except OSError:
        zip_path.unlink(missing_ok=True)
        raise
    if actual is None:
        zip_path.unlink(missing_ok=True)
        raise UpdateDownloadCancelled()
    if actual.lower() != expected:
        zip_path.unlink(missing_ok=True)
        raise UpdateVerificationError("Downloaded package does not match expected SHA256.")


_SHA256_HEX = re.compile(r"[0-9a-fA-F]{64}")


def copy_local_update(
    source_zip: Path,
    expected_sha256: str,
    dest_dir: Path,
    on_progress: Callable[[int, int], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> Path:
    """The developer switch's stand-in for download_update: copies a local
    release zip into dest_dir and verifies it the same way. A copy, because
    staging deletes the zip it was given."""
    if not _SHA256_HEX.fullmatch(expected_sha256 or ""):
        raise UpdateVerificationError("--sha256 must be the 64-digit hex SHA256 of the zip - refusing to install.")
    source_zip = Path(source_zip)
    dest_dir.mkdir(parents=True, exist_ok=True)
    zip_path = dest_dir / "PortableFix-update.zip"
    try:
        total = source_zip.stat().st_size
        copied = 0
        with source_zip.open("rb") as src, zip_path.open("wb") as dst:
            while True:
                if should_stop is not None and should_stop():
                    raise UpdateDownloadCancelled()
                chunk = src.read(_DOWNLOAD_CHUNK_SIZE)
                if not chunk:
                    break
                dst.write(chunk)
                copied += len(chunk)
                if on_progress is not None:
                    on_progress(copied, total)
    except BaseException:
        zip_path.unlink(missing_ok=True)
        raise
    _verify_zip_sha256(zip_path, expected_sha256.lower(), should_stop)
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


_DAY_SEC = 24 * 3600
_LOG_KEEP_SEC = 14 * _DAY_SEC
# Everything launch_swap and the swap script write into update_log_dir().
_UPDATE_LOG_PATTERNS = ("launch_*.txt", "popen_launch_*.log", "update_log_*.txt", "swap_*.ps1", "swap_*.json", "swap_*.marker")


def _age_sec(path: Path, now: float) -> float:
    try:
        return now - path.stat().st_mtime
    except OSError:
        return 0.0


def _stage_version(stage_dir: Path) -> str | None:
    try:
        return (stage_dir / "version.txt").read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def cleanup_update_leftovers(
    install_dir: Path,
    *,
    temp_dir: Path | None = None,
    log_dir: Path | None = None,
    current_version: str | None = None,
    now: float | None = None,
) -> list[Path]:
    """Removes what earlier update attempts left behind: download folders,
    a stage that can no longer be installed, old launch/update logs and the
    generated scripts of versions up to 1.11. Called at startup only, after
    main() made sure no swap is running. Best effort; returns what it
    removed."""
    now = time.time() if now is None else now
    current_version = current_version or APP_VERSION
    if temp_dir is None:
        try:
            temp_dir = Path(tempfile.gettempdir())
        except OSError:
            temp_dir = None
    if log_dir is None:
        log_dir = update_log_dir()
    candidates: list[Path] = []
    if temp_dir is not None:
        # A failed or abandoned download; a live one belongs to this very
        # process and cannot exist yet at startup.
        candidates += [p for p in temp_dir.glob("PortableFixUpdate_*") if _age_sec(p, now) > _DAY_SEC]
        # The generated swap scripts of <= 1.11.x, never cleaned up by them.
        candidates += list(temp_dir.glob("portablefix_update_*.ps1"))
    stage_dir = Path(install_dir) / STAGE_DIR_NAME
    if stage_dir.exists():
        version = _stage_version(stage_dir)
        if _age_sec(stage_dir, now) > _DAY_SEC or version is None or not is_newer(version, current_version):
            candidates.append(stage_dir)
    if log_dir is not None:
        for pattern in _UPDATE_LOG_PATTERNS:
            candidates += [p for p in log_dir.glob(pattern) if _age_sec(p, now) > _LOG_KEEP_SEC]
    removed = []
    for path in candidates:
        try:
            _remove_tree(path)
        except OSError:
            continue
        removed.append(path)
    return removed


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
    """Downloads the release zip - or, for the developer switch, copies a
    local one (local_zip plus its SHA256) - and verifies it."""

    download_finished = Signal(object, str)
    progress = Signal(int, int)

    def __init__(
        self, info: UpdateInfo, dest_dir: Path, parent=None,
        local_zip: Path | None = None, local_sha256: str = "",
    ):
        super().__init__(parent)
        self._info = info
        self._dest_dir = dest_dir
        self._local_zip = local_zip
        self._local_sha256 = local_sha256
        self.finished.connect(self.deleteLater)

    def run(self) -> None:
        try:
            if self._local_zip is not None:
                path = copy_local_update(
                    self._local_zip, self._local_sha256, self._dest_dir,
                    on_progress=self.progress.emit, should_stop=self.isInterruptionRequested,
                )
            else:
                path = download_update(
                    self._info, self._dest_dir,
                    on_progress=self.progress.emit, should_stop=self.isInterruptionRequested,
                )
            self.download_finished.emit(path, "")
        except Exception as exc:
            self.download_finished.emit(None, str(exc) or type(exc).__name__)


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
