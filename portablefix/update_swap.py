"""Qt-free core of the in-app update: stage the downloaded package next to
the install, start the static swap script, and prove it is running before
the app quits.

Everything that can fail on a bad package (hashing, extracting, the layout,
free space) runs here while the app is still open to report it. What is left
for PowerShell after the app exits is same-volume renames and a few file
copies. Stdlib and ctypes only: the frozen end-to-end probe and the release
build import this module without PySide6.
"""
import ctypes
import json
import os
import secrets
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import zipfile
import zlib
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .paths import powershell_executable
from .sha256sums import _sha256_unless_stopped, parse_sha256sums
from .update_swap_script import SWAP_SCRIPT
from .version import APP_VERSION

# Same values as the subprocess constants, spelled out so the flags can be
# built (and tested) on any platform.
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_BREAKAWAY_FROM_JOB = 0x01000000
CREATE_NO_WINDOW = 0x08000000
# CREATE_NO_WINDOW, the flag every other PowerShell the app starts uses.
# Never DETACHED_PROCESS: with no console at all, powershell.exe's console
# host cannot start, and the process exits with code 0 and no output before
# it runs line 1 of the script - which is why the update never completed in
# any version up to 1.11.4 (tests/test_update_spawn_windows.py pins this).
SWAP_CREATIONFLAGS = CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP | CREATE_BREAKAWAY_FROM_JOB

ROUTE_DIRECT = "direct"
ROUTE_NO_BREAKAWAY = "direct-no-breakaway"

# The swap script (or the app itself, for 'handed_off') writes one of these
# words into Data/update_status.txt so the next launch - of whichever
# version ends up running - can tell the user what happened to an update
# that finished while no app was around to report it.
UPDATE_STATUS_OK = "ok"
UPDATE_STATUS_OK_SUMS_STALE = "ok_sums_stale"
UPDATE_STATUS_IN_PROGRESS = "in_progress"
UPDATE_STATUS_ROLLED_BACK = "rolled_back"
# The new folders failed verification and an old one could not be put back
# (a new folder locked in its place): old and new files are now mixed.
UPDATE_STATUS_ROLLBACK_FAILED = "rollback_failed"
UPDATE_STATUS_ABORTED = "aborted"
# Written by the app right after the handshake, before it quits. The script
# overwrites it once it starts working, so if it is still there at the next
# launch, the updater died (or was killed) after it proved it was running.
UPDATE_STATUS_HANDED_OFF = "handed_off"

UPDATE_MUTEX_NAME = "Global\\PortableFix_UpdateInProgress"
STAGE_DIR_NAME = "_update_stage"
SWAP_FOLDERS = ("App", "Modules", "Vendor")
# Only these are installed from the package's Data\ and root: settings.json
# and update_status.txt belong to the user's install, and older zips still
# carried the build machine's settings.json.
DATA_ALLOWLIST = ("SHA256SUMS", "PortableFix-SelfSigned.cer", ".gitkeep")
ROOT_ALLOWLIST = ("PortableFix.cmd", "portablefix.ico")
MANIFEST_EXE = "App/PortableFix.exe"

REASON_EXITED = "exited"
REASON_TIMEOUT = "timeout"
REASON_BLOCKED = "blocked_policy"
REASON_SPAWN_ERROR = "spawn_error"
REASON_CANCELLED = "cancelled"

HANDSHAKE_TIMEOUT_SEC = 45.0
_FREE_SPACE_FACTOR = 1.1
_FREE_SPACE_EXTRA = 20 * 1024 * 1024
_CHUNK = 1024 * 1024
_LOG_TAIL_BYTES = 2048

# PyInstaller's onefile bootloader passes these to the Python child; Qt's
# plugin variables point into the same _MEI folder that is deleted on exit.
_DROPPED_ENV_KEYS = frozenset({"_MEIPASS2", "QT_PLUGIN_PATH", "QML2_IMPORT_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH"})


class UpdateStageError(Exception):
    pass


class UpdateStageCancelled(UpdateStageError):
    pass


@dataclass
class StagedUpdate:
    stage_dir: Path
    stage_root: Path
    file_count: int
    byte_count: int
    version: str | None = None


@dataclass
class SwapJob:
    script_path: Path
    job_path: Path
    marker_path: Path
    log_file: Path
    launch_log: Path
    job: dict = field(repr=False, default_factory=dict)


