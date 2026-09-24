"""Cheap before/after system measurements for a batch.

take_snapshot() runs on the GUI thread at batch start and end, so every
probe here is in-process (no PowerShell spawn) and bounded: folder walks stop
at a time/entry budget and record a lower bound instead of blocking, and the
one shell call that can't be interrupted (the Recycle Bin query) runs on a
helper thread that is simply abandoned after a short timeout.

Every metric degrades to None (unknown) off Windows or on any error - a
snapshot must never raise. Keys already written by older versions
(free_gb, total_gb) keep their meaning so old reports still compare.

compare_snapshots() turns two snapshots into display rows shared by the
batch summary dialog and the HTML report.
"""

import ctypes
import os
import shutil
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone

# Per-folder walk budget. Two temp folders + the Recycle Bin timeout keep the
# worst case around 250 ms; a normal (cleanish) machine finishes in a few ms.
WALK_TIME_BUDGET_SEC = 0.09
WALK_ENTRY_BUDGET = 40_000
RECYCLE_BIN_TIMEOUT_SEC = 0.06

_MB = 1024 * 1024
_GB = 1024 ** 3


@dataclass
class WalkResult:
    size_bytes: int
    files: int
    complete: bool


def walk_folder_size(root, *, time_budget: float = WALK_TIME_BUDGET_SEC,
                     entry_budget: int = WALK_ENTRY_BUDGET, clock=time.perf_counter,
                     scandir=os.scandir) -> WalkResult | None:
    """Total size of regular files under root, without following symlinks
    or junctions. Stops early when either budget runs out and returns what
    it counted so far with complete=False (a lower bound). None when root
    itself can't be listed (missing, access denied)."""
    deadline = clock() + time_budget
    total = files = entries = 0
    stack = [os.fspath(root)]
    first = True
    while stack:
        path = stack.pop()
        try:
            it = scandir(path)
        except OSError:
            if first:
                return None
            continue  # an unreadable subfolder (in use / ACL) - skip it
        first = False
        with it:
            for entry in it:
                entries += 1
                if entries > entry_budget or (entries % 256 == 0 and clock() > deadline):
                    return WalkResult(total, files, False)
                try:
                    if entry.is_symlink() or _is_junction(entry):
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        stack.append(entry.path)
                    elif entry.is_file(follow_symlinks=False):
                        # On Windows DirEntry.stat() comes from the directory
                        # listing itself - no extra syscall per file.
                        total += entry.stat(follow_symlinks=False).st_size
                        files += 1
                except OSError:
                    continue  # file vanished mid-walk - temp folders churn
        if clock() > deadline and stack:
            return WalkResult(total, files, False)
    return WalkResult(total, files, True)


def _is_junction(entry) -> bool:
    is_junction = getattr(entry, "is_junction", None)  # DirEntry.is_junction: 3.12+
    return bool(is_junction()) if is_junction is not None else False


class _MemoryStatusEx(ctypes.Structure):
    _fields_ = [
        ("dwLength", ctypes.c_uint32),
        ("dwMemoryLoad", ctypes.c_uint32),
        ("ullTotalPhys", ctypes.c_uint64),
        ("ullAvailPhys", ctypes.c_uint64),
        ("ullTotalPageFile", ctypes.c_uint64),
        ("ullAvailPageFile", ctypes.c_uint64),
        ("ullTotalVirtual", ctypes.c_uint64),
        ("ullAvailVirtual", ctypes.c_uint64),
        ("ullAvailExtendedVirtual", ctypes.c_uint64),
    ]


def _windows_memory() -> tuple[int, int] | None:
    """(available, total) physical memory in bytes via GlobalMemoryStatusEx."""
    status = _MemoryStatusEx()
    status.dwLength = ctypes.sizeof(_MemoryStatusEx)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
        return None
    return int(status.ullAvailPhys), int(status.ullTotalPhys)


class _ShQueryRbInfo(ctypes.Structure):
    # shellapi.h packs this struct to 1 byte only on 32-bit Windows.
    if ctypes.sizeof(ctypes.c_void_p) == 4:
        _pack_ = 1
    _fields_ = [
        ("cbSize", ctypes.c_uint32),
        ("i64Size", ctypes.c_int64),
        ("i64NumItems", ctypes.c_int64),
    ]


def _windows_recycle_bin() -> tuple[int, int] | None:
    """(bytes, items) in the Recycle Bin of all drives - the same scope
    Clear-RecycleBin empties. Runs on a helper thread (see _with_timeout),
    hence the COM init: shell APIs expect an initialized apartment."""
    ole32 = ctypes.windll.ole32
    initialized = ole32.CoInitializeEx(None, 0x2) in (0, 1)  # S_OK / S_FALSE
    try:
        info = _ShQueryRbInfo()
        info.cbSize = ctypes.sizeof(_ShQueryRbInfo)
        if ctypes.windll.shell32.SHQueryRecycleBinW(None, ctypes.byref(info)) != 0:
            return None
        return int(info.i64Size), int(info.i64NumItems)
    finally:
        if initialized:
            ole32.CoUninitialize()


