import json

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


# --- main() wiring for the resilience fixes ---


def _fake_main_env(monkeypatch, base_dir, events):
    """Runs main() with every Windows/GUI dependency replaced by fakes that
    record what happened, in order."""
    from unittest.mock import MagicMock

    import main as main_module

    class FakeApp:
        def __init__(self, argv):
            pass

        def setStyle(self, *a):
            pass

        def setStyleSheet(self, *a):
            pass

        def setWindowIcon(self, *a):
            pass

        def exec(self):
            events.append("exec")
            return 0

    runner = MagicMock()
    runner.stop.side_effect = lambda *a, **k: events.append("integrity_stop")
    message_box = MagicMock()
    message_box.warning.side_effect = lambda parent, title, text: events.append(("warning", text))
    message_box.critical.side_effect = lambda parent, title, text: events.append(("critical", text))
    monkeypatch.setattr(main_module, "_acquire_single_instance_lock", lambda: True)
    monkeypatch.setattr(main_module, "QApplication", FakeApp)
    monkeypatch.setattr(main_module, "QMessageBox", message_box)
    monkeypatch.setattr(main_module, "get_base_dir", lambda: base_dir)
    monkeypatch.setattr(main_module, "install_excepthook", lambda *a: None)
    monkeypatch.setattr(main_module, "is_admin", lambda: False)
    monkeypatch.setattr(main_module, "MainWindow", MagicMock())
    monkeypatch.setattr(main_module, "IntegrityCheckRunner", MagicMock(return_value=runner))
    return main_module


def test_main_stops_the_integrity_check_after_the_event_loop_ends(tmp_path, monkeypatch):
    # Destroying the window while its IntegrityCheckRunner still hashes
    # aborts the process (see test_integrity) - main() must stop it first.
    events = []
    main_module = _fake_main_env(monkeypatch, tmp_path, events)

    assert main_module.main() == 0
    assert events.index("exec") < events.index("integrity_stop")


def test_main_reports_the_outcome_of_the_last_update_once(tmp_path, monkeypatch):
    from portablefix import i18n, updater

    events = []
    (tmp_path / "Data").mkdir()
    updater.update_status_path(tmp_path).write_text("rolled_back\n", encoding="ascii")
    main_module = _fake_main_env(monkeypatch, tmp_path, events)

    main_module.main()

    expected_start = i18n.translate("update_status_failed", "sk").split("{")[0]
    warnings = [e[1] for e in events if isinstance(e, tuple) and e[0] == "warning"]
    assert any(w.startswith(expected_start) for w in warnings), warnings
    assert not updater.update_status_path(tmp_path).exists()


def test_update_status_message_restores_stranded_modules_and_says_so(tmp_path):
    import main as main_module
    from portablefix import i18n

    (tmp_path / "Modules.old").mkdir()

    message = main_module._update_status_message(tmp_path, "en")

    assert (tmp_path / "Modules").is_dir()
    assert message == i18n.translate("update_status_interrupted", "en")


def test_update_status_message_is_silent_after_a_clean_update(tmp_path):
    import main as main_module
    from portablefix import updater

    (tmp_path / "Data").mkdir()
    updater.update_status_path(tmp_path).write_text("ok\n", encoding="ascii")

    assert main_module._update_status_message(tmp_path, "sk") is None


def test_main_writes_a_crash_log_when_startup_fails(tmp_path, monkeypatch):
    # "Startup failed" dialogs used to leave no trace anywhere on disk.
    from portablefix.diagnostics import crash_log_path

    events = []
    main_module = _fake_main_env(monkeypatch, tmp_path, events)

    def broken_window(**kwargs):
        raise ValueError("modules exploded")

    monkeypatch.setattr(main_module, "MainWindow", broken_window)

    assert main_module.main() == 1
    assert "ValueError: modules exploded" in crash_log_path(tmp_path).read_text(encoding="utf-8")