@dataclass
class LaunchResult:
    ok: bool
    reason: str = ""
    detail: str = ""
    exit_code: int | None = None
    route: str = ""
    child_pid: int | None = None
    warn_job: bool = False
    diagnostics_path: Path | None = None
    log_dir: Path | None = None


def update_log_dir() -> Path | None:
    try:
        return Path(tempfile.gettempdir()) / "PortableFixUpdate"
    except OSError:
        # tempfile.gettempdir() raises FileNotFoundError when no candidate
        # temp directory is usable at all.
        return None


def update_status_path(install_dir: Path) -> Path:
    return install_dir / "Data" / "update_status.txt"


def mark_handed_off(install_dir: Path) -> bool:
    try:
        update_status_path(install_dir).write_text(UPDATE_STATUS_HANDED_OFF + "\n", encoding="ascii")
        return True
    except OSError:
        return False


# --- The spawn -------------------------------------------------------------


def _is_under(entry: str, root: str) -> bool:
    try:
        entry_n = os.path.normcase(os.path.normpath(os.path.abspath(entry)))
        root_n = os.path.normcase(os.path.normpath(os.path.abspath(root)))
    except (OSError, ValueError):
        return False
    return entry_n == root_n or entry_n.startswith(root_n.rstrip(os.sep) + os.sep)


def clean_child_env(environ=None, meipass: str | None = None) -> dict[str, str]:
    """The environment for the swap PowerShell and, through it, the relaunched
    app. A onefile exe that inherits _PYI_ARCHIVE_FILE/_PYI_APPLICATION_HOME_DIR
    believes it is a child of the dead app and loads Python from its deleted
    _MEI folder ("Failed to load Python DLL"). Keys are compared
    case-insensitively, as Windows does."""
    source = dict(os.environ if environ is None else environ)
    if meipass is None:
        meipass = getattr(sys, "_MEIPASS", None)
    env: dict[str, str] = {}
    for key, value in source.items():
        upper = key.upper()
        if upper.startswith("_PYI_") or upper in _DROPPED_ENV_KEYS or upper == "PYINSTALLER_RESET_ENVIRONMENT":
            continue
        env[key] = value
    path_key = next((k for k in env if k.upper() == "PATH"), None)
    if path_key is not None and meipass:
        kept = [e for e in env[path_key].split(os.pathsep) if e and not _is_under(e, meipass)]
        env[path_key] = os.pathsep.join(kept)
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    return env


_kernel32_cache = None


def _kernel32():
    """A private WinDLL instance, so setting argtypes/restype here cannot
    change how other modules' ctypes.windll.kernel32 calls behave."""
    global _kernel32_cache
    if _kernel32_cache is None:
        from ctypes import wintypes

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.OpenProcess.restype = wintypes.HANDLE
        k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k32.CloseHandle.argtypes = [wintypes.HANDLE]
        k32.GetCurrentProcess.restype = wintypes.HANDLE
        k32.QueryFullProcessImageNameW.argtypes = [
            wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD),
        ]
        k32.GetShortPathNameW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
        k32.GetShortPathNameW.restype = wintypes.DWORD
        k32.IsProcessInJob.argtypes = [wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL)]
        k32.QueryInformationJobObject.argtypes = [
            wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
        ]
        k32.CreateMutexW.restype = wintypes.HANDLE
        k32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
        k32.OpenMutexW.restype = wintypes.HANDLE
        k32.OpenMutexW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
        k32.WaitForSingleObject.restype = wintypes.DWORD
        k32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        _kernel32_cache = k32
    return _kernel32_cache


_SYNCHRONIZE = 0x00100000
_ERROR_ACCESS_DENIED = 5


def create_mutex(name: str) -> tuple[int, int]:
    """CreateMutexW -> (handle or 0, last error). The caller owns the handle:
    183 (ERROR_ALREADY_EXISTS) still returns one, which must be closed or the
    caller itself keeps the mutex alive."""
    k32 = _kernel32()
    handle = k32.CreateMutexW(None, False, name)
    return handle or 0, ctypes.get_last_error()


def close_handle(handle: int) -> None:
    if handle:
        _kernel32().CloseHandle(handle)


