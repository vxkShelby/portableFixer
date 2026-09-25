import json
import os
from collections import namedtuple

import pytest

from portablefix import snapshot
from portablefix.snapshot import (
    WalkResult,
    compare_snapshots,
    count_startup_entries,
    format_value,
    take_snapshot,
    walk_folder_size,
)

Usage = namedtuple("Usage", "total used free")
_GB = 1024 ** 3
_MB = 1024 * 1024


def _fill(root, files):
    for rel, size in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x" * size)


class _FakeClock:
    """Advances by `step` on every read - models a slow disk."""

    def __init__(self, step):
        self.now = 0.0
        self.step = step

    def __call__(self):
        self.now += self.step
        return self.now


# --- walk_folder_size ---------------------------------------------------------

def test_walk_counts_nested_files(tmp_path):
    _fill(tmp_path, {"a.tmp": 100, "sub/b.tmp": 200, "sub/deeper/c.tmp": 300})

    result = walk_folder_size(tmp_path)

    assert result == WalkResult(600, 3, True)


def test_walk_missing_root_is_unknown(tmp_path):
    assert walk_folder_size(tmp_path / "nope") is None


def test_walk_entry_budget_returns_lower_bound(tmp_path):
    _fill(tmp_path, {f"f{i}.tmp": 10 for i in range(50)})

    result = walk_folder_size(tmp_path, entry_budget=20)

    assert result.complete is False
    assert result.files == 20
    assert result.size_bytes == 200


def test_walk_time_budget_stops_between_folders(tmp_path):
    _fill(tmp_path, {f"d{i}/f.tmp": 10 for i in range(30)})

    # Every clock read costs 1 s against a 2.5 s budget: the walk must give up
    # after a couple of folders instead of finishing all 30.
    result = walk_folder_size(tmp_path, time_budget=2.5, clock=_FakeClock(1.0))

    assert result.complete is False
    assert result.files < 30


def test_walk_time_budget_checked_inside_one_huge_folder(tmp_path):
    _fill(tmp_path, {f"f{i}.tmp": 1 for i in range(1000)})

    result = walk_folder_size(tmp_path, time_budget=0.5, clock=_FakeClock(1.0))

    assert result.complete is False
    assert result.files < 1000


def test_walk_real_time_budget_is_respected(tmp_path):
    import time

    _fill(tmp_path, {f"d{i // 100}/f{i}.tmp": 1 for i in range(3000)})
    started = time.perf_counter()
    result = walk_folder_size(tmp_path, time_budget=0.0)
    elapsed = time.perf_counter() - started

    assert result.complete is False
    assert elapsed < 0.5


def test_walk_skips_unreadable_subfolder(tmp_path):
    _fill(tmp_path, {"ok/a.tmp": 5, "locked/b.tmp": 7})
    real_scandir = os.scandir

    def scandir(path):
        if str(path).endswith("locked"):
            raise PermissionError(path)
        return real_scandir(path)

    result = walk_folder_size(tmp_path, scandir=scandir)

    assert result == WalkResult(5, 1, True)


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="no symlinks")
def test_walk_does_not_follow_symlinks(tmp_path):
    outside = tmp_path / "outside"
    _fill(outside, {"big.bin": 1000})
    root = tmp_path / "temp"
    _fill(root, {"a.tmp": 1})
    try:
        (root / "link").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks not permitted")

    assert walk_folder_size(root) == WalkResult(1, 1, True)


# --- startup entries ----------------------------------------------------------

def _registry(data):
    def read_values(hive, subkey):
        value = data.get((hive, subkey.rsplit("\\", 1)[-1] if "StartupApproved" in subkey else subkey))
        if isinstance(value, Exception):
            raise value
        return value
    return read_values


_HKCU_RUN = r"Software\Microsoft\Windows\CurrentVersion\Run"
_HKLM_RUN = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Run"
_HKLM_RUN32 = r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Run"


def test_startup_entries_counts_all_run_keys_and_skips_disabled():
    reader = _registry({
        ("HKCU", _HKCU_RUN): {"OneDrive": "x", "Spotify": "y", "": "default-value"},
        ("HKCU", "Run"): {"Spotify": b"\x03" + b"\x00" * 11},  # disabled in Task Manager
        ("HKLM", _HKLM_RUN): {"SecurityHealth": "z"},
        ("HKLM", "Run"): {"SecurityHealth": b"\x02" + b"\x00" * 11},  # explicitly enabled
        ("HKLM", _HKLM_RUN32): {"Adobe": "a", "Java": "b"},
        ("HKLM", "Run32"): None,
    })

    assert count_startup_entries(reader) == 4  # OneDrive, SecurityHealth, Adobe, Java


