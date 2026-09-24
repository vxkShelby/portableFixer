"""Full registry hive backup before a DESTRUCTIVE batch (research G24)."""

import subprocess
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from portablefix import hive_backup


def _fake_run(calls, exit_codes=None, write=True, raise_for=None):
    exit_codes = dict(exit_codes or {})

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        hive = argv[2].split("\\", 1)[1]
        if raise_for == hive:
            raise subprocess.TimeoutExpired(argv, kwargs.get("timeout"))
        code = exit_codes.get(hive, 0)
        if code == 0 and write:
            Path(argv[3]).write_bytes(b"regf")
        return SimpleNamespace(returncode=code, stdout=b"", stderr=b"")

    return run


def test_save_hives_saves_software_and_system_with_reg_save(tmp_path):
    calls = []
    result = hive_backup.save_hives(tmp_path / "hives", run=_fake_run(calls))
    assert result.success and result.detail == ""
    assert [argv[1:3] for argv, _ in calls] == [["save", "HKLM\\SOFTWARE"], ["save", "HKLM\\SYSTEM"]]
    # /y: never stop on reg.exe's interactive "overwrite?" prompt.
    assert all(argv[-1] == "/y" for argv, _ in calls)
    assert result.files == [tmp_path / "hives" / "SOFTWARE.hiv", tmp_path / "hives" / "SYSTEM.hiv"]
    assert all(kwargs["timeout"] == hive_backup.HIVE_SAVE_TIMEOUT_SEC for _, kwargs in calls)


def test_a_failed_hive_fails_the_backup_by_exit_code_only(tmp_path):
    calls = []
    result = hive_backup.save_hives(tmp_path / "hives", run=_fake_run(calls, exit_codes={"SYSTEM": 1}))
    assert not result.success
    assert "HKLM\\SYSTEM" in result.detail and "exit 1" in result.detail
    assert result.files == [tmp_path / "hives" / "SOFTWARE.hiv"]


def test_exit_zero_without_the_file_is_a_failure(tmp_path):
    result = hive_backup.save_hives(tmp_path / "hives", run=_fake_run([], write=False))
    assert not result.success


def test_a_hung_reg_save_is_reported_not_raised(tmp_path):
    result = hive_backup.save_hives(tmp_path / "hives", run=_fake_run([], raise_for="SOFTWARE"))
    assert not result.success and "timed out" in result.detail


def test_reg_exe_missing_is_reported_not_raised(tmp_path):
    def run(argv, **kwargs):
        raise FileNotFoundError("reg")

    result = hive_backup.save_hives(tmp_path / "hives", run=run)
    assert not result.success and "could not start" in result.detail


def test_backup_dir_is_under_the_runs_backups_and_never_reused(tmp_path):
    now = datetime(2026, 9, 24, 10, 0, 0, tzinfo=timezone.utc)
    first = hive_backup.backup_dir(tmp_path, "run1", now=now)
    assert first == tmp_path / "Backups" / "run1" / "hives-20260924-100000"
    first.mkdir(parents=True)
    # A second DESTRUCTIVE batch in the same second must not overwrite the first copy.
    assert hive_backup.backup_dir(tmp_path, "run1", now=now) == tmp_path / "Backups" / "run1" / "hives-20260924-100000-2"


def test_command_text_names_both_hives_and_the_target_files(tmp_path):
    text = hive_backup.command_text(tmp_path / "h")
    assert "reg save HKLM\\SOFTWARE" in text and "reg save HKLM\\SYSTEM" in text
    assert str(tmp_path / "h" / "SOFTWARE.hiv") in text


def test_estimate_bytes_sums_the_live_hive_files(tmp_path, monkeypatch):
    config = tmp_path / "System32" / "config"
    config.mkdir(parents=True)
    (config / "SOFTWARE").write_bytes(b"x" * 300)
    (config / "SYSTEM").write_bytes(b"x" * 40)
    monkeypatch.setenv("SystemRoot", str(tmp_path))
    assert hive_backup.estimate_bytes() == 340


def test_estimate_bytes_unknown_is_none(tmp_path, monkeypatch):
    monkeypatch.setenv("SystemRoot", str(tmp_path))
    monkeypatch.delenv("WINDIR", raising=False)
    assert hive_backup.estimate_bytes() is None