def update_mutex_present() -> bool:
    """True while a swap script holds UPDATE_MUTEX_NAME, i.e. an update is
    replacing this install right now."""
    if sys.platform != "win32":
        return False
    try:
        k32 = _kernel32()
        handle = k32.OpenMutexW(_SYNCHRONIZE, False, UPDATE_MUTEX_NAME)
        error = ctypes.get_last_error()
    except (OSError, AttributeError):
        return False
    if handle:
        k32.CloseHandle(handle)
        return True
    # A mutex created by an elevated updater can refuse a non-elevated
    # caller - it exists all the same.
    return error == _ERROR_ACCESS_DENIED


def wait_for_process_exit(pid: int, timeout_sec: float) -> bool:
    """Waits until pid has exited (True), or timeout_sec passed (False). A
    PID that cannot be opened counts as gone."""
    if sys.platform != "win32":
        return True
    try:
        k32 = _kernel32()
        handle = k32.OpenProcess(_SYNCHRONIZE, False, int(pid))
    except (OSError, AttributeError, ValueError):
        return True
    if not handle:
        return True
    try:
        return k32.WaitForSingleObject(handle, int(timeout_sec * 1000)) == 0
    finally:
        k32.CloseHandle(handle)


_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


def _process_image(pid: int) -> str | None:
    if sys.platform != "win32":
        return None
    from ctypes import wintypes

    k32 = _kernel32()
    handle = k32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return None
    try:
        size = wintypes.DWORD(32768)
        buf = ctypes.create_unicode_buffer(size.value)
        if not k32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return None
        return buf.value
    finally:
        k32.CloseHandle(handle)


def _same_file(a: str, b: str) -> bool:
    try:
        return os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))
    except (OSError, ValueError):
        return False


def onefile_parent_pid() -> int | None:
    """The PyInstaller onefile bootloader that started this Python process,
    when there is one. It outlives the Python child, still mapping
    App\\PortableFix.exe while it deletes the _MEI folder, so the swap must
    wait for it too. Only trusted when the parent really runs the same exe -
    a onedir build or a dev run has an unrelated parent (explorer, cmd)."""
    if not getattr(sys, "frozen", False):
        return None
    try:
        ppid = os.getppid()
    except (AttributeError, OSError):
        return None
    image = _process_image(ppid)
    if image and _same_file(image, sys.executable):
        return ppid
    return None


def _is_plain_ascii_path(text: str) -> bool:
    return text.isascii() and "[" not in text and "]" not in text


def short_path(path) -> str:
    """powershell.exe 5.1 resolves its -File argument itself, and a bracket
    (or, on some systems, a non-ASCII character) in that path can make it
    report the script as missing. The 8.3 alias avoids both - used only when
    it really is plain ASCII, since 8.3 names can be disabled per volume."""
    text = str(path)
    if sys.platform != "win32" or _is_plain_ascii_path(text):
        return text
    try:
        k32 = _kernel32()
        needed = k32.GetShortPathNameW(text, None, 0)
        if not needed:
            return text
        buf = ctypes.create_unicode_buffer(needed)
        if not k32.GetShortPathNameW(text, buf, needed):
            return text
    except (OSError, AttributeError):
        return text
    return buf.value if buf.value and _is_plain_ascii_path(buf.value) else text


def swap_argv(script: Path) -> list[str]:
    # Only the folder is shortened: the script derives its job file's name
    # from its own path ($PSCommandPath), and the file names are ASCII
    # already, so an 8.3 file name (SWAP_1~1.PS1) must never reach it.
    target = os.path.join(short_path(script.parent), script.name)
    # Absolute path when available: a corrupted PATH on a broken machine must
    # not be what stops the update from starting. -NonInteractive turns an
    # unexpected prompt into an error instead of a hidden script waiting
    # forever. No -WindowStyle: CREATE_NO_WINDOW already hides the console.
    return [powershell_executable(), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", target]


def _is_access_denied(exc: OSError) -> bool:
    return getattr(exc, "winerror", None) == 5 or isinstance(exc, PermissionError)


def spawn_swap(script: Path, launch_log: Path, cwd: Path, env: dict[str, str]):
    """Starts the swap PowerShell; returns (Popen, route). Its output goes to
    launch_log, so anything PowerShell prints before or instead of running
    the script (a policy refusal, a parse error) is kept."""
    argv = swap_argv(script)
    flags = SWAP_CREATIONFLAGS
    with open(launch_log, "wb") as out:
        kwargs = dict(
            stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT,
            cwd=str(cwd), env=env, close_fds=True,
        )
        try:
            return subprocess.Popen(argv, creationflags=flags, **kwargs), ROUTE_DIRECT
        except OSError as exc:
            # A parent job without JOB_OBJECT_LIMIT_BREAKAWAY_OK makes
            # CreateProcess fail with ERROR_ACCESS_DENIED for the breakaway
            # flag alone - without this retry such a machine could never
            # start the update at all.
            if not (flags & CREATE_BREAKAWAY_FROM_JOB) or not _is_access_denied(exc):
                raise
        return (
            subprocess.Popen(argv, creationflags=flags & ~CREATE_BREAKAWAY_FROM_JOB, **kwargs),
            ROUTE_NO_BREAKAWAY,
        )


class _BasicLimitInformation(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_int64),
        ("PerJobUserTimeLimit", ctypes.c_int64),
        ("LimitFlags", ctypes.c_uint32),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", ctypes.c_uint32),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", ctypes.c_uint32),
        ("SchedulingClass", ctypes.c_uint32),
    ]


