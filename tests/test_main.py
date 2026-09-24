import json

import pytest

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

        def setStyleSheet(self, sheet):
            events.append(("stylesheet", sheet))

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
    monkeypatch.setattr(main_module, "_acquire_single_instance_lock", lambda *a, **k: True)
    monkeypatch.setattr(main_module.update_swap, "update_mutex_present", lambda: False)
    monkeypatch.setattr(main_module.sys, "argv", ["PortableFix.exe"])
    monkeypatch.setattr(main_module, "_message_box", lambda text: events.append(("message_box", text)))
    # The real one would tidy this machine's own %TEMP%.
    monkeypatch.setattr(main_module.updater, "cleanup_update_leftovers", lambda *a, **k: [])
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


def test_main_skips_custom_theme_under_windows_high_contrast(tmp_path, monkeypatch):
    # research-accessibility.md Finding 3: High Contrast users need their
    # system colors - the dark theme must not be forced on them.
    from portablefix.gui import style

    events = []
    main_module = _fake_main_env(monkeypatch, tmp_path, events)
    monkeypatch.setattr(style, "is_high_contrast", lambda: True)
    main_module.main()
    assert [e for e in events if isinstance(e, tuple) and e[0] == "stylesheet"] == [("stylesheet", "")]

    events.clear()
    monkeypatch.setattr(style, "is_high_contrast", lambda: False)
    main_module.main()
    assert [e for e in events if isinstance(e, tuple) and e[0] == "stylesheet"] == [("stylesheet", style.STYLE)]


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


# --- Relaunch switches, mutexes and update leftovers ---


def test_parse_startup_args_strips_the_apps_own_switches():
    import main as main_module

    args = main_module._parse_startup_args(
        ["PortableFix.exe", "--post-update", "-style", "fusion", "--wait-pid", "12", "--wait-pid", "34",
         "--update-from-zip", "C:\\x y\\p.zip", "--sha256", "ab" * 32],
    )

    assert args.argv == ["PortableFix.exe", "-style", "fusion"]
    assert args.post_update is True
    assert args.wait_pids == [12, 34]
    assert args.update_zip == "C:\\x y\\p.zip"
    assert args.update_sha256 == "ab" * 32
    assert args.is_relaunch is True


def test_parse_startup_args_drops_a_malformed_wait_pid_and_a_dangling_switch():
    import main as main_module

    args = main_module._parse_startup_args(["PortableFix.exe", "--wait-pid", "abc", "--wait-pid"])

    assert args.argv == ["PortableFix.exe"]
    assert args.wait_pids == []
    assert args.is_relaunch is False


def test_main_removes_the_switches_from_argv_before_qt_sees_them(tmp_path, monkeypatch):
    events = []
    main_module = _fake_main_env(monkeypatch, tmp_path, events)
    seen = []

    class RecordingApp(main_module.QApplication):
        def __init__(self, argv):
            seen.append(list(argv))

    monkeypatch.setattr(main_module, "QApplication", RecordingApp)
    monkeypatch.setattr(main_module.sys, "argv", ["PortableFix.exe", "--post-update", "--wait-pid", "7"])
    waited = []
    monkeypatch.setattr(main_module.update_swap, "wait_for_process_exit", lambda pid, timeout: waited.append((pid, timeout)))

    assert main_module.main() == 0
    assert seen == [["PortableFix.exe"]]
    assert waited == [(7, main_module._WAIT_PID_TIMEOUT_SEC)]


class _FakeMutexes:
    """update_swap.create_mutex/close_handle stand-ins: replays (handle,
    last error) results and records which handles were closed."""

    def __init__(self, results):
        self.results = list(results)
        self.closed = []

    def create(self, name):
        return self.results.pop(0)

    def close(self, handle):
        if handle:
            self.closed.append(handle)