def test_startup_entries_missing_keys_count_as_zero():
    assert count_startup_entries(_registry({})) == 0


def test_startup_entries_unknown_when_no_key_readable():
    assert count_startup_entries(_registry({
        ("HKCU", _HKCU_RUN): OSError("denied"),
        ("HKLM", _HKLM_RUN): OSError("denied"),
        ("HKLM", _HKLM_RUN32): OSError("denied"),
    })) is None


def test_startup_entries_unreadable_approved_list_counts_everything():
    reader = _registry({
        ("HKCU", _HKCU_RUN): {"A": "x"},
        ("HKCU", "Run"): OSError("denied"),
    })
    assert count_startup_entries(reader) == 1


# --- take_snapshot ------------------------------------------------------------

def test_take_snapshot_off_windows_only_measures_free_space():
    snap = take_snapshot(
        env={"SystemDrive": "C:"}, platform="linux",
        disk_usage=lambda path: Usage(200 * _GB, 50 * _GB, 150 * _GB),
    )

    assert snap["free_gb"] == 150.0
    assert snap["total_gb"] == 200.0
    for key in ("temp_user_mb", "temp_user_complete", "temp_windows_mb", "recycle_bin_mb",
                "startup_entries", "mem_available_mb", "mem_total_mb"):
        assert snap[key] is None, key
    json.dumps(snap)  # goes straight into the report JSON