class _ExtendedLimitInformation(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _BasicLimitInformation),
        ("IoInfo", ctypes.c_uint64 * 6),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
_JOB_OBJECT_LIMIT_BREAKAWAY_OK = 0x800
_JOB_OBJECT_LIMIT_SILENT_BREAKAWAY_OK = 0x1000
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000


def _in_job(k32, handle) -> bool:
    from ctypes import wintypes

    result = wintypes.BOOL(False)
    if not k32.IsProcessInJob(handle, None, ctypes.byref(result)):
        raise ctypes.WinError(ctypes.get_last_error())
    return bool(result.value)


def job_facts(child_pid: int | None = None) -> dict:
    """Whether the app and the updater sit in a Job Object, and whether the
    app's job kills its members when it closes - the one way left for the
    updater to die with the app after the handshake. Diagnostics only."""
    if sys.platform != "win32":
        return {}
    facts: dict = {}
    try:
        k32 = _kernel32()
        facts["app_in_job"] = _in_job(k32, k32.GetCurrentProcess())
        if child_pid:
            handle = k32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, child_pid)
            if handle:
                try:
                    facts["child_in_job"] = _in_job(k32, handle)
                finally:
                    k32.CloseHandle(handle)
        if facts["app_in_job"]:
            info = _ExtendedLimitInformation()
            if k32.QueryInformationJobObject(
                None, _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION, ctypes.byref(info), ctypes.sizeof(info), None
            ):
                flags = info.BasicLimitInformation.LimitFlags
                facts["job_limit_flags"] = f"0x{flags:08X}"
                facts["kill_on_close"] = bool(flags & _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE)
                facts["breakaway_ok"] = bool(
                    flags & (_JOB_OBJECT_LIMIT_BREAKAWAY_OK | _JOB_OBJECT_LIMIT_SILENT_BREAKAWAY_OK)
                )
            else:
                facts["job_query_error"] = ctypes.get_last_error()
    except (OSError, AttributeError) as exc:
        facts["error"] = str(exc)
    return facts


# --- Staging ---------------------------------------------------------------

# Names Windows maps to devices in any folder, with or without an extension.
_RESERVED_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"}
    | {f"{prefix}{n}" for prefix in ("COM", "LPT") for n in "123456789¹²³"}
)
_FORBIDDEN_CHARS = frozenset('<>:"|?*') | frozenset(chr(c) for c in range(32))


def _safe_parts(name: str) -> list[str]:
    """The path components of a zip entry or manifest line, or
    UpdateStageError. Compress-Archive on Windows PowerShell 5.1 writes
    backslash separators, which zipfile on Linux does not split on - hence
    the normalisation here instead of extractall()."""
    normalized = name.replace("\\", "/")
    if not normalized or normalized.startswith("/"):
        raise UpdateStageError(f"unsafe path in the update package: {name!r}")
    parts = normalized.split("/")
    if parts[-1] == "":
        parts.pop()
    if not parts:
        raise UpdateStageError(f"unsafe path in the update package: {name!r}")
    for part in parts:
        # ':' covers drive letters and NTFS alternate data streams alike; a
        # trailing dot or space is silently dropped by Windows, so "App." would
        # land on App.
        if (
            part in ("", ".", "..")
            or any(ch in _FORBIDDEN_CHARS for ch in part)
            or part[-1] in ". "
            or part.split(".")[0].rstrip(" ").upper() in _RESERVED_NAMES
        ):
            raise UpdateStageError(f"unsafe path in the update package: {name!r}")
    return parts


