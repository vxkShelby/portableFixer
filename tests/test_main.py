import json
from pathlib import Path

from portablefix.audit_log import audit_log_path
from main import _write_startup_diagnostics


def test_write_startup_diagnostics_logs_resolved_paths(tmp_path):
    raw_base_dir = tmp_path / "USB Fixer"
    base_dir = tmp_path / "USB Fixer"  # writable base dir == raw base dir in the common case

    _write_startup_diagnostics(raw_base_dir, base_dir, used_fallback=False, run_id="run_startup1", dry_run=False)

    log_path = audit_log_path(base_dir, "run_startup1")
    assert log_path.exists()
    entry = json.loads(log_path.read_text(encoding="utf-8").splitlines()[0])
    assert entry["module_id"] == "_system"
    assert entry["action_id"] == "startup_diagnostics"
    assert entry["run_id"] == "run_startup1"
    assert entry["exit_code"] == 0
    assert str(raw_base_dir) in entry["output"]
    assert str(base_dir) in entry["output"]
    assert "used_fallback=False" in entry["output"]
    assert "temp_root=" in entry["output"]
    assert "windir_temp_root=" in entry["output"]


def test_write_startup_diagnostics_records_fallback_flag(tmp_path):
    raw_base_dir = tmp_path / "unwritable"
    fallback_dir = tmp_path / "TEMP" / "PortableFix"

    _write_startup_diagnostics(raw_base_dir, fallback_dir, used_fallback=True, run_id="run_startup2", dry_run=True)

    log_path = audit_log_path(fallback_dir, "run_startup2")
    entry = json.loads(log_path.read_text(encoding="utf-8").splitlines()[0])
    assert "used_fallback=True" in entry["output"]
    assert entry["dry_run"] is True


def test_write_startup_diagnostics_swallows_write_failure(tmp_path, monkeypatch):
    import main as main_module

    def raise_oserror(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(main_module, "append_entry", raise_oserror)

    # Must not raise - a forensic breadcrumb failing to write is not fatal
    # to startup, same as every other best-effort disk write in this app.
    _write_startup_diagnostics(tmp_path, tmp_path, used_fallback=False, run_id="run_startup3", dry_run=False)