def test_take_snapshot_off_windows_never_touches_windows_apis(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("Windows-only probe called off Windows")

    monkeypatch.setattr(snapshot, "_windows_memory", boom)
    monkeypatch.setattr(snapshot, "_windows_recycle_bin", boom)
    monkeypatch.setattr(snapshot, "count_startup_entries", boom)
    monkeypatch.setattr(snapshot, "walk_folder_size", boom)

    snap = take_snapshot(env={}, platform="linux", disk_usage=lambda p: Usage(1, 0, 1))

    assert snap["startup_entries"] is None


def test_take_snapshot_uses_system_drive_root():
    seen = []
    take_snapshot(env={"SystemDrive": "D:"}, platform="linux",
                  disk_usage=lambda p: seen.append(p) or Usage(1, 0, 1))
    assert seen == ["D:\\"]


def test_take_snapshot_every_probe_failing_degrades_to_none():
    def boom(*a, **k):
        raise RuntimeError("probe failed")

    snap = take_snapshot(
        env={}, platform="win32", disk_usage=boom, temp_dirs=("a", "b"), walk=boom,
        memory=boom, recycle_bin=boom, startup_entries=boom,
    )

    assert snap["free_gb"] is None and snap["total_gb"] is None
    assert snap["temp_user_mb"] is None and snap["temp_windows_mb"] is None
    assert snap["recycle_bin_mb"] is None
    assert snap["startup_entries"] is None
    assert snap["mem_available_mb"] is None


def test_take_snapshot_with_fake_windows_probes(tmp_path):
    user_temp = tmp_path / "user"
    win_temp = tmp_path / "win"
    _fill(user_temp, {"a.tmp": 3 * _MB, "x/b.tmp": _MB})
    _fill(win_temp, {"c.log": _MB // 2})

    snap = take_snapshot(
        env={"SystemDrive": "C:"}, platform="win32",
        disk_usage=lambda p: Usage(100 * _GB, 0, 40 * _GB),
        temp_dirs=(str(user_temp), str(win_temp)),
        memory=lambda: (6 * 1024 * _MB, 16 * 1024 * _MB),
        recycle_bin=lambda: (250 * _MB, 12),
        startup_entries=lambda: 7,
    )

    assert snap["temp_user_mb"] == 4.0
    assert snap["temp_user_files"] == 2
    assert snap["temp_user_complete"] is True
    assert snap["temp_windows_mb"] == 0.5
    assert snap["recycle_bin_mb"] == 250.0
    assert snap["recycle_bin_items"] == 12
    assert snap["startup_entries"] == 7
    assert snap["mem_available_mb"] == 6144
    assert snap["mem_total_mb"] == 16384
    assert isinstance(snap["snapshot_ms"], float)


def test_take_snapshot_budget_exhaustion_recorded_as_lower_bound(tmp_path):
    temp = tmp_path / "t"
    _fill(temp, {f"f{i}.tmp": _MB for i in range(10)})

    snap = take_snapshot(
        env={}, platform="win32", disk_usage=lambda p: Usage(1, 0, 1),
        temp_dirs=(str(temp), None),
        walk=lambda path: walk_folder_size(path, entry_budget=4),
        memory=lambda: None, recycle_bin=lambda: None, startup_entries=lambda: None,
    )

    assert snap["temp_user_mb"] == 4.0
    assert snap["temp_user_complete"] is False
    assert snap["temp_windows_mb"] is None


def test_default_temp_dirs_dedupes_system_account():
    env = {"TEMP": r"C:\Windows\Temp", "SystemRoot": r"C:\Windows"}
    if os.name != "nt":
        env = {"TEMP": "/w/Temp", "SystemRoot": "/w"}
    user, windows = snapshot._default_temp_dirs(env)
    assert user is None
    assert windows is not None


def test_with_timeout_gives_up_on_a_hung_probe():
    import threading
    import time

    release = threading.Event()
    started = time.perf_counter()
    assert snapshot._with_timeout(lambda: release.wait(5) and (1, 1), 0.05) is None
    assert time.perf_counter() - started < 1
    release.set()
    assert snapshot._with_timeout(lambda: (1, 2), 1) == (1, 2)
    assert snapshot._with_timeout(lambda: 1 / 0, 1) is None


def test_real_snapshot_on_this_platform_is_fast_and_safe():
    import time

    started = time.perf_counter()
    snap = take_snapshot()
    assert time.perf_counter() - started < 1.0
    json.dumps(snap)


# --- compare / format ---------------------------------------------------------

def test_format_value_units():
    assert format_value(101.5, "gb") == "101.5 GB"
    assert format_value(100.0, "gb") == "100 GB"
    assert format_value(512.4, "mb") == "512.4 MB"
    assert format_value(2048.0, "mb") == "2 GB"
    assert format_value(7, "count") == "7"
    assert format_value(-1.5, "gb", signed=True) == "\u22121.5 GB"
    assert format_value(1.5, "gb", signed=True) == "+1.5 GB"
    assert format_value(0, "count", signed=True) == "0"


def test_compare_only_metrics_known_on_both_sides():
    before = {"free_gb": 100.0, "temp_user_mb": 900.0, "temp_user_complete": True,
              "startup_entries": 9, "mem_available_mb": None}
    after = {"free_gb": 101.5, "temp_user_mb": 20.0, "temp_user_complete": True,
             "startup_entries": 9, "recycle_bin_mb": 0.0, "mem_available_mb": 4000}

    rows = {r["key"]: r for r in compare_snapshots(before, after)}

    assert set(rows) == {"free_gb", "temp_user_mb", "startup_entries"}
    assert rows["free_gb"]["delta"] == "+1.5 GB" and rows["free_gb"]["trend"] == "good"
    assert rows["temp_user_mb"]["delta"] == "\u2212880 MB" and rows["temp_user_mb"]["trend"] == "good"
    assert rows["startup_entries"]["trend"] == "same"


def test_compare_bad_direction():
    rows = compare_snapshots({"startup_entries": 3, "free_gb": 10.0}, {"startup_entries": 5, "free_gb": 9.0})
    assert [r["trend"] for r in rows] == ["bad", "bad"]


def test_compare_lower_bound_before_gives_at_most_delta():
    rows = compare_snapshots(
        {"temp_windows_mb": 3000.0, "temp_windows_complete": False},
        {"temp_windows_mb": 100.0, "temp_windows_complete": True},
    )
    row = rows[0]
    assert row["before"] == "\u2265 2.93 GB"
    assert row["after"] == "100 MB"
    assert row["delta"].startswith("\u2264 \u2212")
    assert row["trend"] == "good"
    assert row["lower_bound"] is True


def test_compare_lower_bound_after_is_undetermined_when_it_shrank():
    rows = compare_snapshots(
        {"temp_user_mb": 500.0, "temp_user_complete": True},
        {"temp_user_mb": 300.0, "temp_user_complete": False},
    )
    assert rows[0]["delta"] is None and rows[0]["trend"] is None


def test_compare_tolerates_old_and_malformed_snapshots():
    assert compare_snapshots({}, {}) == []
    assert compare_snapshots(None, "garbage") == []
    rows = compare_snapshots({"free_gb": 10.0}, {"free_gb": 12.0, "total_gb": 100})
    assert [r["key"] for r in rows] == ["free_gb"]
    # bools / strings from a hand-edited JSON are not numbers
    assert compare_snapshots({"startup_entries": True, "free_gb": "10"}, {"startup_entries": 1, "free_gb": 3}) == []