def _remove_tree(path: Path) -> None:
    def _retry_writable(func, target, _exc):
        # A read-only file (copied from read-only media) makes rmtree fail
        # on Windows; clearing the flag once is enough.
        os.chmod(target, stat.S_IWRITE)
        func(target)

    is_junction = getattr(path, "is_junction", lambda: False)
    if path.is_symlink() or is_junction():
        # Never recurse through a link someone planted in place of the stage.
        try:
            path.unlink()
        except OSError:
            os.rmdir(path)
        return
    if path.is_dir():
        if sys.version_info >= (3, 12):
            shutil.rmtree(path, onexc=_retry_writable)
        else:
            shutil.rmtree(path, onerror=_retry_writable)
    elif path.exists():
        path.unlink()


# RuntimeError covers encrypted entries and (NotImplementedError) unknown
# compression methods.
_UNPACK_ERRORS = (OSError, zipfile.BadZipFile, zipfile.LargeZipFile, zlib.error, EOFError, RuntimeError, ValueError)


def _discard(stage_dir: Path) -> None:
    try:
        _remove_tree(stage_dir)
    except OSError:
        pass


def stage_update(
    zip_path: Path,
    install_dir: Path,
    should_stop: Callable[[], bool] | None = None,
    progress: Callable[[int, int], None] | None = None,
    version: str | None = None,
) -> StagedUpdate:
    """Extracts and verifies the downloaded release zip into
    <install>\\_update_stage - the same volume as the install, so the swap is
    renames only (a cross-volume move onto a USB stick takes minutes, while
    no App\\ exists). Deletes the zip on success; leaves no partial stage on
    failure or interruption."""
    zip_path = Path(zip_path)
    install_dir = Path(install_dir)
    stage_dir = install_dir / STAGE_DIR_NAME
    try:
        _remove_tree(stage_dir)
    except OSError as exc:
        raise UpdateStageError(f"could not remove the previous staged update {stage_dir}: {exc}") from exc
    try:
        staged = _extract_and_verify(zip_path, install_dir, stage_dir, should_stop, progress)
        if version:
            (stage_dir / "version.txt").write_text(version, encoding="utf-8")
        staged.version = version
    except UpdateStageError:
        _discard(stage_dir)
        raise
    except _UNPACK_ERRORS as exc:
        _discard(stage_dir)
        raise UpdateStageError(f"could not unpack the update: {exc}") from exc
    except BaseException:
        _discard(stage_dir)
        raise
    try:
        zip_path.unlink()
    except OSError:
        pass
    return staged