def _with_timeout(func, timeout: float):
    """func() on a daemon thread; None if it errors or doesn't finish in
    time (the thread is left to finish on its own - it only reads)."""
    result: list = []

    def run():
        try:
            result.append(func())
        except Exception:
            result.append(None)

    worker = threading.Thread(target=run, name="pf-snapshot-probe", daemon=True)
    worker.start()
    worker.join(timeout)
    return result[0] if result else None


_RUN_KEYS = (
    # (hive, Run key, StartupApproved value list that can disable its entries)
    ("HKCU", r"Software\Microsoft\Windows\CurrentVersion\Run",
     r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run"),
    ("HKLM", r"SOFTWARE\Microsoft\Windows\CurrentVersion\Run",
     r"SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run"),
    ("HKLM", r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Run",
     r"SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run32"),
)


def _winreg_values(hive: str, subkey: str) -> dict | None:
    """All values of a key as {name: data}; None when the key doesn't exist."""
    import winreg

    root = winreg.HKEY_CURRENT_USER if hive == "HKCU" else winreg.HKEY_LOCAL_MACHINE
    try:
        key = winreg.OpenKey(root, subkey, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY)
    except FileNotFoundError:
        return None
    with key:
        count = winreg.QueryInfoKey(key)[1]
        values = {}
        for index in range(count):
            name, data, _ = winreg.EnumValue(key, index)
            values[name] = data
        return values


def count_startup_entries(read_values=_winreg_values) -> int | None:
    """Enabled entries in the HKCU/HKLM (64 + 32-bit) Run keys. An entry the
    technician disabled in Task Manager stays in Run but is marked disabled
    (odd first byte) under Explorer\\StartupApproved - it isn't counted,
    so disabling a startup app shows up as an improvement. None if no Run
    key could be read at all."""
    total = 0
    readable = 0
    for hive, run_key, approved_key in _RUN_KEYS:
        try:
            values = read_values(hive, run_key)
        except OSError:
            continue
        readable += 1
        if not values:
            continue
        try:
            approved = read_values(hive, approved_key) or {}
        except OSError:
            approved = {}
        for name in values:
            if not name:
                continue  # the key's unnamed "(Default)" value isn't an entry
            flag = approved.get(name)
            if isinstance(flag, (bytes, bytearray)) and flag and flag[0] & 1:
                continue
            total += 1
    return total if readable else None


def _default_temp_dirs(env) -> tuple[str | None, str | None]:
    user = env.get("TEMP") or env.get("TMP")
    windir = env.get("SystemRoot") or env.get("windir")
    windows = os.path.join(windir, "Temp") if windir else None
    if user and windows and os.path.normcase(os.path.abspath(user)) == os.path.normcase(os.path.abspath(windows)):
        user = None  # running as SYSTEM - %TEMP% *is* Windows\Temp, count it once
    return user, windows


def _walk_metrics(prefix: str, path, walk) -> dict:
    result = None
    if path:
        try:
            result = walk(path)
        except Exception:
            result = None
    if result is None:
        return {f"{prefix}_mb": None, f"{prefix}_files": None, f"{prefix}_complete": None}
    return {
        f"{prefix}_mb": round(result.size_bytes / _MB, 1),
        f"{prefix}_files": result.files,
        f"{prefix}_complete": result.complete,
    }


def _safe(func):
    try:
        return func()
    except Exception:
        return None


def take_snapshot(*, env=None, platform: str | None = None, disk_usage=None,
                  temp_dirs: tuple | None = None, walk=None, memory=None,
                  recycle_bin=None, startup_entries=None, clock=time.perf_counter) -> dict:
    """Measure the system; never raises. Every dependency is injectable for
    tests. Off Windows only free/total space is measured (as before); the
    Windows-only probes report None unless a fake is passed in."""
    started = clock()
    env = os.environ if env is None else env
    on_windows = (platform or sys.platform) == "win32"
    disk_usage = disk_usage or shutil.disk_usage
    snap: dict = {"taken_at": datetime.now(timezone.utc).isoformat()}

    system_drive = env.get("SystemDrive", "C:") + "\\"
    usage = _safe(lambda: disk_usage(system_drive))
    snap["free_gb"] = round(usage.free / _GB, 2) if usage is not None else None
    snap["total_gb"] = round(usage.total / _GB, 2) if usage is not None else None

    if temp_dirs is None:
        temp_dirs = _default_temp_dirs(env) if on_windows else (None, None)
    walk = walk or walk_folder_size
    user_temp, windows_temp = temp_dirs
    snap.update(_walk_metrics("temp_user", user_temp, walk))
    snap.update(_walk_metrics("temp_windows", windows_temp, walk))

    if recycle_bin is None and on_windows:
        recycle_bin = lambda: _with_timeout(_windows_recycle_bin, RECYCLE_BIN_TIMEOUT_SEC)  # noqa: E731
    rb = _safe(recycle_bin) if recycle_bin else None
    snap["recycle_bin_mb"] = round(rb[0] / _MB, 1) if rb else None
    snap["recycle_bin_items"] = int(rb[1]) if rb else None

    if startup_entries is None and on_windows:
        startup_entries = count_startup_entries
    snap["startup_entries"] = _safe(startup_entries) if startup_entries else None

    if memory is None and on_windows:
        memory = _windows_memory
    mem = _safe(memory) if memory else None
    snap["mem_available_mb"] = round(mem[0] / _MB) if mem else None
    snap["mem_total_mb"] = round(mem[1] / _MB) if mem else None

    snap["snapshot_ms"] = round((clock() - started) * 1000, 1)
    return snap


# --- comparison / display ---------------------------------------------------

@dataclass(frozen=True)
class Metric:
    key: str            # snapshot key holding the number
    label_key: str      # i18n key
    unit: str           # "gb", "mb" (shown as MB/GB) or "count"
    better: str         # "up" or "down" - which direction is an improvement
    complete_key: str | None = None  # False there = value is only a lower bound


METRICS = (
    Metric("free_gb", "snapshot_free_space", "gb", "up"),
    Metric("temp_user_mb", "snapshot_temp_user", "mb", "down", "temp_user_complete"),
    Metric("temp_windows_mb", "snapshot_temp_windows", "mb", "down", "temp_windows_complete"),
    Metric("recycle_bin_mb", "snapshot_recycle_bin", "mb", "down"),
    Metric("startup_entries", "snapshot_startup", "count", "down"),
    Metric("mem_available_mb", "snapshot_memory", "mb", "up"),
)


def _number(value):
    # bool is an int subclass - a stray True must not read as 1.
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value


def _trim(value: float) -> str:
    return f"{value:.2f}".rstrip("0").rstrip(".") if value != int(value) else str(int(value))


def format_value(value: float, unit: str, signed: bool = False) -> str:
    sign = ""
    if signed:
        sign = "+" if value > 0 else ("−" if value < 0 else "")
        value = abs(value)
    if unit == "gb":
        return f"{sign}{_trim(round(value, 2))} GB"
    if unit == "mb":
        if abs(value) >= 1024:
            return f"{sign}{_trim(round(value / 1024, 2))} GB"
        return f"{sign}{_trim(round(value, 1))} MB"
    return f"{sign}{_trim(round(value, 2))}"


def compare_snapshots(before, after) -> list[dict]:
    """Rows for every metric known (numeric) on both sides, in METRICS order.
    Tolerates old/partial/malformed snapshots (missing keys, non-dicts).

    Each row: key, label_key, before, after (display text, "≥ " prefix for a
    lower bound), delta (display text or None when not determinable),
    delta_value (float or None), trend ("good" / "bad" / "same" / None).
    """
    before = before if isinstance(before, dict) else {}
    after = after if isinstance(after, dict) else {}
    rows = []
    for m in METRICS:
        b, a = _number(before.get(m.key)), _number(after.get(m.key))
        if b is None or a is None:
            continue
        b_bound = m.complete_key is not None and before.get(m.complete_key) is False
        a_bound = m.complete_key is not None and after.get(m.complete_key) is False
        diff = round(a - b, 2)
        delta_value = delta = None
        prefix = ""
        if not b_bound and not a_bound:
            delta_value = diff
        elif b_bound and not a_bound and diff < 0:
            # Real "before" was at least b, so the real change is at most diff.
            delta_value, prefix = diff, "≤ "
        elif a_bound and not b_bound and diff > 0:
            delta_value, prefix = diff, "≥ "
        trend = None
        if delta_value is not None:
            delta = prefix + format_value(delta_value, m.unit, signed=True)
            if delta_value == 0:
                trend = "same"
            else:
                trend = "good" if (delta_value > 0) == (m.better == "up") else "bad"
        rows.append({
            "key": m.key,
            "label_key": m.label_key,
            "before": ("≥ " if b_bound else "") + format_value(b, m.unit),
            "after": ("≥ " if a_bound else "") + format_value(a, m.unit),
            "delta": delta,
            "delta_value": delta_value,
            "trend": trend,
            "lower_bound": b_bound or a_bound,
        })
    return rows