def _patch_mutexes(monkeypatch, main_module, results):
    fake = _FakeMutexes(results)
    monkeypatch.setattr(main_module.update_swap, "create_mutex", fake.create)
    monkeypatch.setattr(main_module.update_swap, "close_handle", fake.close)
    monkeypatch.setattr(main_module, "_single_instance_handle", 0)
    return fake


def test_single_instance_lock_is_acquired_and_its_handle_kept(monkeypatch):
    import main as main_module

    fake = _patch_mutexes(monkeypatch, main_module, [(77, 0)])

    assert main_module._acquire_single_instance_lock() is True
    assert main_module._single_instance_handle == 77
    assert fake.closed == []


def test_single_instance_lock_treats_a_null_handle_as_another_instance(monkeypatch):
    # An elevated instance's mutex refuses a non-elevated CreateMutexW with
    # ERROR_ACCESS_DENIED and a NULL handle - that used to count as "not
    # running", so two instances locked each other's exe.
    import main as main_module

    _patch_mutexes(monkeypatch, main_module, [(0, 5)])

    assert main_module._acquire_single_instance_lock() is False


def test_single_instance_lock_retry_closes_its_own_handle_between_attempts(monkeypatch):
    import main as main_module

    fake = _patch_mutexes(monkeypatch, main_module, [(11, 183), (0, 5), (12, 0)])
    clock = iter(range(100))
    monkeypatch.setattr(main_module.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(main_module.time, "sleep", lambda s: None)

    assert main_module._acquire_single_instance_lock(retry_sec=30) is True
    # Holding on to handle 11 would have kept the old name alive forever.
    assert fake.closed == [11]
    assert main_module._single_instance_handle == 12


def test_main_second_launch_shows_already_running(tmp_path, monkeypatch):
    events = []
    main_module = _fake_main_env(monkeypatch, tmp_path, events)
    monkeypatch.setattr(main_module, "_acquire_single_instance_lock", lambda *a, **k: False)

    assert main_module.main() == 0
    boxes = [e[1] for e in events if isinstance(e, tuple) and e[0] == "message_box"]
    assert len(boxes) == 1 and "already running" in boxes[0]
    assert "exec" not in events


def test_main_post_update_relaunch_retries_the_lock_and_exits_silently(tmp_path, monkeypatch):
    events = []
    main_module = _fake_main_env(monkeypatch, tmp_path, events)
    retries = []

    def still_held(retry_sec=0.0):
        retries.append(retry_sec)
        return False

    monkeypatch.setattr(main_module, "_acquire_single_instance_lock", still_held)
    monkeypatch.setattr(main_module.sys, "argv", ["PortableFix.exe", "--post-update"])

    assert main_module.main() == 0
    assert retries == [main_module._RELAUNCH_RETRY_SEC]
    assert not [e for e in events if isinstance(e, tuple) and e[0] == "message_box"]
    assert "exec" not in events


def test_main_refuses_to_start_while_an_update_is_replacing_the_files(tmp_path, monkeypatch):
    from portablefix import i18n

    events = []
    main_module = _fake_main_env(monkeypatch, tmp_path, events)
    monkeypatch.setattr(main_module.update_swap, "update_mutex_present", lambda: True)
    locked = []
    monkeypatch.setattr(main_module, "_acquire_single_instance_lock", lambda *a, **k: locked.append(True) or True)

    assert main_module.main() == 0
    boxes = [e[1] for e in events if isinstance(e, tuple) and e[0] == "message_box"]
    assert boxes == [
        i18n.translate("update_in_progress_running", "sk") + "\n" + i18n.translate("update_in_progress_running", "en")
    ]
    assert locked == [] and "exec" not in events


def test_main_post_update_waits_for_the_update_mutex_to_go_away(tmp_path, monkeypatch):
    events = []
    main_module = _fake_main_env(monkeypatch, tmp_path, events)
    present = iter([True, True, False])
    monkeypatch.setattr(main_module.update_swap, "update_mutex_present", lambda: next(present))
    monkeypatch.setattr(main_module.time, "sleep", lambda s: None)
    monkeypatch.setattr(main_module.sys, "argv", ["PortableFix.exe", "--post-update"])

    assert main_module.main() == 0
    assert "exec" in events
    assert not [e for e in events if isinstance(e, tuple) and e[0] == "message_box"]


def test_main_reports_a_handed_off_update_that_never_finished(tmp_path, monkeypatch):
    from portablefix import i18n, updater

    events = []
    (tmp_path / "Data").mkdir()
    updater.update_status_path(tmp_path).write_text("handed_off\n", encoding="ascii")
    main_module = _fake_main_env(monkeypatch, tmp_path, events)

    main_module.main()

    expected_start = i18n.translate("update_status_incomplete", "sk").split("{")[0]
    warnings = [e[1] for e in events if isinstance(e, tuple) and e[0] == "warning"]
    assert any(w.startswith(expected_start) for w in warnings), warnings


def test_main_cleans_up_update_leftovers_and_survives_a_failure(tmp_path, monkeypatch):
    from portablefix import updater

    events = []
    main_module = _fake_main_env(monkeypatch, tmp_path, events)
    calls = []

    def broken_cleanup(install_dir):
        calls.append(install_dir)
        raise PermissionError("locked")

    monkeypatch.setattr(updater, "cleanup_update_leftovers", broken_cleanup)

    assert main_module.main() == 0
    assert calls == [tmp_path]
    assert "exec" in events


def test_dev_update_switch_needs_the_env_var(tmp_path, monkeypatch):
    from unittest.mock import MagicMock

    import main as main_module

    window = MagicMock()
    args = main_module._parse_startup_args(["PortableFix.exe", "--update-from-zip", "p.zip", "--sha256", "00"])

    monkeypatch.delenv("PORTABLEFIX_DEV_UPDATE", raising=False)
    main_module._start_dev_update(window, args, tmp_path)
    assert not window.start_local_update.called

    monkeypatch.setenv("PORTABLEFIX_DEV_UPDATE", "1")
    main_module._start_dev_update(window, args, tmp_path)
    window.start_local_update.assert_called_once_with(main_module.Path("p.zip"), "00")


def test_dev_update_switch_refuses_to_replace_a_git_working_tree(tmp_path, monkeypatch):
    # build.ps1 builds into the repo's own App\ - updating that "install"
    # would delete the working tree's Modules\ and Vendor\.
    from unittest.mock import MagicMock

    import main as main_module

    (tmp_path / ".git").mkdir()
    boxes = []
    monkeypatch.setattr(main_module, "_message_box", boxes.append)
    monkeypatch.setenv("PORTABLEFIX_DEV_UPDATE", "1")
    window = MagicMock()
    args = main_module._parse_startup_args(["PortableFix.exe", "--update-from-zip", "p.zip"])

    main_module._start_dev_update(window, args, tmp_path)

    assert not window.start_local_update.called
    assert len(boxes) == 1 and "git" in boxes[0]


def test_main_moves_a_frozen_build_off_the_app_folder(tmp_path, monkeypatch):
    # Started from App\ (a <= 1.11.4 shortcut, Explorer), every child the
    # app spawns would inherit App\ as its cwd - and one that outlives the
    # app (m02 restarts explorer.exe) blocks the next update's App rename.
    import os

    events = []
    main_module = _fake_main_env(monkeypatch, tmp_path, events)
    (tmp_path / "App").mkdir()
    monkeypatch.chdir(tmp_path / "App")
    monkeypatch.setattr(main_module.sys, "frozen", True, raising=False)

    main_module.main()

    assert os.path.samefile(os.getcwd(), tmp_path)


def test_leave_app_folder_keeps_a_relative_dev_zip_pointing_at_the_same_file(tmp_path, monkeypatch):
    import os

    import main as main_module

    (tmp_path / "App").mkdir()
    monkeypatch.chdir(tmp_path / "App")
    monkeypatch.setattr(main_module.sys, "frozen", True, raising=False)
    monkeypatch.setattr(main_module, "get_base_dir", lambda: tmp_path)
    args = main_module._parse_startup_args(["PortableFix.exe", "--update-from-zip", "p.zip"])

    main_module._leave_app_folder(args)

    assert os.path.samefile(os.getcwd(), tmp_path)
    assert main_module.Path(args.update_zip).resolve() == (tmp_path / "App" / "p.zip").resolve()


def test_leave_app_folder_is_a_no_op_when_running_from_source(tmp_path, monkeypatch):
    import os

    import main as main_module

    monkeypatch.chdir(tmp_path)
    monkeypatch.delattr(main_module.sys, "frozen", raising=False)
    monkeypatch.setattr(main_module, "get_base_dir", lambda: tmp_path / "elsewhere")

    main_module._leave_app_folder(main_module._parse_startup_args(["main.py"]))

    assert os.path.samefile(os.getcwd(), tmp_path)


def _age(path, days):
    import os
    import time

    stamp = time.time() - days * 86400
    os.utime(path, (stamp, stamp))


def test_cleanup_update_leftovers_removes_only_old_or_stale_items(tmp_path):
    from portablefix import updater

    temp = tmp_path / "TEMP"
    logs = temp / "PortableFixUpdate"
    install = tmp_path / "USB Fixer"
    logs.mkdir(parents=True)
    install.mkdir()
    old_download = temp / "PortableFixUpdate_old"
    new_download = temp / "PortableFixUpdate_new"
    for folder in (old_download, new_download):
        folder.mkdir()
        (folder / "PortableFix-update.zip").write_bytes(b"zip")
    _age(old_download, 2)
    legacy_script = temp / "portablefix_update_1234.ps1"
    legacy_script.write_text("x", encoding="ascii")
    old_log, new_log = logs / "launch_1.txt", logs / "launch_2.txt"
    old_popen, old_swap = logs / "popen_launch_1.log", logs / "swap_1_ab.ps1"
    for f in (old_log, new_log, old_popen, old_swap):
        f.write_text("x", encoding="ascii")
    for f in (old_log, old_popen, old_swap):
        _age(f, 15)
    unrelated = temp / "someone_elses.txt"
    unrelated.write_text("x", encoding="ascii")
    _age(unrelated, 30)

    removed = updater.cleanup_update_leftovers(install, temp_dir=temp, log_dir=logs, current_version="1.12.0")

    assert set(removed) == {old_download, legacy_script, old_log, old_popen, old_swap}
    assert new_download.exists() and new_log.exists() and unrelated.exists() and logs.is_dir()


def _make_stage(install, version, days_old=0):
    stage = install / "_update_stage"
    (stage / "PortableFix" / "App").mkdir(parents=True)
    if version is not None:
        (stage / "version.txt").write_text(version, encoding="utf-8")
    if days_old:
        _age(stage, days_old)
    return stage


@pytest.mark.parametrize(
    ("version", "days_old", "kept"),
    [
        ("1.13.0", 0, True),  # newer and fresh: a retry could still use it
        ("1.13.0", 2, False),  # abandoned
        ("1.12.0", 0, False),  # already installed (or older)
        (None, 0, False),  # unknown version
    ],
)
def test_cleanup_update_leftovers_keeps_only_a_fresh_newer_stage(tmp_path, version, days_old, kept):
    from portablefix import updater

    install = tmp_path / "install"
    stage = _make_stage(install, version, days_old)
    (tmp_path / "TEMP").mkdir()

    updater.cleanup_update_leftovers(install, temp_dir=tmp_path / "TEMP", log_dir=tmp_path / "none", current_version="1.12.0")

    assert stage.exists() is kept