def _extract_and_verify(zip_path, install_dir, stage_dir, should_stop, progress) -> StagedUpdate:
    with zipfile.ZipFile(zip_path) as zf:
        entries = []
        seen: set[str] = set()
        top = None
        total = 0
        for info in zf.infolist():
            parts = _safe_parts(info.filename)
            is_dir = info.filename.replace("\\", "/").endswith("/")
            if top is None:
                top = parts[0]
            elif parts[0] != top:
                raise UpdateStageError("the update package has more than one top-level folder")
            if is_dir:
                continue
            if len(parts) < 2:
                raise UpdateStageError(f"file outside the package folder: {info.filename!r}")
            key = "/".join(parts).casefold()
            if key in seen:
                # Windows would silently overwrite one with the other.
                raise UpdateStageError(f"duplicate entry in the update package: {info.filename!r}")
            seen.add(key)
            entries.append((info, parts))
            total += info.file_size
        if not entries:
            raise UpdateStageError("the update package is empty")

        needed = int(total * _FREE_SPACE_FACTOR) + _FREE_SPACE_EXTRA
        free = shutil.disk_usage(install_dir).free
        if free < needed:
            raise UpdateStageError(
                f"not enough free space next to the install: {needed // (1024 * 1024)} MB needed, "
                f"{free // (1024 * 1024)} MB free"
            )

        stage_dir.mkdir()
        done = 0
        for info, parts in entries:
            target = stage_dir.joinpath(*parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, open(target, "xb") as dst:
                while True:
                    if should_stop is not None and should_stop():
                        raise UpdateStageCancelled("staging the update was cancelled")
                    chunk = src.read(_CHUNK)
                    if not chunk:
                        break
                    dst.write(chunk)
                    done += len(chunk)
                    if progress is not None:
                        progress(done, total)

    root = stage_dir / top
    _verify_layout(root)
    _verify_manifest(root, should_stop)
    return StagedUpdate(stage_dir=stage_dir, stage_root=root, file_count=len(entries), byte_count=done)


def _verify_layout(root: Path) -> None:
    # The release zip contract (scripts/build_release_zip.ps1): one folder
    # with App\, Modules\, Vendor\, Data\ and PortableFix.cmd. Checked before
    # the app hands off, while nothing live has been touched.
    if not (root / "App" / "PortableFix.exe").is_file():
        raise UpdateStageError("the update package has no App\\PortableFix.exe")
    for name in ("Modules", "Vendor"):
        folder = root / name
        if not folder.is_dir() or not any(folder.iterdir()):
            raise UpdateStageError(f"the update package has no {name} folder, or it is empty")
    if not (root / "Data" / "SHA256SUMS").is_file():
        raise UpdateStageError("the update package has no Data\\SHA256SUMS")
    if not (root / "PortableFix.cmd").is_file():
        raise UpdateStageError("the update package has no PortableFix.cmd")


def _verify_manifest(root: Path, should_stop) -> None:
    try:
        manifest = parse_sha256sums(root / "Data" / "SHA256SUMS")
    except (OSError, UnicodeDecodeError) as exc:
        raise UpdateStageError(f"the update's SHA256SUMS is unreadable: {exc}") from exc
    if MANIFEST_EXE not in manifest:
        raise UpdateStageError("the update's SHA256SUMS does not cover App/PortableFix.exe")
    for rel_path, expected in manifest.items():
        path = root.joinpath(*_safe_parts(rel_path))
        if not path.is_file():
            raise UpdateStageError(f"{rel_path} is listed in SHA256SUMS but missing from the package")
        actual = _sha256_unless_stopped(path, should_stop)
        if actual is None:
            raise UpdateStageCancelled("staging the update was cancelled")
        if actual != expected:
            raise UpdateStageError(f"{rel_path} does not match SHA256SUMS - damaged or tampered package")


# --- The job and the handshake --------------------------------------------


def write_swap_job(
    staged: StagedUpdate,
    install_dir: Path,
    pids: list[int],
    log_dir: Path,
    *,
    max_wait_sec: int = 600,
    rename_tries: int = 60,
    rename_delay_ms: int = 500,
    poll_ms: int = 250,
    sums_tries: int = 5,
    sums_delay_ms: int = 1000,
) -> SwapJob:
    """Writes the static script and its JSON job into log_dir. The JSON is
    pure ASCII (non-ASCII path characters become \\u escapes), so how
    PowerShell 5.1 guesses a file's encoding cannot matter. Pairs are
    objects, not nested arrays, and every list is wrapped in @() by the
    script - ConvertFrom-Json on 5.1 mangles nested and one-element arrays."""
    if not pids:
        # The script refuses such a job anyway: with nothing to wait for it
        # would rename folders under the running app.
        raise ValueError("the swap needs at least one process to wait for")
    install_dir = Path(os.path.abspath(install_dir))
    log_dir = Path(os.path.abspath(log_dir))
    stage_root = Path(os.path.abspath(staged.stage_root))
    # Created here, not by the script: the script does no path arithmetic
    # and must be able to write its log and status from the first line.
    log_dir.mkdir(parents=True, exist_ok=True)
    (install_dir / "Data").mkdir(parents=True, exist_ok=True)
    pid = os.getpid()
    base = f"swap_{pid}_{secrets.token_hex(4)}"
    script_path = log_dir / f"{base}.ps1"
    job_path = log_dir / f"{base}.json"
    marker_path = log_dir / f"{base}.marker"
    marker_path.unlink(missing_ok=True)

    def copies(src_dir: Path, dst_dir: Path, names) -> list[dict]:
        return [
            {"Src": str(src_dir / name), "Dst": str(dst_dir / name)}
            for name in names if (src_dir / name).is_file()
        ]

    job = {
        "Schema": 1,
        "Pids": [int(p) for p in pids],
        "MaxWaitSec": int(max_wait_sec),
        "PollMs": int(poll_ms),
        "RenameTries": int(rename_tries),
        "RenameDelayMs": int(rename_delay_ms),
        "SumsTries": int(sums_tries),
        "SumsDelayMs": int(sums_delay_ms),
        "InstallDir": str(install_dir),
        # Order matters: App first, so a locked exe (the likeliest failure)
        # stops the swap before anything else was moved.
        "Folders": [
            {
                "Name": name,
                "Live": str(install_dir / name),
                "Backup": str(install_dir / f"{name}.old"),
                "Staged": str(stage_root / name),
                # Where a rollback parks the rejected new folder.
                "Discard": str(install_dir / f"{name}.failed"),
            }
            for name in SWAP_FOLDERS
        ],
        "StageDir": str(Path(os.path.abspath(staged.stage_dir))),
        "DataCopies": copies(stage_root / "Data", install_dir / "Data", DATA_ALLOWLIST),
        "RootCopies": copies(stage_root, install_dir, ROOT_ALLOWLIST),
        "SumsSrc": str(stage_root / "Data" / "SHA256SUMS"),
        "SumsDst": str(install_dir / "Data" / "SHA256SUMS"),
        "StatusFile": str(update_status_path(install_dir)),
        "LogFile": str(log_dir / f"update_log_{pid}.txt"),
        "MarkerFile": str(marker_path),
        "AppExe": str(install_dir / "App" / "PortableFix.exe"),
        "MutexName": UPDATE_MUTEX_NAME,
    }
    job_path.write_text(json.dumps(job, ensure_ascii=True, indent=1), encoding="ascii")
    # utf-8-sig like every script the app runs; the text is ASCII anyway.
    # Bytes, not write_text: text mode on Windows turned every \n into \r\n,
    # so the file differed from SWAP_SCRIPT depending on the platform.
    script_path.write_bytes(b"\xef\xbb\xbf" + SWAP_SCRIPT.encode("ascii"))
    return SwapJob(
        script_path=script_path, job_path=job_path, marker_path=marker_path,
        log_file=Path(job["LogFile"]), launch_log=log_dir / f"popen_launch_{pid}.log", job=job,
    )


def _read_marker(path: Path) -> str:
    try:
        text = path.read_bytes().decode("ascii", errors="replace")
    except OSError:
        return ""
    # Set-Content ends the line with a newline; without it the file is still
    # being written.
    return text.strip() if text.endswith("\n") else ""


def _log_tail(path: Path) -> str:
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - _LOG_TAIL_BYTES))
            data = f.read()
    except OSError:
        return ""
    for encoding in ("utf-8", "oem" if sys.platform == "win32" else "latin-1"):
        try:
            return data.decode(encoding).strip()
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("utf-8", errors="replace").strip()


def _exit_detail(exit_code: int, launch_log: Path) -> str:
    code = f"exit code 0x{exit_code & 0xFFFFFFFF:08X}"
    tail = _log_tail(launch_log)
    if tail:
        return f"{code}: {tail}"
    if exit_code == 2:
        return f"{code}: the updater could not read its job file"
    return f"{code}: no output (PowerShell closed before running the updater)"


def _stop(proc) -> int | None:
    try:
        proc.kill()
    except OSError:
        pass
    try:
        return proc.wait(timeout=5)
    except (subprocess.TimeoutExpired, OSError):
        return None


def _append_diagnostics(path: Path | None, lines: dict) -> None:
    if path is None:
        return
    text = f"=== update launch {datetime.now().isoformat(timespec='seconds')} ===\n"
    text += "".join(f"{key}: {value}\n" for key, value in lines.items())
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8", errors="replace") as f:
            f.write(text + "\n")
    except OSError:
        pass


def launch_swap(
    staged: StagedUpdate,
    install_dir: Path,
    *,
    pids: list[int] | None = None,
    log_dir: Path | None = None,
    timeout: float = HANDSHAKE_TIMEOUT_SEC,
    poll: float = 0.1,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    should_stop: Callable[[], bool] | None = None,
    job_options: dict | None = None,
) -> LaunchResult:
    """Starts the swap and waits until it proves it is running (the 'ready'
    marker, written after it pinned the app's processes). The app may quit
    only on ok: every other outcome keeps it open with the reason on screen.
    On ok, Data\\update_status.txt says 'handed_off' until the script
    overwrites it, so an updater that is killed later is still reported."""
    started = clock()
    own_pid = os.getpid()
    parent_pid = onefile_parent_pid()
    if pids is None:
        pids = [own_pid] + ([parent_pid] if parent_pid else [])
    if log_dir is None:
        log_dir = update_log_dir()
    diagnostics_path = log_dir / f"launch_{own_pid}.txt" if log_dir is not None else None
    diag: dict = {
        "app_version": APP_VERSION,
        "frozen": bool(getattr(sys, "frozen", False)),
        "executable": sys.executable,
        "meipass": getattr(sys, "_MEIPASS", None),
        "app_pid": own_pid,
        "parent_pid": os.getppid() if hasattr(os, "getppid") else None,
        "parent_image": _process_image(os.getppid()) if hasattr(os, "getppid") else None,
        "wait_pids": pids,
        "install_dir": install_dir,
        "stage_root": staged.stage_root,
    }

    def finish(result: LaunchResult, marker: str = "") -> LaunchResult:
        result.diagnostics_path = diagnostics_path
        result.log_dir = log_dir
        diag.update({
            "outcome": "ok" if result.ok else result.reason,
            "exit_code": None if result.exit_code is None else f"0x{result.exit_code & 0xFFFFFFFF:08X}",
            "marker": marker,
            "child_pid": result.child_pid,
            "detail": result.detail,
            "elapsed_ms": int((clock() - started) * 1000),
        })
        _append_diagnostics(diagnostics_path, diag)
        return result

    if log_dir is None:
        return finish(LaunchResult(ok=False, reason=REASON_SPAWN_ERROR, detail="no usable temp folder for the updater"))
    try:
        job = write_swap_job(staged, install_dir, pids, log_dir, **(job_options or {}))
    except OSError as exc:
        return finish(LaunchResult(ok=False, reason=REASON_SPAWN_ERROR, detail=f"could not write the updater files: {exc}"))

    source_env = dict(os.environ)
    env = clean_child_env(source_env)
    path_key = next((k for k in env if k.upper() == "PATH"), None)
    diag.update({
        "job": job.job_path,
        "argv": swap_argv(job.script_path),
        "cwd": log_dir,
        "flags": f"0x{SWAP_CREATIONFLAGS:08X}",
        "removed_env": sorted(k for k in source_env if k not in env),
        "path_trimmed": path_key is not None and env[path_key] != source_env.get(path_key),
    })
    try:
        proc, route = spawn_swap(job.script_path, job.launch_log, cwd=log_dir, env=env)
    except OSError as exc:
        winerror = getattr(exc, "winerror", None)
        detail = f"{exc}" + (f" (winerror {winerror})" if winerror is not None else "")
        return finish(LaunchResult(ok=False, reason=REASON_SPAWN_ERROR, detail=detail))
    diag["route"] = route
    result = LaunchResult(ok=False, route=route, child_pid=proc.pid)
    deadline = clock() + timeout
    while True:
        marker = _read_marker(job.marker_path)
        rc = proc.poll()
        if rc is not None and not marker:
            # The marker may have landed between the two reads above - or the
            # processes it waits for exited right after 'ready' and the whole
            # swap finished between two polls.
            marker = _read_marker(job.marker_path)
        if marker.startswith("ready"):
            result.ok = True
            facts = job_facts(proc.pid)
            diag["job_facts"] = facts
            # Kill-on-close only fires when the job's last handle closes, so
            # the updater usually survives the app anyway - worth a record,
            # not a refusal.
            result.warn_job = bool(facts.get("child_in_job") and facts.get("kill_on_close"))
            diag["handed_off_written"] = mark_handed_off(install_dir)
            return finish(result, marker)
        if marker.startswith(("blocked", "error")) or rc is not None:
            if rc is None:
                try:
                    rc = proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    rc = _stop(proc)
            result.exit_code = rc
            if marker.startswith("blocked"):
                result.reason = REASON_BLOCKED
                result.detail = marker
            else:
                result.reason = REASON_EXITED
                result.detail = _exit_detail(rc if rc is not None else 0, job.launch_log)
                if marker:
                    result.detail = f"{marker}; {result.detail}"
            return finish(result, marker)
        if should_stop is not None and should_stop():
            result.reason = REASON_CANCELLED
            result.exit_code = _stop(proc)
            return finish(result, marker)
        if clock() >= deadline:
            result.reason = REASON_TIMEOUT
            result.exit_code = _stop(proc)
            tail = _log_tail(job.launch_log)
            result.detail = f"no response from the updater within {timeout:.0f} s" + (f": {tail}" if tail else "")
            return finish(result, marker)
        sleep(poll)
