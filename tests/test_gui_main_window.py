import json
import os
import shutil
from pathlib import Path

import pytest

from PySide6.QtWidgets import QApplication, QMessageBox

from portablefix import elevation
from portablefix.audit_log import audit_log_path
from portablefix.gui.main_window import MainWindow
from portablefix.settings import Settings


def _write_module(base_dir, module_id, category, action_id):
    module_dir = base_dir / "Modules" / module_id
    module_dir.mkdir(parents=True)
    (module_dir / "actions.yaml").write_text(
        f"module_id: {module_id}\n"
        f"category: {category}\n"
        "actions:\n"
        f"  - id: {action_id}\n"
        "    label_sk: \"X\"\n"
        "    label_en: \"X\"\n"
        "    risk: SAFE\n"
        "    command: \"Write-Output 'x'\"\n",
        encoding="utf-8",
    )

ACTIONS_YAML = """
module_id: m01_diagnostics
actions:
  - id: hello
    label_sk: "Pozdrav"
    label_en: "Greeting"
    risk: SAFE
    command: "Write-Output 'hello-from-gui-test'"
    description_sk: "Test"
    description_en: "Test"
"""

MODERATE_ACTIONS_YAML = """
module_id: m01_diagnostics
actions:
  - id: risky
    label_sk: "Riskantna akcia"
    label_en: "Risky action"
    risk: MODERATE
    command: "Write-Output 'risky-ran'"
    description_sk: "Test"
    description_en: "Test"
"""


def _audit_entries(log_path: Path) -> list[dict]:
    if not log_path.exists():
        return []
    return [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _executed_action_ids(log_path: Path) -> list[str]:
    # Real action entries only - "_system" entries (restore point, declined
    # confirmations) name the action they guard in `subject` and don't
    # mean it ran.
    return [e["action_id"] for e in _audit_entries(log_path) if e["module_id"] != "_system"]


def _system_events(log_path: Path, kind: str) -> list[dict]:
    return [e for e in _audit_entries(log_path) if e["module_id"] == "_system" and e["action_id"] == kind]


def _wait_batch_idle(qtbot, window, timeout=15000):
    # Wait for the batch AND its (threaded) report to finish before the test
    # ends: otherwise qtbot's teardown closes a window mid-batch, closeEvent
    # asks "a batch is running, close anyway?", and headless that dialog
    # either hangs forever or (with conftest's guard) errors in teardown.
    qtbot.waitUntil(lambda: not window._batch_active and window._report_runner is None, timeout=timeout)


def _make_base_dir(tmp_path: Path, yaml_text: str = ACTIONS_YAML) -> Path:
    module_dir = tmp_path / "Modules" / "m01_diagnostics"
    module_dir.mkdir(parents=True)
    (module_dir / "actions.yaml").write_text(yaml_text, encoding="utf-8")
    return tmp_path


def _fake_staged(tmp_path: Path, version: str | None):
    from portablefix.updater import StagedUpdate

    stage_dir = tmp_path / "_update_stage"
    root = stage_dir / "PortableFix"
    (root / "App").mkdir(parents=True, exist_ok=True)
    (root / "App" / "PortableFix.exe").write_bytes(b"new-exe")
    return StagedUpdate(stage_dir=stage_dir, stage_root=root, file_count=1, byte_count=7, version=version)


def _patch_update_flow(monkeypatch, tmp_path: Path, launch_results: list) -> dict:
    """Replaces the network, the staging and the updater spawn behind the
    real Download/Stage/Launch runners; returns the call counts. Nothing
    touches the real install folder (paths.get_base_dir() in a test)."""
    from portablefix import updater

    calls = {"download": 0, "stage": 0, "launch": 0}

    def fake_download(info, dest, on_progress=None, should_stop=None):
        calls["download"] += 1
        zip_path = dest / "PortableFix-update.zip"
        zip_path.write_bytes(b"zip")
        return zip_path

    def fake_stage(zip_path, install_dir, should_stop=None, progress=None, version=None):
        calls["stage"] += 1
        calls["staged_version"] = version
        return _fake_staged(tmp_path, version)

    def fake_launch(staged, install_dir, should_stop=None, **kwargs):
        calls["launch"] += 1
        return launch_results.pop(0)

    monkeypatch.setattr(updater, "download_update", fake_download)
    monkeypatch.setattr(updater, "is_writable", lambda p: True)
    monkeypatch.setattr(updater, "stage_update", fake_stage)
    monkeypatch.setattr(updater, "launch_swap", fake_launch)
    return calls


def test_main_window_loads_m01_actions(qtbot, tmp_path):
    base_dir = _make_base_dir(tmp_path)
    window = MainWindow(assets_dir=base_dir, state_dir=base_dir, settings=Settings(), is_admin=True, run_id="testrun")
    qtbot.addWidget(window)
    assert "hello" in window._action_checkboxes


def test_action_checkbox_shows_description_as_tooltip(qtbot, tmp_path):
    module_dir = tmp_path / "Modules" / "m01_diagnostics"
    module_dir.mkdir(parents=True)
    (module_dir / "actions.yaml").write_text(
        "module_id: m01_diagnostics\n"
        "category: DIAGNOSTICS\n"
        "actions:\n"
        "  - id: described_action\n"
        "    label_sk: \"X\"\n"
        "    label_en: \"X\"\n"
        "    risk: SAFE\n"
        "    command: \"Write-Output 'x'\"\n"
        "    description_sk: \"Popis SK\"\n"
        "    description_en: \"Description EN\"\n",
        encoding="utf-8",
    )
    settings = Settings(language="en", dry_run=True)
    window = MainWindow(assets_dir=tmp_path, state_dir=tmp_path, settings=settings, is_admin=True, run_id="run_tooltip")
    qtbot.addWidget(window)
    assert window._action_checkboxes["described_action"].toolTip() == "Description EN"
    assert window._action_checkboxes["described_action"].accessibleDescription() == "Description EN"


def test_action_checkbox_accessible_name_includes_risk_level(qtbot, tmp_path):
    base_dir = _make_base_dir(tmp_path, _TWO_SAFE_ACTIONS_YAML)
    window = MainWindow(assets_dir=base_dir, state_dir=base_dir, settings=Settings(language="en"), is_admin=True, run_id="run_a11y_name")
    qtbot.addWidget(window)

    name = window._action_checkboxes["first_action"].accessibleName()
    assert "First action" in name
    assert "SAFE" in name


def test_action_status_update_appends_status_to_accessible_name(qtbot, tmp_path):
    base_dir = _make_base_dir(tmp_path, _TWO_SAFE_ACTIONS_YAML)
    window = MainWindow(assets_dir=base_dir, state_dir=base_dir, settings=Settings(language="en"), is_admin=True, run_id="run_a11y_status")
    qtbot.addWidget(window)

    window._set_action_status("first_action", "ok", "OK (1.2s)")

    name = window._action_checkboxes["first_action"].accessibleName()
    assert "SAFE" in name
    assert "OK (1.2s)" in name


def test_language_toggle_preserves_category_selection_and_focus(qtbot, tmp_path):
    base_dir = tmp_path
    _write_module(base_dir, "m01_diagnostics", "DIAGNOSTICS", "diag_action")
    _write_module(base_dir, "m02_cleanup", "CLEANUP", "clean_action")
    window = MainWindow(assets_dir=base_dir, state_dir=base_dir, settings=Settings(language="en"), is_admin=True, run_id="run_lang_focus")
    qtbot.addWidget(window)
    window.show()
    window.activateWindow()
    qtbot.waitActive(window, timeout=5000)

    # row 0 is the always-present Dashboard, row 1 Diagnostics, row 2 Cleanup.
    window.category_list.setCurrentRow(2)
    window._action_checkboxes["clean_action"].setFocus()
    # hasFocus() only reflects reality once the OS has actually handed this
    # window keyboard focus, which is asynchronous even after activateWindow().
    qtbot.waitUntil(lambda: window._action_checkboxes["clean_action"].hasFocus(), timeout=5000)

    window._on_toggle_language()

    assert window.category_list.currentRow() == 2
    qtbot.waitUntil(lambda: window._action_checkboxes["clean_action"].hasFocus(), timeout=5000)


def test_run_selected_action_writes_console_and_audit_log(qtbot, tmp_path):
    base_dir = _make_base_dir(tmp_path)
    settings = Settings(language="sk", dry_run=False)
    window = MainWindow(assets_dir=base_dir, state_dir=base_dir, settings=settings, is_admin=True, run_id="testrun")
    qtbot.addWidget(window)
    window._action_checkboxes["hello"].setChecked(True)

    window.run_selected_actions()

    log_path = audit_log_path(base_dir, "testrun")
    qtbot.waitUntil(lambda: log_path.exists() and log_path.read_text(encoding="utf-8").strip() != "", timeout=10000)

    assert "hello-from-gui-test" in window.console.toPlainText()
    line = log_path.read_text(encoding="utf-8").splitlines()[0]
    entry = json.loads(line)
    assert entry["action_id"] == "hello"
    assert entry["exit_code"] == 0


_PROBLEM_KEYWORD_ACTIONS_YAML = """
module_id: m01_diagnostics
actions:
  - id: diag_check
    label_sk: "Kontrola"
    label_en: "Check"
    risk: SAFE
    command: "Write-Output 'STATE: BROKEN'"
    problem_keywords:
      - "STATE: BROKEN"
    recommended_action_ids:
      - fix_it
  - id: fix_it
    label_sk: "Oprava"
    label_en: "Fix"
    risk: MODERATE
    command: "Write-Output 'fixed'"
"""


def test_diagnostic_action_with_matching_output_recommends_fix(qtbot, tmp_path):
    base_dir = _make_base_dir(tmp_path, _PROBLEM_KEYWORD_ACTIONS_YAML)
    window = MainWindow(assets_dir=base_dir, state_dir=base_dir, settings=Settings(dry_run=False), is_admin=True, run_id="testrun")
    qtbot.addWidget(window)
    window._action_checkboxes["diag_check"].setChecked(True)

    window.run_selected_actions()

    qtbot.waitUntil(lambda: not window._batch_active, timeout=10000)
    assert "fix_it" in window._recommended_action_ids


def test_diagnostic_action_with_no_matching_output_recommends_nothing(qtbot, tmp_path):
    base_dir = _make_base_dir(tmp_path, ACTIONS_YAML)
    window = MainWindow(assets_dir=base_dir, state_dir=base_dir, settings=Settings(dry_run=False), is_admin=True, run_id="testrun")
    qtbot.addWidget(window)
    window._action_checkboxes["hello"].setChecked(True)

    window.run_selected_actions()

    qtbot.waitUntil(lambda: not window._batch_active, timeout=10000)
    assert window._recommended_action_ids == set()


_USER_TEMP_ACTIONS_YAML = """
module_id: m02_cleanup
actions:
  - id: user_temp
    label_sk: "X"
    label_en: "User temp files"
    risk: SAFE
    command: "Write-Output 'user-temp-ran'"
"""


def test_user_temp_action_is_blocked_when_app_is_temp_root_or_temp_is_redirected(qtbot, tmp_path, monkeypatch):
    # Regression test for the wiring in _dispatch_action, not paths.py's own
    # logic (already covered by tests/test_paths.py) - this is the choke
    # point a future refactor of _dispatch_action could silently break
    # (reordering the check past the dry-run branch, forgetting to call
    # _run_next() and stalling the queue) for a bug that has already
    # recurred twice in the field.
    from portablefix.gui import main_window as mw_module

    base_dir = _make_base_dir(tmp_path, _USER_TEMP_ACTIONS_YAML)
    sentinel = tmp_path / "faketemp"
    sentinel.mkdir()
    monkeypatch.setattr(mw_module.paths, "compute_temp_protected_child", lambda app_dir: sentinel)
    monkeypatch.setattr(mw_module.paths, "resolve_temp_root", lambda: sentinel)
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warnings.append(a))

    window = MainWindow(assets_dir=base_dir, state_dir=base_dir, settings=Settings(), is_admin=True, run_id="testrun_blocked")
    qtbot.addWidget(window)
    window._action_checkboxes["user_temp"].setChecked(True)

    window.run_selected_actions()

    qtbot.waitUntil(lambda: not window._batch_active, timeout=5000)
    assert len(warnings) == 1
    assert "user-temp-ran" not in window.console.toPlainText()


_TWO_SAFE_ACTIONS_YAML = """
module_id: m01_diagnostics
actions:
  - id: first_action
    label_sk: "X"
    label_en: "First action"
    risk: SAFE
    command: "Write-Output 'first-ran'"
  - id: second_action
    label_sk: "X"
    label_en: "Second action"
    risk: SAFE
    command: "Write-Output 'second-ran'"
"""


def test_batch_continues_past_disk_write_failure_instead_of_stalling(qtbot, tmp_path, monkeypatch):
    from portablefix.gui import main_window as mw_module

    base_dir = _make_base_dir(tmp_path, _TWO_SAFE_ACTIONS_YAML)
    settings = Settings(language="en", dry_run=False)
    window = MainWindow(assets_dir=base_dir, state_dir=base_dir, settings=settings, is_admin=True, run_id="run_diskfail")
    qtbot.addWidget(window)
    window._action_checkboxes["first_action"].setChecked(True)
    window._action_checkboxes["second_action"].setChecked(True)

    def raise_oserror(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(mw_module, "append_entry", raise_oserror)

    window.run_selected_actions()

    qtbot.waitUntil(lambda: not window._batch_active, timeout=10000)

    assert "second-ran" in window.console.toPlainText()
    assert window.console.toPlainText().count("Disk write failed") == 2


def test_batch_stops_when_app_dir_disappears_mid_run(qtbot, tmp_path, monkeypatch):
    # Regression test for the mid-batch integrity guard in _run_next: if the
    # app's own install folder vanishes between two queued actions (the real
    # incident this guard exists for - something external wiped it out from
    # under the running process), the batch must stop immediately instead of
    # dispatching the next action against a filesystem state nobody can
    # reason about, and it must say so loudly rather than silently continue.
    base_dir = _make_base_dir(tmp_path, _TWO_SAFE_ACTIONS_YAML)
    settings = Settings(language="en", dry_run=False)
    window = MainWindow(assets_dir=base_dir, state_dir=base_dir, settings=settings, is_admin=True, run_id="run_dir_gone")
    qtbot.addWidget(window)
    window._action_checkboxes["first_action"].setChecked(True)
    window._action_checkboxes["second_action"].setChecked(True)

    calls = {"n": 0}

    def fake_intact():
        calls["n"] += 1
        return calls["n"] == 1  # intact for the first dispatch, gone before the second

    monkeypatch.setattr(window, "_app_dir_intact", fake_intact)
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warnings.append(a) or QMessageBox.Ok)

    window.run_selected_actions()

    qtbot.waitUntil(lambda: not window._batch_active, timeout=10000)

    assert "first-ran" in window.console.toPlainText()
    assert "second-ran" not in window.console.toPlainText()
    assert window._queue == []
    assert len(warnings) == 1

    log_path = audit_log_path(base_dir, "run_dir_gone")
    entries = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    assert any(e["module_id"] == "_system" and e["action_id"] == "integrity_guard" for e in entries)


def test_single_action_run_also_stops_when_app_dir_missing_before_dispatch(qtbot, tmp_path, monkeypatch):
    # The guard must not be batch-only: a run with a single selected action
    # goes through the exact same _queue/_run_next path, and this must not
    # false-positive for the normal case either (this test's fake_intact
    # only ever returns False, proving the guard fires even for a 1-item
    # queue rather than being skipped as "too small to be a real batch").
    base_dir = _make_base_dir(tmp_path, _TWO_SAFE_ACTIONS_YAML)
    settings = Settings(language="en", dry_run=False)
    window = MainWindow(assets_dir=base_dir, state_dir=base_dir, settings=settings, is_admin=True, run_id="run_single_gone")
    qtbot.addWidget(window)
    window._action_checkboxes["first_action"].setChecked(True)

    monkeypatch.setattr(window, "_app_dir_intact", lambda: False)
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warnings.append(a) or QMessageBox.Ok)

    window.run_selected_actions()

    qtbot.waitUntil(lambda: not window._batch_active, timeout=10000)

    assert "first-ran" not in window.console.toPlainText()
    assert window._queue == []
    assert len(warnings) == 1


def test_main_window_warns_but_still_opens_when_one_module_is_broken(qtbot, tmp_path, monkeypatch):
    base_dir = _make_base_dir(tmp_path)
    broken_dir = base_dir / "Modules" / "m02_cleanup"
    broken_dir.mkdir(parents=True)
    (broken_dir / "actions.yaml").write_text("module_id: [unclosed\n  bad: yaml:\n", encoding="utf-8")

    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warnings.append(a) or QMessageBox.Ok)

    window = MainWindow(assets_dir=base_dir, state_dir=base_dir, settings=Settings(), is_admin=True, run_id="run_broken_module")
    qtbot.addWidget(window)

    assert len(warnings) == 1
    assert any("m02_cleanup" in str(arg) for arg in warnings[0])
    assert [m.module_id for m in window.modules] == ["m01_diagnostics"]


def test_main_window_warns_when_no_modules_found(qtbot, tmp_path, monkeypatch):
    (tmp_path / "Modules").mkdir()

    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warnings.append(a) or QMessageBox.Ok)

    window = MainWindow(assets_dir=tmp_path, state_dir=tmp_path, settings=Settings(), is_admin=True, run_id="run_no_modules")
    qtbot.addWidget(window)

    assert len(warnings) == 1
    assert window.modules == []


def test_restart_as_admin_button_visibility(qtbot, tmp_path):
    base_dir = _make_base_dir(tmp_path)
    readonly_window = MainWindow(
        assets_dir=base_dir, state_dir=base_dir, settings=Settings(), is_admin=False, run_id="testrun"
    )
    qtbot.addWidget(readonly_window)
    readonly_window.show()
    assert readonly_window.restart_admin_button.isVisible()

    admin_window = MainWindow(
        assets_dir=base_dir, state_dir=base_dir, settings=Settings(), is_admin=True, run_id="testrun"
    )
    qtbot.addWidget(admin_window)
    admin_window.show()
    assert not admin_window.restart_admin_button.isVisible()


def test_audit_log_written_to_state_dir_not_assets_dir(qtbot, tmp_path):
    """Fix 1: Modules must be read from assets_dir while audit log writes to state_dir."""
    assets_dir = _make_base_dir(tmp_path / "usb")
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    settings = Settings(language="sk", dry_run=False)
    window = MainWindow(
        assets_dir=assets_dir, state_dir=state_dir, settings=settings, is_admin=True, run_id="testrun"
    )
    qtbot.addWidget(window)
    assert "hello" in window._action_checkboxes  # module was read from assets_dir
    window._action_checkboxes["hello"].setChecked(True)

    window.run_selected_actions()

    log_path = audit_log_path(state_dir, "testrun")
    qtbot.waitUntil(lambda: log_path.exists() and log_path.read_text(encoding="utf-8").strip() != "", timeout=10000)

    assert "hello-from-gui-test" in window.console.toPlainText()
    assert not audit_log_path(assets_dir, "testrun").exists()
    entry = json.loads(log_path.read_text(encoding="utf-8").splitlines()[0])
    assert entry["action_id"] == "hello"
    assert entry["exit_code"] == 0


def test_restart_as_admin_shows_warning_on_failure(qtbot, tmp_path, monkeypatch):
    base_dir = _make_base_dir(tmp_path)
    window = MainWindow(assets_dir=base_dir, state_dir=base_dir, settings=Settings(), is_admin=False, run_id="testrun")
    qtbot.addWidget(window)

    monkeypatch.setattr(elevation, "relaunch_as_admin", lambda *a, **k: 2)  # <=32 means failure/cancel
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warnings.append(a))

    window._on_restart_as_admin()

    assert len(warnings) == 1


def test_restart_as_admin_closes_window_on_success(qtbot, tmp_path, monkeypatch):
    base_dir = _make_base_dir(tmp_path)
    window = MainWindow(assets_dir=base_dir, state_dir=base_dir, settings=Settings(), is_admin=False, run_id="testrun")
    qtbot.addWidget(window)
    window.show()

    monkeypatch.setattr(elevation, "relaunch_as_admin", lambda *a, **k: 42)  # >32 means success
    closed = []
    monkeypatch.setattr(window, "close", lambda: closed.append(True))

    window._on_restart_as_admin()

    assert closed == [True]


def test_restart_as_admin_passes_sys_argv_when_not_frozen(qtbot, tmp_path, monkeypatch):
    import sys as sys_module

    base_dir = _make_base_dir(tmp_path)
    window = MainWindow(assets_dir=base_dir, state_dir=base_dir, settings=Settings(), is_admin=False, run_id="testrun")
    qtbot.addWidget(window)

    monkeypatch.delattr(sys_module, "frozen", raising=False)
    calls = []
    monkeypatch.setattr(elevation, "relaunch_as_admin", lambda *a, **k: calls.append(a) or 42)
    monkeypatch.setattr(window, "close", lambda: None)

    window._on_restart_as_admin()

    assert calls[0][1] == sys_module.argv


def test_restart_as_admin_passes_no_args_when_frozen(qtbot, tmp_path, monkeypatch):
    import sys as sys_module

    base_dir = _make_base_dir(tmp_path)
    window = MainWindow(assets_dir=base_dir, state_dir=base_dir, settings=Settings(), is_admin=False, run_id="testrun")
    qtbot.addWidget(window)

    monkeypatch.setattr(sys_module, "frozen", True, raising=False)
    calls = []
    monkeypatch.setattr(elevation, "relaunch_as_admin", lambda *a, **k: calls.append(a) or 42)
    monkeypatch.setattr(window, "close", lambda: None)

    window._on_restart_as_admin()

    assert calls[0][1] == []


def test_restart_as_admin_makes_the_new_instance_wait_for_this_one(qtbot, tmp_path, monkeypatch):
    # Without --wait-pid the elevated copy raced this one for the
    # single-instance mutex and usually lost - "already running", no app.
    base_dir = _make_base_dir(tmp_path)
    window = MainWindow(assets_dir=base_dir, state_dir=base_dir, settings=Settings(), is_admin=False, run_id="testrun")
    qtbot.addWidget(window)
    calls = []
    monkeypatch.setattr(elevation, "relaunch_as_admin", lambda *a, **k: calls.append(k) or 42)
    monkeypatch.setattr(window, "close", lambda: None)

    window._on_restart_as_admin()

    assert calls[0]["wait_pids"][0] == os.getpid()


def test_language_toggle_flips_language_and_labels(qtbot, tmp_path):
    base_dir = _make_base_dir(tmp_path)
    settings = Settings(language="sk")
    window = MainWindow(assets_dir=base_dir, state_dir=base_dir, settings=settings, is_admin=True, run_id="testrun")
    qtbot.addWidget(window)

    assert window.settings.language == "sk"
    assert window.run_button.text() == "Spustiť vybrané"

    window.language_button.click()

    assert window.settings.language == "en"
    assert window.run_button.text() == "Run selected"
    assert window.language_button.text() == "EN"


def test_moderate_risk_action_declined_does_not_run_but_logs_the_decline(qtbot, tmp_path, monkeypatch):
    base_dir = _make_base_dir(tmp_path, MODERATE_ACTIONS_YAML)
    settings = Settings(language="sk", dry_run=False)
    window = MainWindow(assets_dir=base_dir, state_dir=base_dir, settings=settings, is_admin=True, run_id="testrun")
    qtbot.addWidget(window)
    window._action_checkboxes["risky"].setChecked(True)

    # G12: the decline now happens on the batch review screen.
    _answer_review(monkeypatch, accept=False)

    window.run_selected_actions()

    qtbot.wait(300)
    assert "risky-ran" not in window.console.toPlainText()
    # Not run - but the "No" itself is on record (research-reporting.md F2).
    log_path = audit_log_path(base_dir, "testrun")
    assert "risky" not in _executed_action_ids(log_path)
    assert [e["subject"] for e in _system_events(log_path, "risk_declined")] == ["m01_diagnostics/risky"]


def test_moderate_risk_action_accepted_runs_and_logs(qtbot, tmp_path, monkeypatch):
    base_dir = _make_base_dir(tmp_path, MODERATE_ACTIONS_YAML)
    settings = Settings(language="sk", dry_run=False)
    window = MainWindow(assets_dir=base_dir, state_dir=base_dir, settings=settings, is_admin=True, run_id="testrun")
    qtbot.addWidget(window)
    window._action_checkboxes["risky"].setChecked(True)

    # G12: confirmed once on the batch review screen.
    _answer_review(monkeypatch)

    window.run_selected_actions()

    log_path = audit_log_path(base_dir, "testrun")
    # The review's own "_system" entry is written first now.
    qtbot.waitUntil(lambda: "risky" in _executed_action_ids(log_path), timeout=10000)

    assert "risky-ran" in window.console.toPlainText()
    entry = next(e for e in _audit_entries(log_path) if e["module_id"] != "_system")
    assert entry["action_id"] == "risky"
    assert entry["exit_code"] == 0


DESTRUCTIVE_ACTIONS_YAML = """
module_id: m02_cleanup
actions:
  - id: risky_thing
    label_sk: "Riskantna vec"
    label_en: "Risky thing"
    risk: DESTRUCTIVE
    command: "Write-Output 'destructive-ran'"
    preview_command: "Write-Output 'destructive-preview'"
    description_sk: "Test"
    description_en: "Test"
  - id: safe_thing
    label_sk: "Bezpecna vec"
    label_en: "Safe thing"
    risk: SAFE
    command: "Write-Output 'safe-ran'"
    preview_command: "Write-Output 'safe-preview'"
    description_sk: "Test"
    description_en: "Test"
"""


def _make_destructive_base_dir(tmp_path):
    module_dir = tmp_path / "Modules" / "m02_cleanup"
    module_dir.mkdir(parents=True)
    (module_dir / "actions.yaml").write_text(DESTRUCTIVE_ACTIONS_YAML, encoding="utf-8")
    return tmp_path


def test_dry_run_with_preview_command_runs_preview_not_real_command(qtbot, tmp_path):
    base_dir = _make_destructive_base_dir(tmp_path)
    settings = Settings(language="en", dry_run=True)
    window = MainWindow(
        assets_dir=base_dir, state_dir=base_dir, settings=settings, is_admin=True, run_id="run_preview"
    )
    qtbot.addWidget(window)
    window._action_checkboxes["safe_thing"].setChecked(True)

    window.run_selected_actions()

    qtbot.waitUntil(lambda: "safe-preview" in window.console.toPlainText(), timeout=10000)
    assert "safe-ran" not in window.console.toPlainText()
    _wait_batch_idle(qtbot, window)


def test_destructive_action_declined_at_hard_confirm_is_not_run(qtbot, tmp_path, monkeypatch):
    from portablefix import restore_point

    monkeypatch.setattr(restore_point, "create_restore_point", lambda description: (True, ""))
    # G12: confirmed on the review screen without ticking "I understand,
    # irreversible" for the DESTRUCTIVE action - that action is declined.
    _answer_review(monkeypatch, tick=False)

    base_dir = _make_destructive_base_dir(tmp_path)
    settings = Settings(language="en", dry_run=False)
    window = MainWindow(
        assets_dir=base_dir, state_dir=base_dir, settings=settings, is_admin=True, run_id="run_decline"
    )
    qtbot.addWidget(window)
    window._action_checkboxes["risky_thing"].setChecked(True)
    window._action_checkboxes["safe_thing"].setChecked(True)

    window.run_selected_actions()

    from portablefix.audit_log import audit_log_path
    log_path = audit_log_path(base_dir, "run_decline")
    # A synthetic restore_point entry is written first (real execution
    # order), so waiting for "non-empty" alone would resolve before
    # safe_thing has actually run - wait for its specific entry instead.
    qtbot.waitUntil(
        lambda: log_path.exists() and "safe_thing" in log_path.read_text(encoding="utf-8"), timeout=10000
    )

    executed = _executed_action_ids(log_path)
    assert "risky_thing" not in executed
    assert "safe_thing" in executed
    declined = _system_events(log_path, "risk_declined")
    assert len(declined) == 1 and declined[0]["subject"].endswith("/risky_thing")
    assert declined[0]["risk"] == "DESTRUCTIVE"
    assert "confirm" not in declined[0]["warning_text"]  # the real translated copy, not a key
    assert declined[0]["warning_text"]


def test_dry_run_destructive_action_never_creates_restore_point(qtbot, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    from portablefix import restore_point

    def fail_if_called(description):
        raise AssertionError("create_restore_point must not be called in dry-run")

    monkeypatch.setattr(restore_point, "create_restore_point", fail_if_called)
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **kw: QMessageBox.Yes))

    base_dir = _make_destructive_base_dir(tmp_path)
    settings = Settings(language="en", dry_run=True)
    window = MainWindow(
        assets_dir=base_dir, state_dir=base_dir, settings=settings, is_admin=True, run_id="run_dryrun_destructive"
    )
    qtbot.addWidget(window)
    window._action_checkboxes["risky_thing"].setChecked(True)

    window.run_selected_actions()

    qtbot.waitUntil(lambda: "destructive-preview" in window.console.toPlainText(), timeout=10000)
    assert "destructive-ran" not in window.console.toPlainText()
    _wait_batch_idle(qtbot, window)


def test_take_snapshot_measures_system_drive_not_state_dir(qtbot, tmp_path, monkeypatch):
    base_dir = _make_base_dir(tmp_path)
    settings = Settings(language="en", dry_run=False)
    window = MainWindow(
        assets_dir=base_dir, state_dir=base_dir, settings=settings, is_admin=True, run_id="run_snapshot"
    )
    qtbot.addWidget(window)

    captured = {}
    real_disk_usage = shutil.disk_usage  # capture before patching, since it's the same shutil module

    def fake_disk_usage(path):
        captured["path"] = path
        return real_disk_usage(os.environ.get("SystemDrive", "C:") + "\\")

    monkeypatch.setattr("portablefix.gui.main_window.shutil.disk_usage", fake_disk_usage)
    window._take_snapshot()

    assert str(captured["path"]) != str(base_dir)
    assert str(tmp_path) not in str(captured["path"])


def test_run_button_disabled_during_batch_and_reenabled_after(qtbot, tmp_path):
    base_dir = _make_base_dir(tmp_path)
    settings = Settings(language="en", dry_run=False)
    window = MainWindow(
        assets_dir=base_dir, state_dir=base_dir, settings=settings, is_admin=True, run_id="run_button_lock"
    )
    qtbot.addWidget(window)
    window._action_checkboxes["hello"].setChecked(True)

    window.run_selected_actions()
    assert window.run_button.isEnabled() is False
    assert window.progress_bar.isVisibleTo(window) is True
    assert window.progress_bar.maximum() == 1

    qtbot.waitUntil(lambda: window.run_button.isEnabled() is True, timeout=10000)
    assert window.progress_bar.isVisibleTo(window) is False
    assert window.progress_bar.value() == 1


def test_running_a_batch_generates_a_report(qtbot, tmp_path):
    base_dir = _make_base_dir(tmp_path)  # from F1: single "hello" SAFE action, no preview_command
    settings = Settings(language="en", dry_run=False)
    window = MainWindow(
        assets_dir=base_dir, state_dir=base_dir, settings=settings, is_admin=True, run_id="run_report"
    )
    qtbot.addWidget(window)
    window._action_checkboxes["hello"].setChecked(True)

    window.run_selected_actions()

    reports_dir = base_dir / "Reports"
    qtbot.waitUntil(lambda: reports_dir.exists() and any(reports_dir.glob("*.html")), timeout=10000)
    assert any(reports_dir.glob("*.json"))


def test_opening_without_running_anything_generates_no_report(qtbot, tmp_path):
    base_dir = _make_base_dir(tmp_path)
    settings = Settings(language="en", dry_run=False)
    window = MainWindow(
        assets_dir=base_dir, state_dir=base_dir, settings=settings, is_admin=True, run_id="run_none"
    )
    qtbot.addWidget(window)

    window.run_selected_actions()  # nothing checked

    assert not (base_dir / "Reports").exists()


def test_destructive_action_accepted_runs_normally(qtbot, tmp_path, monkeypatch):
    from portablefix import restore_point

    monkeypatch.setattr(restore_point, "create_restore_point", lambda description: (True, ""))
    _answer_review(monkeypatch)  # G12: ticked and confirmed on the review screen

    base_dir = _make_destructive_base_dir(tmp_path)
    settings = Settings(language="en", dry_run=False)
    window = MainWindow(
        assets_dir=base_dir, state_dir=base_dir, settings=settings, is_admin=True, run_id="run_accept"
    )
    qtbot.addWidget(window)
    window._action_checkboxes["risky_thing"].setChecked(True)

    window.run_selected_actions()

    from portablefix.audit_log import audit_log_path
    log_path = audit_log_path(base_dir, "run_accept")
    # The restore_point entry names risky_thing as its subject before the
    # action runs - wait for the action's own entry, not just the string.
    qtbot.waitUntil(lambda: "risky_thing" in _executed_action_ids(log_path), timeout=10000)
    assert "destructive-ran" in window.console.toPlainText()


def test_cancel_during_restore_point_creation_prevents_the_pending_action_from_running(qtbot, tmp_path, monkeypatch):
    # Checkpoint-Computer is a real, slow-ish PowerShell call running on a
    # background QThread - clicking Cancel while it's still in flight must
    # not let the DESTRUCTIVE action it was guarding run anyway once it
    # finishes.
    import time

    from portablefix import restore_point

    def slow_create_restore_point(description):
        time.sleep(0.4)
        return True, ""

    monkeypatch.setattr(restore_point, "create_restore_point", slow_create_restore_point)
    _answer_review(monkeypatch)  # G12: the batch is confirmed on the review screen first

    base_dir = _make_destructive_base_dir(tmp_path)
    settings = Settings(language="en", dry_run=False)
    window = MainWindow(
        assets_dir=base_dir, state_dir=base_dir, settings=settings, is_admin=True, run_id="run_cancel_rp"
    )
    qtbot.addWidget(window)
    window._action_checkboxes["risky_thing"].setChecked(True)

    window.run_selected_actions()
    qtbot.waitUntil(lambda: window._pending_restore_point_runner is not None, timeout=5000)
    assert window._pending_restore_point_runner.isRunning() is True
    window._on_cancel_clicked()

    qtbot.wait(700)
    assert window._runner is None
    assert "destructive-ran" not in window.console.toPlainText()


def test_restore_point_failure_declined_skips_remaining_destructive_but_runs_safe(qtbot, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    from portablefix import restore_point

    monkeypatch.setattr(restore_point, "create_restore_point", lambda description: (False, "restore point failed"))
    # "No" to the restore-point-failed question; the batch itself was
    # confirmed on the G12 review screen.
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **kw: QMessageBox.No))
    _answer_review(monkeypatch)

    base_dir = _make_destructive_base_dir(tmp_path)
    settings = Settings(language="en", dry_run=False)
    window = MainWindow(
        assets_dir=base_dir, state_dir=base_dir, settings=settings, is_admin=True, run_id="run_rpfail"
    )
    qtbot.addWidget(window)
    window._action_checkboxes["risky_thing"].setChecked(True)
    window._action_checkboxes["safe_thing"].setChecked(True)

    window.run_selected_actions()

    from portablefix.audit_log import audit_log_path
    log_path = audit_log_path(base_dir, "run_rpfail")
    qtbot.waitUntil(lambda: log_path.exists() and "safe_thing" in log_path.read_text(encoding="utf-8"), timeout=10000)
    assert "risky_thing" not in _executed_action_ids(log_path)
    assert [e["decision"] for e in _system_events(log_path, "restore_point_decision")] == ["skip"]


def test_category_list_deduplicates_same_category_across_modules(qtbot, tmp_path):
    _write_module(tmp_path, "m01_diagnostics", "DIAGNOSTICS", "a1")
    _write_module(tmp_path, "m02_other", "DIAGNOSTICS", "a2")
    settings = Settings(language="en", dry_run=True)
    window = MainWindow(assets_dir=tmp_path, state_dir=tmp_path, settings=settings, is_admin=True, run_id="run_cat1")
    qtbot.addWidget(window)
    # +1 for the always-present Dashboard (first), +1 for the always-present
    # Uninstaller, +1 for the "Risk: SAFE" tab (both test actions are SAFE).
    assert window.category_list.count() == 4
    assert window.category_list.item(0).text() == "Dashboard"
    assert window.category_list.item(1).text() == "Diagnostics"


def test_category_list_shows_distinct_entries_for_different_categories(qtbot, tmp_path):
    _write_module(tmp_path, "m01_diagnostics", "DIAGNOSTICS", "a1")
    _write_module(tmp_path, "m04_integrity", "REPAIR", "a2")
    settings = Settings(language="en", dry_run=True)
    window = MainWindow(assets_dir=tmp_path, state_dir=tmp_path, settings=settings, is_admin=True, run_id="run_cat2")
    qtbot.addWidget(window)
    # +1 for the always-present Dashboard, +1 for the always-present
    # Uninstaller, +1 for the "Risk: SAFE" tab (both test actions are SAFE).
    assert window.category_list.count() == 5
    labels = {window.category_list.item(i).text() for i in range(window.category_list.count())}
    assert labels == {"Dashboard", "Diagnostics", "System repair", "Uninstall programs", "Risk: SAFE"}


def test_category_click_shows_only_selected_category_group(qtbot, tmp_path):
    from portablefix.models import ModuleCategory

    _write_module(tmp_path, "m01_diagnostics", "DIAGNOSTICS", "a1")
    _write_module(tmp_path, "m04_integrity", "REPAIR", "a2")
    settings = Settings(language="en", dry_run=True)
    window = MainWindow(assets_dir=tmp_path, state_dir=tmp_path, settings=settings, is_admin=True, run_id="run_filter")
    qtbot.addWidget(window)

    # row 0 is the always-present Dashboard by default.
    assert window.category_list.currentRow() == 0
    assert window._category_groups[ModuleCategory.DASHBOARD].isHidden() is False

    diag_row = window._categories_order.index(ModuleCategory.DIAGNOSTICS)
    repair_row = window._categories_order.index(ModuleCategory.REPAIR)

    window.category_list.setCurrentRow(diag_row)
    assert not window._category_groups[ModuleCategory.DIAGNOSTICS].isHidden()
    assert window._category_groups[ModuleCategory.REPAIR].isHidden()

    window.category_list.setCurrentRow(repair_row)
    assert window._category_groups[ModuleCategory.DIAGNOSTICS].isHidden()
    assert not window._category_groups[ModuleCategory.REPAIR].isHidden()


def test_winget_category_builds_dynamic_update_panel_without_crashing(qtbot, tmp_path):
    from PySide6.QtWidgets import QLabel

    from portablefix.models import ModuleCategory

    _write_module(tmp_path, "m20_test", "WINGET", "wtest_action")
    settings = Settings(language="en", dry_run=True)
    window = MainWindow(assets_dir=tmp_path, state_dir=tmp_path, settings=settings, is_admin=True, run_id="run_winget")
    qtbot.addWidget(window)

    # The live update panel lives on the Prehlad/Dashboard card, not the
    # Winget category page (which only keeps the static winget actions).
    assert ModuleCategory.DASHBOARD in window._category_groups
    card = window._category_groups[ModuleCategory.DASHBOARD]

    def scan_settled() -> bool:
        texts = [label.text() for label in card.findChildren(QLabel)]
        return any(
            "No winget updates" in t or "Updates found" in t or t.startswith("winget ") or "winget update check" in t
            for t in texts
        )

    # The background winget scan (real subprocess call) must finish and
    # settle on any outcome - updates, none, or winget unavailable/failed
    # (a machine without winget) - without the window ever crashing.
    qtbot.waitUntil(scan_settled, timeout=20000)


def test_checkbox_state_survives_category_switch(qtbot, tmp_path):
    _write_module(tmp_path, "m01_diagnostics", "DIAGNOSTICS", "a1")
    _write_module(tmp_path, "m04_integrity", "REPAIR", "a2")
    settings = Settings(language="en", dry_run=True)
    window = MainWindow(assets_dir=tmp_path, state_dir=tmp_path, settings=settings, is_admin=True, run_id="run_persist")
    qtbot.addWidget(window)

    window._action_checkboxes["a1"].setChecked(True)
    window.category_list.setCurrentRow(1)
    window.category_list.setCurrentRow(0)
    assert window._action_checkboxes["a1"].isChecked()


def _write_mixed_module(base_dir, module_id, category, safe_id, moderate_id):
    module_dir = base_dir / "Modules" / module_id
    module_dir.mkdir(parents=True)
    (module_dir / "actions.yaml").write_text(
        f"module_id: {module_id}\n"
        f"category: {category}\n"
        "actions:\n"
        f"  - id: {safe_id}\n"
        "    label_sk: \"S\"\n"
        "    label_en: \"S\"\n"
        "    risk: SAFE\n"
        "    command: \"Write-Output 's'\"\n"
        f"  - id: {moderate_id}\n"
        "    label_sk: \"M\"\n"
        "    label_en: \"M\"\n"
        "    risk: MODERATE\n"
        "    command: \"Write-Output 'm'\"\n",
        encoding="utf-8",
    )


def test_global_select_buttons_cover_all_categories(qtbot, tmp_path):
    _write_mixed_module(tmp_path, "m01_diagnostics", "DIAGNOSTICS", "d_safe", "d_mod")
    _write_mixed_module(tmp_path, "m04_integrity", "REPAIR", "r_safe", "r_mod")
    settings = Settings(language="en", dry_run=True)
    window = MainWindow(assets_dir=tmp_path, state_dir=tmp_path, settings=settings, is_admin=True, run_id="run_selall")
    qtbot.addWidget(window)

    window.global_select_all_button.click()
    assert all(cb.isChecked() for cb in window._action_checkboxes.values())

    window.global_select_none_button.click()
    assert not any(cb.isChecked() for cb in window._action_checkboxes.values())

    window.global_select_safe_button.click()
    assert window._action_checkboxes["d_safe"].isChecked()
    assert window._action_checkboxes["r_safe"].isChecked()
    assert not window._action_checkboxes["d_mod"].isChecked()
    assert not window._action_checkboxes["r_mod"].isChecked()


def test_category_select_buttons_affect_only_their_category(qtbot, tmp_path):
    from portablefix.models import ModuleCategory

    _write_mixed_module(tmp_path, "m01_diagnostics", "DIAGNOSTICS", "d_safe", "d_mod")
    _write_mixed_module(tmp_path, "m04_integrity", "REPAIR", "r_safe", "r_mod")
    settings = Settings(language="en", dry_run=True)
    window = MainWindow(assets_dir=tmp_path, state_dir=tmp_path, settings=settings, is_admin=True, run_id="run_selcat")
    qtbot.addWidget(window)

    all_btn, safe_btn, none_btn = window._category_select_buttons[ModuleCategory.DIAGNOSTICS]
    all_btn.click()
    assert window._action_checkboxes["d_safe"].isChecked()
    assert window._action_checkboxes["d_mod"].isChecked()
    assert not window._action_checkboxes["r_safe"].isChecked()
    assert not window._action_checkboxes["r_mod"].isChecked()

    safe_btn2 = window._category_select_buttons[ModuleCategory.REPAIR][1]
    safe_btn2.click()
    assert window._action_checkboxes["r_safe"].isChecked()
    assert not window._action_checkboxes["r_mod"].isChecked()

    none_btn.click()
    assert not window._action_checkboxes["d_safe"].isChecked()
    assert not window._action_checkboxes["d_mod"].isChecked()
    assert window._action_checkboxes["r_safe"].isChecked()


def _write_module_with_excluded_action(base_dir, module_id, category, safe_id, excluded_id):
    module_dir = base_dir / "Modules" / module_id
    module_dir.mkdir(parents=True)
    (module_dir / "actions.yaml").write_text(
        f"module_id: {module_id}\n"
        f"category: {category}\n"
        "actions:\n"
        f"  - id: {safe_id}\n"
        "    label_sk: \"S\"\n"
        "    label_en: \"S\"\n"
        "    risk: SAFE\n"
        "    command: \"Write-Output 's'\"\n"
        f"  - id: {excluded_id}\n"
        "    label_sk: \"R\"\n"
        "    label_en: \"R\"\n"
        "    risk: MODERATE\n"
        "    command: \"Write-Output 'r'\"\n"
        "    exclude_from_select_all: true\n",
        encoding="utf-8",
    )


def test_category_select_all_skips_actions_excluded_from_select_all(qtbot, tmp_path):
    from portablefix.models import ModuleCategory

    _write_module_with_excluded_action(
        tmp_path, "m10_drivers", "DRIVER_UPDATES", "drv_safe", "drv_restore_backup"
    )
    settings = Settings(language="en", dry_run=True)
    window = MainWindow(
        assets_dir=tmp_path, state_dir=tmp_path, settings=settings, is_admin=True, run_id="run_selexcl"
    )
    qtbot.addWidget(window)

    all_btn, _safe_btn, _none_btn = window._category_select_buttons[ModuleCategory.DRIVER_UPDATES]
    all_btn.click()
    assert window._action_checkboxes["drv_safe"].isChecked()
    assert not window._action_checkboxes["drv_restore_backup"].isChecked()

    # Manually checking it still works - the flag only opts it out of the
    # bulk "select all" sweep, not out of selection entirely.
    window._action_checkboxes["drv_restore_backup"].setChecked(True)
    assert window._action_checkboxes["drv_restore_backup"].isChecked()


def test_batch_completion_shows_summary_dialog(qtbot, tmp_path):
    _write_module(tmp_path, "m01_diagnostics", "DIAGNOSTICS", "a1")
    settings = Settings(language="en", dry_run=True)
    window = MainWindow(assets_dir=tmp_path, state_dir=tmp_path, settings=settings, is_admin=True, run_id="run_summary")
    qtbot.addWidget(window)
    window._action_checkboxes["a1"].setChecked(True)

    window.run_selected_actions()

    reports_dir = tmp_path / "Reports"
    qtbot.waitUntil(lambda: reports_dir.exists(), timeout=10000)
    qtbot.waitUntil(lambda: window._summary_dialog is not None, timeout=10000)
    assert window._batch_results == [("a1", 0)]
    assert not window._summary_dialog.isHidden()
    assert window._summary_dialog.windowTitle() == "Batch results"


def test_repair_category_safe_action_triggers_restore_point_and_undo_script(qtbot, tmp_path, monkeypatch):
    from portablefix import restore_point

    captured = {}

    def fake_create_restore_point(description):
        captured["called"] = True
        return True, ""

    monkeypatch.setattr(restore_point, "create_restore_point", fake_create_restore_point)
    _write_module(tmp_path, "m04_integrity", "REPAIR", "safe_repair_action")

    settings = Settings(language="en", dry_run=False)
    window = MainWindow(assets_dir=tmp_path, state_dir=tmp_path, settings=settings, is_admin=True, run_id="run_repair")
    qtbot.addWidget(window)
    window._action_checkboxes["safe_repair_action"].setChecked(True)

    window.run_selected_actions()

    qtbot.waitUntil(lambda: captured.get("called") is True, timeout=10000)
    assert (tmp_path / "Backups" / "run_repair" / "undo.ps1").exists()
    _wait_batch_idle(qtbot, window)


def test_dry_run_repair_action_never_creates_restore_point_or_undo_script(qtbot, tmp_path, monkeypatch):
    from portablefix import restore_point

    def fail_if_called(description):
        raise AssertionError("create_restore_point must not be called in dry-run")

    monkeypatch.setattr(restore_point, "create_restore_point", fail_if_called)
    module_dir = tmp_path / "Modules" / "m04_integrity"
    module_dir.mkdir(parents=True)
    (module_dir / "actions.yaml").write_text(
        "module_id: m04_integrity\n"
        "category: REPAIR\n"
        "actions:\n"
        "  - id: safe_repair_action\n"
        "    label_sk: \"X\"\n"
        "    label_en: \"X\"\n"
        "    risk: SAFE\n"
        "    command: \"Write-Output 'repaired'\"\n"
        "    preview_command: \"Write-Output 'preview'\"\n",
        encoding="utf-8",
    )
    settings = Settings(language="en", dry_run=True)
    window = MainWindow(assets_dir=tmp_path, state_dir=tmp_path, settings=settings, is_admin=True, run_id="run_repair_dry")
    qtbot.addWidget(window)
    window._action_checkboxes["safe_repair_action"].setChecked(True)

    window.run_selected_actions()

    qtbot.waitUntil(lambda: "preview" in window.console.toPlainText(), timeout=10000)
    assert not (tmp_path / "Backups").exists()
    _wait_batch_idle(qtbot, window)


def test_restore_point_failure_declined_skips_remaining_repair_actions_too(qtbot, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    from portablefix import restore_point

    monkeypatch.setattr(restore_point, "create_restore_point", lambda description: (False, "restore point failed"))
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **kw: QMessageBox.No))

    module_dir = tmp_path / "Modules" / "m04_integrity"
    module_dir.mkdir(parents=True)
    (module_dir / "actions.yaml").write_text(
        "module_id: m04_integrity\n"
        "category: REPAIR\n"
        "actions:\n"
        "  - id: repair_action\n"
        "    label_sk: \"X\"\n"
        "    label_en: \"X\"\n"
        "    risk: SAFE\n"
        "    command: \"Write-Output 'repair-ran'\"\n"
        "  - id: other_repair_action\n"
        "    label_sk: \"Y\"\n"
        "    label_en: \"Y\"\n"
        "    risk: SAFE\n"
        "    command: \"Write-Output 'other-ran'\"\n",
        encoding="utf-8",
    )
    settings = Settings(language="en", dry_run=False)
    window = MainWindow(assets_dir=tmp_path, state_dir=tmp_path, settings=settings, is_admin=True, run_id="run_rp_repair_fail")
    qtbot.addWidget(window)
    window._action_checkboxes["repair_action"].setChecked(True)
    window._action_checkboxes["other_repair_action"].setChecked(True)

    window.run_selected_actions()

    reports_dir = tmp_path / "Reports"
    qtbot.waitUntil(lambda: reports_dir.exists(), timeout=10000)
    assert "repair-ran" not in window.console.toPlainText()
    assert "other-ran" not in window.console.toPlainText()


def test_successful_actions_with_undo_command_accumulate_in_undo_script(qtbot, tmp_path, monkeypatch):
    from portablefix import restore_point

    monkeypatch.setattr(restore_point, "create_restore_point", lambda description: (True, ""))

    module_dir = tmp_path / "Modules" / "m05_windows_update"
    module_dir.mkdir(parents=True)
    (module_dir / "actions.yaml").write_text(
        "module_id: m05_windows_update\n"
        "category: REPAIR\n"
        "actions:\n"
        "  - id: step_one\n"
        "    label_sk: \"X\"\n"
        "    label_en: \"X\"\n"
        "    risk: SAFE\n"
        "    command: \"Write-Output 'one'\"\n"
        "    undo_command: \"Write-Output 'undo-one'\"\n"
        "  - id: step_two\n"
        "    label_sk: \"Y\"\n"
        "    label_en: \"Y\"\n"
        "    risk: SAFE\n"
        "    command: \"Write-Output 'two'\"\n"
        "    undo_command: \"Write-Output 'undo-two'\"\n",
        encoding="utf-8",
    )
    settings = Settings(language="en", dry_run=False)
    window = MainWindow(assets_dir=tmp_path, state_dir=tmp_path, settings=settings, is_admin=True, run_id="run_undo_accum")
    qtbot.addWidget(window)
    window._action_checkboxes["step_one"].setChecked(True)
    window._action_checkboxes["step_two"].setChecked(True)

    window.run_selected_actions()

    reports_dir = tmp_path / "Reports"
    qtbot.waitUntil(lambda: reports_dir.exists(), timeout=10000)
    undo_content = (tmp_path / "Backups" / "run_undo_accum" / "undo.ps1").read_text(encoding="utf-8")
    assert "Write-Output 'undo-one'" in undo_content
    assert "Write-Output 'undo-two'" in undo_content
    assert undo_content.index("Write-Output 'undo-two'") < undo_content.index("Write-Output 'undo-one'")


def test_failed_action_with_undo_command_not_added_to_undo_script(qtbot, tmp_path, monkeypatch):
    from portablefix import restore_point

    monkeypatch.setattr(restore_point, "create_restore_point", lambda description: (True, ""))

    module_dir = tmp_path / "Modules" / "m05_windows_update"
    module_dir.mkdir(parents=True)
    (module_dir / "actions.yaml").write_text(
        "module_id: m05_windows_update\n"
        "category: REPAIR\n"
        "actions:\n"
        "  - id: failing_step\n"
        "    label_sk: \"X\"\n"
        "    label_en: \"X\"\n"
        "    risk: SAFE\n"
        "    command: \"exit 1\"\n"
        "    undo_command: \"Write-Output 'should-not-appear'\"\n",
        encoding="utf-8",
    )
    settings = Settings(language="en", dry_run=False)
    window = MainWindow(assets_dir=tmp_path, state_dir=tmp_path, settings=settings, is_admin=True, run_id="run_undo_fail")
    qtbot.addWidget(window)
    window._action_checkboxes["failing_step"].setChecked(True)

    window.run_selected_actions()

    reports_dir = tmp_path / "Reports"
    qtbot.waitUntil(lambda: reports_dir.exists(), timeout=10000)
    undo_content = (tmp_path / "Backups" / "run_undo_fail" / "undo.ps1").read_text(encoding="utf-8")
    assert "should-not-appear" not in undo_content


def test_dry_run_action_with_undo_command_never_creates_backups_dir(qtbot, tmp_path, monkeypatch):
    from portablefix import restore_point

    def fail_if_called(description):
        raise AssertionError("create_restore_point must not be called in dry-run")

    monkeypatch.setattr(restore_point, "create_restore_point", fail_if_called)

    module_dir = tmp_path / "Modules" / "m05_windows_update"
    module_dir.mkdir(parents=True)
    (module_dir / "actions.yaml").write_text(
        "module_id: m05_windows_update\n"
        "category: REPAIR\n"
        "actions:\n"
        "  - id: step_one\n"
        "    label_sk: \"X\"\n"
        "    label_en: \"X\"\n"
        "    risk: SAFE\n"
        "    command: \"Write-Output 'one'\"\n"
        "    undo_command: \"Write-Output 'undo-one'\"\n"
        "    preview_command: \"Write-Output 'preview'\"\n",
        encoding="utf-8",
    )
    settings = Settings(language="en", dry_run=True)
    window = MainWindow(assets_dir=tmp_path, state_dir=tmp_path, settings=settings, is_admin=True, run_id="run_undo_dry")
    qtbot.addWidget(window)
    window._action_checkboxes["step_one"].setChecked(True)

    window.run_selected_actions()

    qtbot.waitUntil(lambda: "preview" in window.console.toPlainText(), timeout=10000)
    assert not (tmp_path / "Backups").exists()
    _wait_batch_idle(qtbot, window)


def test_undo_steps_accumulate_across_batches_in_same_run(qtbot, tmp_path, monkeypatch):
    from portablefix import restore_point

    monkeypatch.setattr(restore_point, "create_restore_point", lambda description: (True, ""))

    module_dir = tmp_path / "Modules" / "m05_windows_update"
    module_dir.mkdir(parents=True)
    (module_dir / "actions.yaml").write_text(
        "module_id: m05_windows_update\n"
        "category: REPAIR\n"
        "actions:\n"
        "  - id: step_one\n"
        "    label_sk: \"X\"\n"
        "    label_en: \"X\"\n"
        "    risk: SAFE\n"
        "    command: \"Write-Output 'one'\"\n"
        "    undo_command: \"Write-Output 'undo-one'\"\n"
        "  - id: step_two\n"
        "    label_sk: \"Y\"\n"
        "    label_en: \"Y\"\n"
        "    risk: SAFE\n"
        "    command: \"Write-Output 'two'\"\n"
        "    undo_command: \"Write-Output 'undo-two'\"\n",
        encoding="utf-8",
    )
    settings = Settings(language="en", dry_run=False)
    window = MainWindow(assets_dir=tmp_path, state_dir=tmp_path, settings=settings, is_admin=True, run_id="run_undo_accum_batches")
    qtbot.addWidget(window)

    window._action_checkboxes["step_one"].setChecked(True)
    window.run_selected_actions()
    # The report is written off the GUI thread; Run re-enables once it's done.
    qtbot.waitUntil(lambda: window.run_button.isEnabled(), timeout=10000)
    assert window._undo_steps == ["Write-Output 'undo-one'"]

    window._action_checkboxes["step_one"].setChecked(False)
    window._action_checkboxes["step_two"].setChecked(True)
    window.run_selected_actions()
    qtbot.waitUntil(
        lambda: window._undo_steps == ["Write-Output 'undo-one'", "Write-Output 'undo-two'"],
        timeout=10000,
    )

    undo_content = (tmp_path / "Backups" / "run_undo_accum_batches" / "undo.ps1").read_text(encoding="utf-8")
    assert "Write-Output 'undo-one'" in undo_content
    assert "Write-Output 'undo-two'" in undo_content
    assert undo_content.index("Write-Output 'undo-two'") < undo_content.index("Write-Output 'undo-one'")


def test_undo_order_uses_real_m05_undo_commands_in_reversed_order(qtbot, tmp_path, monkeypatch):
    from pathlib import Path

    import yaml as yaml_module

    from portablefix import restore_point
    from portablefix.module_engine import load_module

    monkeypatch.setattr(restore_point, "create_restore_point", lambda description: (True, ""))
    # MODERATE-risk actions are confirmed on the batch review screen (G12);
    # auto-confirm so the test doesn't hang on a real modal.
    _answer_review(monkeypatch)

    real_catalog_path = Path(__file__).resolve().parent.parent / "Modules" / "m05_windows_update" / "actions.yaml"
    real_module = load_module(real_catalog_path)
    real_actions = {a.id: a for a in real_module.actions}
    stop_undo = real_actions["wu_stop_services"].undo_command
    reset_undo = real_actions["wu_reset_cache"].undo_command
    assert stop_undo and reset_undo  # sanity: both must exist in the real catalog

    # command: fields are stubbed so this test never executes the real
    # Stop-Service/Rename-Item commands against this machine; undo_command
    # values are taken verbatim (loaded, not hand-typed) from the real
    # catalog to prove the actual shipped undo strings end up correctly
    # (reverse-) ordered.
    fixture = {
        "module_id": "m05_windows_update",
        "category": "REPAIR",
        "actions": [
            {
                "id": "wu_stop_services",
                "label_sk": "X",
                "label_en": "X",
                "risk": "MODERATE",
                "command": "Write-Output 'stubbed-stop'",
                "undo_command": stop_undo,
            },
            {
                "id": "wu_reset_cache",
                "label_sk": "Y",
                "label_en": "Y",
                "risk": "MODERATE",
                "command": "Write-Output 'stubbed-reset'",
                "undo_command": reset_undo,
            },
        ],
    }
    module_dir = tmp_path / "Modules" / "m05_windows_update"
    module_dir.mkdir(parents=True)
    (module_dir / "actions.yaml").write_text(yaml_module.safe_dump(fixture), encoding="utf-8")

    settings = Settings(language="en", dry_run=False)
    window = MainWindow(assets_dir=tmp_path, state_dir=tmp_path, settings=settings, is_admin=True, run_id="run_real_m05_order")
    qtbot.addWidget(window)
    window._action_checkboxes["wu_stop_services"].setChecked(True)
    window._action_checkboxes["wu_reset_cache"].setChecked(True)

    window.run_selected_actions()

    reports_dir = tmp_path / "Reports"
    qtbot.waitUntil(lambda: reports_dir.exists(), timeout=10000)
    undo_content = (tmp_path / "Backups" / "run_real_m05_order" / "undo.ps1").read_text(encoding="utf-8")
    assert undo_content.index(reset_undo) < undo_content.index(stop_undo)


_TWO_ACTIONS_YAML = """
module_id: m01_diagnostics
actions:
  - id: temp_cleanup
    label_sk: "X"
    label_en: "Temp cleanup"
    risk: SAFE
    command: "Write-Output 'a'"
  - id: firewall_check
    label_sk: "X"
    label_en: "Firewall status"
    risk: MODERATE
    command: "Write-Output 'b'"
"""


def test_search_box_filters_action_rows_by_label(qtbot, tmp_path):
    base_dir = _make_base_dir(tmp_path, _TWO_ACTIONS_YAML)
    window = MainWindow(assets_dir=base_dir, state_dir=base_dir, settings=Settings(language="en"), is_admin=True, run_id="run_search")
    qtbot.addWidget(window)

    window.search_box.setText("firewall")

    assert window._action_rows["firewall_check"].isHidden() is False
    assert window._action_rows["temp_cleanup"].isHidden() is True


def test_search_box_matches_action_id_and_command_not_just_label(qtbot, tmp_path):
    # "sfc"/"dism" live in the action id and command (e.g. id "sfc_scannow",
    # command "sfc /scannow"), not in the human-friendly label ("System File
    # Checker (repair)") - label-only search missed these entirely.
    yaml_text = """
module_id: m01_diagnostics
actions:
  - id: sfc_scannow
    label_sk: "X"
    label_en: "System File Checker (repair)"
    risk: MODERATE
    command: "sfc /scannow"
  - id: firewall_check
    label_sk: "X"
    label_en: "Firewall status"
    risk: MODERATE
    command: "Write-Output 'b'"
"""
    base_dir = _make_base_dir(tmp_path, yaml_text)
    window = MainWindow(assets_dir=base_dir, state_dir=base_dir, settings=Settings(language="en"), is_admin=True, run_id="run_search_cmd")
    qtbot.addWidget(window)

    window.search_box.setText("sfc")

    assert window._action_rows["sfc_scannow"].isHidden() is False
    assert window._action_rows["firewall_check"].isHidden() is True


def test_search_box_shows_all_rows_when_cleared(qtbot, tmp_path):
    base_dir = _make_base_dir(tmp_path, _TWO_ACTIONS_YAML)
    window = MainWindow(assets_dir=base_dir, state_dir=base_dir, settings=Settings(language="en"), is_admin=True, run_id="run_search2")
    qtbot.addWidget(window)

    window.search_box.setText("firewall")
    window.search_box.setText("")

    assert window._action_rows["firewall_check"].isHidden() is False
    assert window._action_rows["temp_cleanup"].isHidden() is False


def test_search_box_also_filters_the_risk_tab_view(qtbot, tmp_path):
    base_dir = _make_base_dir(tmp_path, MIXED_RISK_ACTIONS_YAML)
    window = MainWindow(assets_dir=base_dir, state_dir=base_dir, settings=Settings(language="en"), is_admin=True, run_id="run_search_risk")
    qtbot.addWidget(window)

    window.search_box.setText("moderate")

    assert window._risk_view_rows["moderate_one"].isHidden() is False
    assert window._risk_view_rows["safe_one"].isHidden() is True
    assert window._risk_view_rows["destructive_one"].isHidden() is True

    window.search_box.setText("")

    assert window._risk_view_rows["moderate_one"].isHidden() is False
    assert window._risk_view_rows["safe_one"].isHidden() is False


def test_search_box_searches_globally_not_just_the_open_category(qtbot, tmp_path):
    # Searching used to only unhide matching rows inside whichever
    # category/risk card the sidebar currently had open - a match sitting in
    # any other card stayed invisible because _on_category_changed had
    # hidden that whole card. Search must now show every matching card at
    # once regardless of which sidebar row is selected.
    base_dir = _make_base_dir(tmp_path, MIXED_RISK_ACTIONS_YAML)
    window = MainWindow(assets_dir=base_dir, state_dir=base_dir, settings=Settings(language="en"), is_admin=True, run_id="run_search_hint")
    qtbot.addWidget(window)
    window.category_list.setCurrentRow(1)  # SAFE risk tab - only safe_one lives here

    window.search_box.setText("moderate")

    assert "1" in window.statusBar().currentMessage()
    # The card containing the match (a different risk tab than the one
    # selected) must actually be visible, not just counted in the message.
    moderate_card = window._risk_view_rows["moderate_one"]
    while moderate_card.parentWidget() is not None and moderate_card not in window._nav_row_order:
        moderate_card = moderate_card.parentWidget()
    assert moderate_card.isHidden() is False


def test_selecting_a_category_while_searching_keeps_all_cards_visible(qtbot, tmp_path):
    base_dir = _make_base_dir(tmp_path, MIXED_RISK_ACTIONS_YAML)
    window = MainWindow(assets_dir=base_dir, state_dir=base_dir, settings=Settings(language="en"), is_admin=True, run_id="run_search_switch")
    qtbot.addWidget(window)

    window.search_box.setText("moderate")
    window.category_list.setCurrentRow(1)

    for card in window._nav_row_order:
        assert card.isHidden() is False


def test_search_box_shows_no_matches_message(qtbot, tmp_path):
    base_dir = _make_base_dir(tmp_path, MIXED_RISK_ACTIONS_YAML)
    window = MainWindow(assets_dir=base_dir, state_dir=base_dir, settings=Settings(language="en"), is_admin=True, run_id="run_search_none")
    qtbot.addWidget(window)

    window.search_box.setText("zzz_no_such_action")

    assert "No matches" in window.statusBar().currentMessage()


def test_apply_preset_selects_only_ids_present_in_catalog(qtbot, tmp_path):
    from portablefix.gui.main_window import PRESETS

    base_dir = _make_base_dir(tmp_path, _TWO_ACTIONS_YAML)
    window = MainWindow(assets_dir=base_dir, state_dir=base_dir, settings=Settings(language="en"), is_admin=True, run_id="run_preset")
    qtbot.addWidget(window)
    PRESETS["_test_preset"] = ["temp_cleanup", "does_not_exist_in_this_catalog"]

    try:
        window._action_checkboxes["firewall_check"].setChecked(True)
        window._apply_preset("_test_preset")

        assert window._action_checkboxes["temp_cleanup"].isChecked() is True
        assert window._action_checkboxes["firewall_check"].isChecked() is False
    finally:
        del PRESETS["_test_preset"]


def test_preset_button_stays_checked_and_is_mutually_exclusive(qtbot, tmp_path):
    base_dir = _make_base_dir(tmp_path, _TWO_ACTIONS_YAML)
    window = MainWindow(assets_dir=base_dir, state_dir=base_dir, settings=Settings(language="en"), is_admin=True, run_id="run_preset_checked")
    qtbot.addWidget(window)

    window._preset_buttons["quick_clean"].click()

    assert window._preset_buttons["quick_clean"].isChecked() is True

    window._preset_buttons["full_diagnostic"].click()

    assert window._preset_buttons["quick_clean"].isChecked() is False
    assert window._preset_buttons["full_diagnostic"].isChecked() is True


def test_applying_a_preset_switches_the_sidebar_to_the_category_it_selected(qtbot, tmp_path):
    # Applying a preset checked the right boxes correctly but left whichever
    # category the sidebar already happened to be on visible - if that
    # wasn't the category the preset actually touched, the user saw no
    # visible change and had no way to tell anything had been selected.
    from portablefix.gui.main_window import PRESETS
    from portablefix.models import ModuleCategory

    diag_dir = tmp_path / "Modules" / "m01_diagnostics"
    diag_dir.mkdir(parents=True)
    (diag_dir / "actions.yaml").write_text(
        "module_id: m01_diagnostics\ncategory: DIAGNOSTICS\nactions:\n"
        "  - id: diag_one\n    label_sk: X\n    label_en: Diag one\n    risk: SAFE\n    command: \"Write-Output 'a'\"\n",
        encoding="utf-8",
    )
    clean_dir = tmp_path / "Modules" / "m02_cleanup"
    clean_dir.mkdir(parents=True)
    (clean_dir / "actions.yaml").write_text(
        "module_id: m02_cleanup\ncategory: CLEANUP\nactions:\n"
        "  - id: clean_one\n    label_sk: X\n    label_en: Clean one\n    risk: SAFE\n    command: \"Write-Output 'b'\"\n",
        encoding="utf-8",
    )
    window = MainWindow(assets_dir=tmp_path, state_dir=tmp_path, settings=Settings(language="en"), is_admin=True, run_id="run_preset_switch")
    qtbot.addWidget(window)
    PRESETS["_test_preset_switch"] = ["clean_one"]

    try:
        diag_index = window._categories_order.index(ModuleCategory.DIAGNOSTICS)
        cleanup_index = window._categories_order.index(ModuleCategory.CLEANUP)
        window.category_list.setCurrentRow(diag_index)

        window._apply_preset("_test_preset_switch")

        assert window.category_list.currentRow() == cleanup_index
        assert window._category_groups[ModuleCategory.CLEANUP].isHidden() is False
        assert window._category_groups[ModuleCategory.DIAGNOSTICS].isHidden() is True
    finally:
        del PRESETS["_test_preset_switch"]


def test_clearing_selection_unchecks_the_lit_preset_button(qtbot, tmp_path):
    base_dir = _make_base_dir(tmp_path, _TWO_ACTIONS_YAML)
    window = MainWindow(assets_dir=base_dir, state_dir=base_dir, settings=Settings(language="en"), is_admin=True, run_id="run_preset_clear")
    qtbot.addWidget(window)

    window._preset_buttons["quick_clean"].click()
    assert window._preset_buttons["quick_clean"].isChecked() is True
    # quick_clean has no matching ids in this minimal fixture, so nothing
    # actually got checked - check one by hand so the clear-selection
    # button (now disabled with nothing selected) is actually clickable.
    window._action_checkboxes["temp_cleanup"].setChecked(True)

    window.global_select_none_button.click()

    assert window._preset_buttons["quick_clean"].isChecked() is False
    assert all(not cb.isChecked() for cb in window._action_checkboxes.values())


def test_status_bar_shows_selection_count_and_highest_risk(qtbot, tmp_path):
    base_dir = _make_base_dir(tmp_path, _TWO_ACTIONS_YAML)
    window = MainWindow(assets_dir=base_dir, state_dir=base_dir, settings=Settings(language="en"), is_admin=True, run_id="run_statusbar")
    qtbot.addWidget(window)

    assert window.statusBar().currentMessage() == "Nothing selected"

    window._action_checkboxes["temp_cleanup"].setChecked(True)
    assert window.statusBar().currentMessage() == "Selected: 1  |  Highest risk: SAFE"

    window._action_checkboxes["firewall_check"].setChecked(True)
    assert window.statusBar().currentMessage() == "Selected: 2  |  Highest risk: MODERATE"


def test_global_clear_selection_button_only_enabled_when_something_is_selected(qtbot, tmp_path):
    base_dir = _make_base_dir(tmp_path, _TWO_ACTIONS_YAML)
    window = MainWindow(assets_dir=base_dir, state_dir=base_dir, settings=Settings(language="en"), is_admin=True, run_id="run_clear_btn_state")
    qtbot.addWidget(window)

    assert window.global_select_none_button.isEnabled() is False

    window._action_checkboxes["temp_cleanup"].setChecked(True)
    assert window.global_select_none_button.isEnabled() is True

    window.global_select_none_button.click()
    assert window.global_select_none_button.isEnabled() is False


def test_console_fullscreen_toggle_collapses_and_restores_the_body_pane(qtbot, tmp_path):
    base_dir = _make_base_dir(tmp_path)
    window = MainWindow(assets_dir=base_dir, state_dir=base_dir, settings=Settings(), is_admin=True, run_id="run_console_fs")
    qtbot.addWidget(window)
    window.show()
    window.resize(1000, 700)
    original_sizes = window._main_splitter.sizes()
    assert original_sizes[0] > 0

    window.console_fullscreen_button.click()
    assert window._main_splitter.sizes()[0] == 0
    assert window._main_splitter.sizes()[1] > 0

    window.console_fullscreen_button.click()
    assert window._main_splitter.sizes()[0] > 0


def test_console_popout_reparents_console_and_reattaches_on_close(qtbot, tmp_path):
    base_dir = _make_base_dir(tmp_path)
    window = MainWindow(assets_dir=base_dir, state_dir=base_dir, settings=Settings(), is_admin=True, run_id="run_console_popout")
    qtbot.addWidget(window)

    assert window._console_container_layout.indexOf(window.console) != -1

    window.console_popout_button.click()

    assert window._console_window is not None
    assert window._console_container_layout.indexOf(window.console) == -1
    assert window.console.parent() is window._console_window

    window._console_window.close()

    assert window._console_window is None
    assert window._console_container_layout.indexOf(window.console) != -1


def test_update_banner_hidden_by_default(qtbot, tmp_path):
    base_dir = _make_base_dir(tmp_path)
    window = MainWindow(assets_dir=base_dir, state_dir=base_dir, settings=Settings(), is_admin=True, run_id="run_update1")
    qtbot.addWidget(window)
    window.show()  # isVisible() reflects the ancestor chain, so the top-level must be shown (see test_restart_as_admin_button_visibility for the same pattern)
    assert window.update_banner.isVisible() is False


def test_update_banner_shows_when_check_finds_newer_version(qtbot, tmp_path):
    from portablefix.updater import UpdateInfo

    base_dir = _make_base_dir(tmp_path)
    window = MainWindow(assets_dir=base_dir, state_dir=base_dir, settings=Settings(language="en"), is_admin=True, run_id="run_update2")
    qtbot.addWidget(window)
    window.show()  # isVisible() reflects the ancestor chain, so the top-level must be shown (see test_restart_as_admin_button_visibility for the same pattern)

    info = UpdateInfo(version="9.9.9", package_url="https://x", sha256_url=None, notes="")
    window._on_update_check_finished(info)

    assert window.update_banner.isVisible() is True
    assert "9.9.9" in window.update_banner_label.text()


def test_update_banner_check_finished_with_none_stays_hidden(qtbot, tmp_path):
    base_dir = _make_base_dir(tmp_path)
    window = MainWindow(assets_dir=base_dir, state_dir=base_dir, settings=Settings(language="en"), is_admin=True, run_id="run_update3")
    qtbot.addWidget(window)
    window.show()  # isVisible() reflects the ancestor chain, so the top-level must be shown (see test_restart_as_admin_button_visibility for the same pattern)

    window._on_update_check_finished(None)

    assert window.update_banner.isVisible() is False


def test_update_banner_dismiss_hides_it(qtbot, tmp_path):
    from portablefix.updater import UpdateInfo

    base_dir = _make_base_dir(tmp_path)
    window = MainWindow(assets_dir=base_dir, state_dir=base_dir, settings=Settings(language="en"), is_admin=True, run_id="run_update4")
    qtbot.addWidget(window)
    window.show()  # isVisible() reflects the ancestor chain, so the top-level must be shown (see test_restart_as_admin_button_visibility for the same pattern)

    window._on_update_check_finished(UpdateInfo(version="9.9.9", package_url="https://x", sha256_url=None, notes=""))
    window.update_dismiss_button.click()

    assert window.update_banner.isVisible() is False


def test_update_check_skipped_when_not_frozen(qtbot, tmp_path):
    # pytest never runs as a frozen PyInstaller build, so sys.frozen is
    # always falsy here - this proves _start_update_check's own guard,
    # not a monkeypatched substitute for it.
    base_dir = _make_base_dir(tmp_path)
    window = MainWindow(assets_dir=base_dir, state_dir=base_dir, settings=Settings(), is_admin=True, run_id="run_update5")
    qtbot.addWidget(window)
    assert window._update_check_runner is None


def test_update_button_click_declined_confirm_does_not_start_download(qtbot, tmp_path, monkeypatch):
    from portablefix.updater import UpdateInfo

    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.No))
    base_dir = _make_base_dir(tmp_path)
    window = MainWindow(assets_dir=base_dir, state_dir=base_dir, settings=Settings(language="en"), is_admin=True, run_id="run_update6")
    qtbot.addWidget(window)
    window._on_update_check_finished(UpdateInfo(version="9.9.9", package_url="https://x", sha256_url=None, notes=""))

    window.update_button.click()

    assert window._update_download_runner is None


def test_update_button_click_confirmed_downloads_stages_and_hands_off(qtbot, tmp_path, monkeypatch):
    from portablefix.updater import LaunchResult, UpdateInfo

    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Yes))
    calls = _patch_update_flow(monkeypatch, tmp_path, [LaunchResult(ok=True, route="direct")])

    base_dir = _make_base_dir(tmp_path)
    window = MainWindow(assets_dir=base_dir, state_dir=base_dir, settings=Settings(language="en"), is_admin=True, run_id="run_update7")
    qtbot.addWidget(window)
    quit_calls = []
    monkeypatch.setattr(window, "_quit_app", lambda: quit_calls.append(True))
    window._on_update_check_finished(UpdateInfo(version="9.9.9", package_url="https://x", sha256_url=None, notes=""))

    window.update_button.click()

    qtbot.waitUntil(lambda: quit_calls == [True], timeout=5000)
    assert calls["download"] == 1 and calls["stage"] == 1 and calls["launch"] == 1
    assert calls["staged_version"] == "9.9.9"
    assert window._closing_for_update is True


def test_update_download_progress_signal_updates_progress_bar(qtbot, tmp_path, monkeypatch):
    from portablefix.updater import UpdateInfo
    from portablefix.gui import main_window as mw_module
    import threading

    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Yes))
    resume = threading.Event()
    fake_exe = tmp_path / "PortableFix.new.exe"
    fake_exe.write_bytes(b"x")

    def fake_download_update(info, dest, on_progress=None, should_stop=None):
        on_progress(50, 100)
        resume.wait(timeout=5)
        return fake_exe

    monkeypatch.setattr(mw_module.updater, "download_update", fake_download_update)
    # Ends the flow right after the download, so the bar is hidden again.
    monkeypatch.setattr(mw_module.updater, "is_writable", lambda p: False)

    base_dir = _make_base_dir(tmp_path)
    window = MainWindow(assets_dir=base_dir, state_dir=base_dir, settings=Settings(language="en"), is_admin=True, run_id="run_update_progress")
    qtbot.addWidget(window)
    monkeypatch.setattr(window, "_quit_app", lambda: None)
    window._on_update_check_finished(UpdateInfo(version="9.9.9", package_url="https://x", sha256_url=None, notes=""))

    window.update_button.click()

    qtbot.waitUntil(lambda: window.progress_bar.maximum() == 100, timeout=5000)
    assert window.progress_bar.isVisibleTo(window) is True
    assert window.progress_bar.value() == 50

    resume.set()
    qtbot.waitUntil(lambda: window.progress_bar.isVisibleTo(window) is False, timeout=5000)


def test_update_download_failure_shows_error_and_reenables_button(qtbot, tmp_path, monkeypatch):
    from portablefix.updater import UpdateInfo
    from portablefix.gui import main_window as mw_module

    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Yes))

    def raise_it(info, dest, on_progress=None, should_stop=None):
        raise Exception("boom")

    monkeypatch.setattr(mw_module.updater, "download_update", raise_it)

    base_dir = _make_base_dir(tmp_path)
    window = MainWindow(assets_dir=base_dir, state_dir=base_dir, settings=Settings(language="en"), is_admin=True, run_id="run_update8")
    qtbot.addWidget(window)
    window._on_update_check_finished(UpdateInfo(version="9.9.9", package_url="https://x", sha256_url=None, notes=""))

    window.update_button.click()

    qtbot.waitUntil(lambda: window.update_button.isEnabled() is True, timeout=5000)
    assert window.update_banner_label.text() == "Downloading the update failed. Try again later."
    assert window.update_banner_label.toolTip() == "boom"
    assert window.progress_bar.isVisibleTo(window) is False


def test_update_not_writable_shows_error_without_applying(qtbot, tmp_path, monkeypatch):
    from portablefix.updater import UpdateInfo
    from portablefix.gui import main_window as mw_module

    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Yes))
    fake_exe = tmp_path / "PortableFix.new.exe"
    fake_exe.write_bytes(b"x")
    monkeypatch.setattr(mw_module.updater, "download_update", lambda info, dest, **k: fake_exe)
    monkeypatch.setattr(mw_module.updater, "is_writable", lambda p: False)
    applied = {}
    monkeypatch.setattr(mw_module.updater, "stage_update", lambda *a, **k: applied.setdefault("called", True))

    base_dir = _make_base_dir(tmp_path)
    window = MainWindow(assets_dir=base_dir, state_dir=base_dir, settings=Settings(language="en"), is_admin=True, run_id="run_update9")
    qtbot.addWidget(window)
    window._on_update_check_finished(UpdateInfo(version="9.9.9", package_url="https://x", sha256_url=None, notes=""))

    window.update_button.click()

    qtbot.waitUntil(lambda: window.update_banner_label.text() == "The app folder is not writable, the update cannot be applied.", timeout=5000)
    assert applied.get("called") is None


def test_update_needs_admin_shows_elevation_hint_without_applying(qtbot, tmp_path, monkeypatch):
    from portablefix.updater import UpdateInfo
    from portablefix.gui import main_window as mw_module

    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Yes))
    fake_exe = tmp_path / "PortableFix.new.exe"
    fake_exe.write_bytes(b"x")
    monkeypatch.setattr(mw_module.updater, "download_update", lambda info, dest, **k: fake_exe)
    monkeypatch.setattr(mw_module.updater, "is_writable", lambda p: False)
    monkeypatch.setattr(mw_module.updater, "needs_elevation_for_update", lambda p: True)
    applied = {}
    monkeypatch.setattr(mw_module.updater, "stage_update", lambda *a, **k: applied.setdefault("called", True))

    base_dir = _make_base_dir(tmp_path)
    window = MainWindow(assets_dir=base_dir, state_dir=base_dir, settings=Settings(language="en"), is_admin=True, run_id="run_update_needs_admin")
    qtbot.addWidget(window)
    window._on_update_check_finished(UpdateInfo(version="9.9.9", package_url="https://x", sha256_url=None, notes=""))

    window.update_button.click()

    qtbot.waitUntil(
        lambda: window.update_banner_label.text()
        == "The app is installed in a protected folder (e.g. Program Files). Use 'Restart as administrator', then try the update again.",
        timeout=5000,
    )
    assert applied.get("called") is None


def test_language_toggle_mid_download_keeps_buttons_disabled(qtbot, tmp_path):
    from portablefix.updater import UpdateInfo

    base_dir = _make_base_dir(tmp_path)
    window = MainWindow(assets_dir=base_dir, state_dir=base_dir, settings=Settings(language="en"), is_admin=True, run_id="run_toggle_mid_dl")
    qtbot.addWidget(window)
    window._on_update_check_finished(UpdateInfo(version="9.9.9", package_url="https://x", sha256_url=None, notes=""))

    # Simulate a download in progress (mirrors what _on_update_button_clicked
    # sets before starting the QThread) without actually starting one.
    window._update_in_progress = True
    window.update_button.setEnabled(False)
    window.update_dismiss_button.setEnabled(False)

    window._on_toggle_language()

    assert window.update_button.isEnabled() is False
    assert window.update_dismiss_button.isEnabled() is False


def test_language_toggle_mid_batch_restores_run_state_on_the_rebuilt_widgets(qtbot, tmp_path):
    base_dir = _make_base_dir(tmp_path)
    window = MainWindow(assets_dir=base_dir, state_dir=base_dir, settings=Settings(language="en"), is_admin=True, run_id="run_toggle_mid_batch")
    qtbot.addWidget(window)

    # Simulate a batch in progress (mirrors what run_selected_actions sets)
    # without actually starting one, then rebuild via a language toggle.
    window._batch_active = True
    window._queue = ["hello"]
    window._queue_total = 2
    window.run_button.setEnabled(False)
    window.cancel_button.setEnabled(True)
    window.language_button.setEnabled(False)

    window._on_toggle_language()

    assert window.run_button.isEnabled() is False
    assert window.cancel_button.isEnabled() is True
    assert window.language_button.isEnabled() is False
    assert window.progress_bar.isVisibleTo(window) is True
    assert window.progress_bar.maximum() == 2
    assert window.progress_bar.value() == 1
    # The batch above is only simulated - end it so teardown's close
    # doesn't ask "a batch is running, close anyway?".
    window._batch_active = False
    window._queue = []


def test_update_button_click_does_nothing_during_active_batch(qtbot, tmp_path):
    from portablefix.updater import UpdateInfo

    base_dir = _make_base_dir(tmp_path)
    window = MainWindow(assets_dir=base_dir, state_dir=base_dir, settings=Settings(language="en"), is_admin=True, run_id="run_update_batch_guard")
    qtbot.addWidget(window)
    window._on_update_check_finished(UpdateInfo(version="9.9.9", package_url="https://x", sha256_url=None, notes=""))
    window._batch_active = True

    window.update_button.click()

    assert window._update_download_runner is None
    # The batch above is only simulated - end it so teardown's close
    # doesn't ask "a batch is running, close anyway?".
    window._batch_active = False
    window._queue = []


def test_quit_app_routes_through_close_event_and_cancels_a_live_batch_runner(qtbot, tmp_path):
    # _quit_app() used to call QApplication.quit() directly, which bypasses
    # closeEvent entirely - confirming an update restart mid-batch would
    # then leave a live ActionRunner uncancelled and unwaited-on.
    from portablefix.executor import ActionRunner, build_execution_plan

    base_dir = _make_base_dir(tmp_path)
    window = MainWindow(assets_dir=base_dir, state_dir=base_dir, settings=Settings(language="en"), is_admin=True, run_id="run_quit_app_cleanup")
    qtbot.addWidget(window)

    plan = build_execution_plan("Start-Sleep -Seconds 30", dry_run=False)
    runner = ActionRunner(plan, parent=window)
    window._runner = runner
    runner.start()
    qtbot.waitUntil(lambda: runner._process is not None, timeout=5000)

    window._quit_app()

    assert runner._cancel_requested is True
    qtbot.waitUntil(lambda: runner.isFinished(), timeout=5000)


def test_run_selected_actions_does_nothing_while_update_is_in_progress(qtbot, tmp_path):
    # Symmetric to the update-button guard above: starting a batch while an
    # update download is in flight let _quit_app() (confirmed restart) fire
    # mid-batch with no chance for closeEvent's cleanup to run.
    base_dir = _make_base_dir(tmp_path)
    window = MainWindow(assets_dir=base_dir, state_dir=base_dir, settings=Settings(language="en"), is_admin=True, run_id="run_batch_update_guard")
    qtbot.addWidget(window)
    window._action_checkboxes["hello"].setChecked(True)
    window._update_in_progress = True

    window.run_selected_actions()

    assert window._batch_active is False
    assert window._runner is None


def test_update_restart_declined_reverts_banner_and_keeps_the_stage(qtbot, tmp_path, monkeypatch):
    from portablefix.updater import UpdateInfo

    answers = [QMessageBox.Yes, QMessageBox.No]
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: answers.pop(0)))
    calls = _patch_update_flow(monkeypatch, tmp_path, [])

    base_dir = _make_base_dir(tmp_path)
    window = MainWindow(assets_dir=base_dir, state_dir=base_dir, settings=Settings(language="en"), is_admin=True, run_id="run_update10")
    qtbot.addWidget(window)
    monkeypatch.setattr(window, "_quit_app", lambda: calls.__setitem__("quit", True))
    window._on_update_check_finished(UpdateInfo(version="9.9.9", package_url="https://x", sha256_url=None, notes=""))

    window.update_button.click()

    qtbot.waitUntil(lambda: window.update_banner_label.text() == "Version 9.9.9 is available", timeout=5000)
    assert answers == []
    assert calls["launch"] == 0 and "quit" not in calls
    assert window._staged_update is not None
    assert window.update_button.isEnabled() and window.progress_bar.isVisibleTo(window) is False


MIXED_RISK_ACTIONS_YAML = """
module_id: m01_diagnostics
actions:
  - id: safe_one
    label_sk: "Bezpecna"
    label_en: "Safe one"
    risk: SAFE
    command: "Write-Output 'safe'"
    description_sk: "Test"
    description_en: "Test"
  - id: moderate_one
    label_sk: "Riskantna"
    label_en: "Moderate one"
    risk: MODERATE
    command: "Write-Output 'moderate'"
    description_sk: "Test"
    description_en: "Test"
  - id: destructive_one
    label_sk: "Nevratna"
    label_en: "Destructive one"
    risk: DESTRUCTIVE
    command: "Write-Output 'destructive'"
    description_sk: "Test"
    description_en: "Test"
  - id: reboot_one
    label_sk: "Restart"
    label_en: "Reboot one"
    risk: REQUIRES_REBOOT
    command: "Write-Output 'reboot'"
    description_sk: "Test"
    description_en: "Test"
"""


def test_global_select_moderate_destructive_reboot_buttons_select_only_that_risk(qtbot, tmp_path):
    base_dir = _make_base_dir(tmp_path, MIXED_RISK_ACTIONS_YAML)
    window = MainWindow(assets_dir=base_dir, state_dir=base_dir, settings=Settings(language="en"), is_admin=True, run_id="run_risk_select")
    qtbot.addWidget(window)

    window.global_select_moderate_button.click()
    assert [aid for aid, cb in window._action_checkboxes.items() if cb.isChecked()] == ["moderate_one"]

    window.global_select_destructive_button.click()
    assert [aid for aid, cb in window._action_checkboxes.items() if cb.isChecked()] == ["destructive_one"]

    window.global_select_reboot_button.click()
    assert [aid for aid, cb in window._action_checkboxes.items() if cb.isChecked()] == ["reboot_one"]


def test_risk_tabs_are_appended_after_categories_in_nav_list(qtbot, tmp_path):
    base_dir = _make_base_dir(tmp_path, MIXED_RISK_ACTIONS_YAML)
    window = MainWindow(assets_dir=base_dir, state_dir=base_dir, settings=Settings(language="en"), is_admin=True, run_id="run_risk_tabs")
    qtbot.addWidget(window)

    # 1 category (all four test actions default to the same category) + one
    # risk tab per distinct risk level actually present (4 here).
    labels = [window.category_list.item(i).text() for i in range(window.category_list.count())]
    assert labels[-4:] == ["Risk: SAFE", "Risk: MODERATE", "Risk: DESTRUCTIVE", "Risk: REQUIRES_REBOOT"]


def test_clicking_a_risk_tab_shows_only_that_risk_card(qtbot, tmp_path):
    base_dir = _make_base_dir(tmp_path, MIXED_RISK_ACTIONS_YAML)
    window = MainWindow(assets_dir=base_dir, state_dir=base_dir, settings=Settings(language="en"), is_admin=True, run_id="run_risk_click")
    qtbot.addWidget(window)

    category_row_count = window.category_list.count() - 4
    safe_tab_row = category_row_count
    window.category_list.setCurrentRow(safe_tab_row)

    for index, widget in enumerate(window._nav_row_order):
        assert widget.isHidden() == (index != safe_tab_row)


def test_risk_tab_mirror_checkbox_syncs_bidirectionally_with_canonical(qtbot, tmp_path):
    base_dir = _make_base_dir(tmp_path, MIXED_RISK_ACTIONS_YAML)
    window = MainWindow(assets_dir=base_dir, state_dir=base_dir, settings=Settings(language="en"), is_admin=True, run_id="run_risk_mirror")
    qtbot.addWidget(window)

    canonical = window._action_checkboxes["moderate_one"]
    mirror = window._risk_view_checkboxes["moderate_one"]
    assert mirror.isChecked() is False

    canonical.setChecked(True)
    assert mirror.isChecked() is True

    mirror.setChecked(False)
    assert canonical.isChecked() is False


def test_sysinfo_panel_has_a_label_for_every_expected_field(qtbot, tmp_path):
    base_dir = _make_base_dir(tmp_path)
    window = MainWindow(assets_dir=base_dir, state_dir=base_dir, settings=Settings(language="en"), is_admin=True, run_id="run_sysinfo_labels")
    qtbot.addWidget(window)

    expected_keys = {
        "os", "uptime", "cpu_name", "cpu_load", "cpu_clock", "ram", "ram_speed",
        "battery", "disk_health", "gpu_name", "gpu_load", "gpu_temp", "gpu_clock",
        "gpu_vram", "ip", "ping", "vpn",
    }
    assert expected_keys <= set(window._sysinfo_labels.keys())
    assert window.speed_test_button is not None
    assert window.speed_test_result_label is not None


def test_static_info_ready_updates_os_cpu_ip_labels(qtbot, tmp_path):
    from portablefix import sysinfo

    base_dir = _make_base_dir(tmp_path)
    window = MainWindow(assets_dir=base_dir, state_dir=base_dir, settings=Settings(language="en"), is_admin=True, run_id="run_static_info")
    qtbot.addWidget(window)

    window._on_static_info_ready(sysinfo.StaticInfo(
        os_name="Windows 11 Pro", cpu_name="Test CPU", cpu_cores=8,
        local_ip="10.0.0.5", ram_speed_mhz=3200, disk_health_summary="Healthy",
    ))

    assert window._sysinfo_labels["os"].text() == "Windows 11 Pro"
    assert window._sysinfo_labels["cpu_name"].text() == "Test CPU (8 cores)"
    assert window._sysinfo_labels["ram_speed"].text() == "3200 MHz"
    assert window._sysinfo_labels["ip"].text() == "10.0.0.5"
    assert window._sysinfo_labels["disk_health"].text() == "Healthy"


def test_format_uptime_formats_days_hours_minutes():
    assert MainWindow._format_uptime(90) == "0h 1m"
    assert MainWindow._format_uptime(100_000) == "1d 3h 46m"


def test_sysinfo_tick_updates_uptime_and_battery_labels(qtbot, tmp_path, monkeypatch):
    from portablefix.gui import main_window as mw_module

    base_dir = _make_base_dir(tmp_path)
    window = MainWindow(assets_dir=base_dir, state_dir=base_dir, settings=Settings(language="en"), is_admin=True, run_id="run_uptime_battery")
    qtbot.addWidget(window)

    monkeypatch.setattr(mw_module.sysinfo, "get_uptime_seconds", lambda: 100_000)
    monkeypatch.setattr(mw_module.sysinfo, "get_battery_percent", lambda: 77)
    window._on_sysinfo_tick()
    assert window._sysinfo_labels["uptime"].text() == "1d 3h 46m"
    assert window._sysinfo_labels["battery"].text() == "77%"

    monkeypatch.setattr(mw_module.sysinfo, "get_battery_percent", lambda: None)
    window._on_sysinfo_tick()
    assert window._sysinfo_labels["battery"].text() == "N/A"


def test_hw_sensors_ready_updates_cpu_and_gpu_labels(qtbot, tmp_path):
    base_dir = _make_base_dir(tmp_path)
    window = MainWindow(assets_dir=base_dir, state_dir=base_dir, settings=Settings(language="en"), is_admin=True, run_id="run_hw_sensors")
    qtbot.addWidget(window)

    window._on_hw_sensors_ready({
        "cpu_clock_mhz": 4200.0,
        "gpu_name": "Test GPU",
        "gpu_load_percent": 12.0,
        "gpu_temp_c": 55.0,
        "gpu_clock_mhz": 1800.0,
        "gpu_vram_used_gb": 2.0,
        "gpu_vram_total_gb": 8.0,
    })

    assert window._sysinfo_labels["cpu_clock"].text() == "4200 MHz"
    assert window._sysinfo_labels["gpu_name"].text() == "Test GPU"
    assert window._sysinfo_labels["gpu_load"].text() == "12%"
    assert window._sysinfo_labels["gpu_temp"].text() == "55°C"
    assert window._sysinfo_labels["gpu_clock"].text() == "1800 MHz"
    assert window._sysinfo_labels["gpu_vram"].text() == "2.0 / 8.0 GB"


def test_hw_sensors_ready_shows_na_when_sensor_unavailable(qtbot, tmp_path):
    base_dir = _make_base_dir(tmp_path)
    window = MainWindow(assets_dir=base_dir, state_dir=base_dir, settings=Settings(language="en"), is_admin=True, run_id="run_hw_sensors_na")
    qtbot.addWidget(window)

    window._on_hw_sensors_ready({
        "cpu_clock_mhz": None, "gpu_name": None, "gpu_load_percent": None,
        "gpu_temp_c": None, "gpu_clock_mhz": None, "gpu_vram_used_gb": None, "gpu_vram_total_gb": None,
    })

    assert window._sysinfo_labels["cpu_clock"].text() == "N/A"
    assert window._sysinfo_labels["gpu_name"].text() == "N/A"
    assert window._sysinfo_labels["gpu_vram"].text() == "N/A"


def test_ping_ready_updates_ping_label(qtbot, tmp_path):
    base_dir = _make_base_dir(tmp_path)
    window = MainWindow(assets_dir=base_dir, state_dir=base_dir, settings=Settings(language="en"), is_admin=True, run_id="run_ping")
    qtbot.addWidget(window)

    window._on_ping_ready(23.0)
    assert window._sysinfo_labels["ping"].text() == "23 ms"

    window._on_ping_ready(None)
    assert window._sysinfo_labels["ping"].text() == "N/A"


def test_vpn_status_ready_updates_vpn_label(qtbot, tmp_path):
    base_dir = _make_base_dir(tmp_path)
    window = MainWindow(assets_dir=base_dir, state_dir=base_dir, settings=Settings(language="en"), is_admin=True, run_id="run_vpn")
    qtbot.addWidget(window)

    window._on_vpn_status_ready("WireGuard Tunnel")
    assert window._sysinfo_labels["vpn"].text() == "Connected (WireGuard Tunnel)"

    window._on_vpn_status_ready("")
    assert window._sysinfo_labels["vpn"].text() == "Not connected"

    window._on_vpn_status_ready(None)
    assert window._sysinfo_labels["vpn"].text() == "N/A"


def test_sysinfo_labels_render_as_plain_text_not_rich_text(qtbot, tmp_path):
    # These labels show strings sourced from hardware/OS reports (GPU name,
    # VPN adapter name, disk health summary, ...) that an unprivileged local
    # process can name arbitrarily - they must never be interpreted as HTML.
    from PySide6.QtCore import Qt

    base_dir = _make_base_dir(tmp_path)
    window = MainWindow(assets_dir=base_dir, state_dir=base_dir, settings=Settings(language="en"), is_admin=True, run_id="run_plaintext_labels")
    qtbot.addWidget(window)

    for key, label in window._sysinfo_labels.items():
        assert label.textFormat() == Qt.TextFormat.PlainText, key


def test_close_event_cancels_and_waits_on_an_in_flight_batch_runner(qtbot, tmp_path):
    from portablefix.executor import ActionRunner, build_execution_plan

    base_dir = _make_base_dir(tmp_path)
    window = MainWindow(assets_dir=base_dir, state_dir=base_dir, settings=Settings(language="en"), is_admin=True, run_id="run_close_cancel")
    qtbot.addWidget(window)

    plan = build_execution_plan("Start-Sleep -Seconds 30", dry_run=False)
    runner = ActionRunner(plan, parent=window)
    window._runner = runner
    runner.start()
    qtbot.waitUntil(lambda: runner._process is not None, timeout=5000)

    # closeEvent must cancel the still-running action and actually wait for
    # its process to die - not just fire-and-forget, which would either hang
    # the whole app shutdown or destroy the runner mid-flight (a crash risk).
    window.close()

    assert runner._cancel_requested is True
    qtbot.waitUntil(lambda: runner.isFinished(), timeout=5000)


def test_close_event_waits_longer_for_uncancellable_network_runners(qtbot, tmp_path):
    class _FakeRunner:
        def __init__(self):
            self.wait_calls = []
            self.interrupted = False

        def wait(self, *args):
            self.wait_calls.append(args)
            return True

        def requestInterruption(self):
            self.interrupted = True

    base_dir = _make_base_dir(tmp_path)
    window = MainWindow(assets_dir=base_dir, state_dir=base_dir, settings=Settings(language="en"), is_admin=True, run_id="run_close_slow")
    qtbot.addWidget(window)

    # The speed test makes one blocking, uninterruptible network call -
    # closeEvent can't cancel it, so it must wait long enough to cover its
    # real worst-case duration instead of the 5s used for everything else.
    # The update runners stop once interrupted and are waited for without
    # any cap: a capped wait that ran out destroyed a live QThread.
    speed_test_runner = _FakeRunner()
    update_runners = [_FakeRunner(), _FakeRunner(), _FakeRunner()]
    window._speed_test_runner = speed_test_runner
    window._update_download_runner, window._update_stage_runner, window._update_launch_runner = update_runners

    window.close()

    assert speed_test_runner.wait_calls == [(25_000,)]
    for runner in update_runners:
        assert runner.interrupted is True
        assert runner.wait_calls == [()]


def test_presets_only_reference_action_ids_that_exist_in_the_real_catalogs():
    from portablefix.gui.main_window import PRESETS
    from portablefix.module_engine import load_all_modules

    real_modules_dir = Path(__file__).resolve().parent.parent / "Modules"
    modules, errors = load_all_modules(real_modules_dir)
    assert errors == []
    real_ids = {action.id for module in modules for action in module.actions}

    for preset_name, action_ids in PRESETS.items():
        missing = [a for a in action_ids if a not in real_ids]
        assert not missing, f"preset {preset_name!r} references missing action id(s): {missing}"


DETAILED_ACTION_YAML = """
module_id: m01_diagnostics
actions:
  - id: detailed_action
    label_sk: "X"
    label_en: "X"
    risk: SAFE
    command: "Write-Output 'run-me'"
    description_sk: "Popis SK"
    description_en: "Full description EN"
    undo_command: "Write-Output 'undo-me'"
"""


def test_action_detail_panel_starts_hidden_in_both_views(qtbot, tmp_path):
    base_dir = _make_base_dir(tmp_path, DETAILED_ACTION_YAML)
    window = MainWindow(assets_dir=base_dir, state_dir=base_dir, settings=Settings(language="en"), is_admin=True, run_id="run_detail1")
    qtbot.addWidget(window)

    assert window._action_detail_panels["detailed_action"].isHidden() is True
    assert window._risk_view_detail_panels["detailed_action"].isHidden() is True


def test_clicking_detail_toggle_shows_description_command_and_undo_command(qtbot, tmp_path):
    from PySide6.QtWidgets import QLabel, QPlainTextEdit

    base_dir = _make_base_dir(tmp_path, DETAILED_ACTION_YAML)
    window = MainWindow(assets_dir=base_dir, state_dir=base_dir, settings=Settings(language="en"), is_admin=True, run_id="run_detail2")
    qtbot.addWidget(window)

    panel = window._action_detail_panels["detailed_action"]
    window._action_detail_toggles["detailed_action"].click()

    assert panel.isHidden() is False
    label_texts = [w.text() for w in panel.findChildren(QLabel)]
    command_texts = [w.toPlainText() for w in panel.findChildren(QPlainTextEdit)]
    assert "Full description EN" in label_texts
    assert "Write-Output 'run-me'" in command_texts
    assert "Write-Output 'undo-me'" in command_texts


def test_clicking_detail_toggle_again_hides_the_panel(qtbot, tmp_path):
    base_dir = _make_base_dir(tmp_path, DETAILED_ACTION_YAML)
    window = MainWindow(assets_dir=base_dir, state_dir=base_dir, settings=Settings(language="en"), is_admin=True, run_id="run_detail3")
    qtbot.addWidget(window)

    toggle = window._action_detail_toggles["detailed_action"]
    panel = window._action_detail_panels["detailed_action"]

    toggle.click()
    assert panel.isHidden() is False
    toggle.click()
    assert panel.isHidden() is True


def test_detail_panel_omits_undo_section_when_action_has_no_undo_command(qtbot, tmp_path):
    from PySide6.QtWidgets import QPlainTextEdit

    base_dir = _make_base_dir(tmp_path)  # ACTIONS_YAML's "hello" action has no undo_command
    window = MainWindow(assets_dir=base_dir, state_dir=base_dir, settings=Settings(language="en"), is_admin=True, run_id="run_detail4")
    qtbot.addWidget(window)

    panel = window._action_detail_panels["hello"]
    window._action_detail_toggles["hello"].click()

    command_texts = [w.toPlainText() for w in panel.findChildren(QPlainTextEdit)]
    assert command_texts == ["Write-Output 'hello-from-gui-test'"]


def test_expand_state_is_independent_between_category_and_risk_tab_views(qtbot, tmp_path):
    # Expand/collapse is a pure display convenience, not selection state - it
    # deliberately has no two-way sync like the checkboxes do, so expanding
    # one view's row must never affect the other view's row for the same action.
    base_dir = _make_base_dir(tmp_path, DETAILED_ACTION_YAML)
    window = MainWindow(assets_dir=base_dir, state_dir=base_dir, settings=Settings(language="en"), is_admin=True, run_id="run_detail5")
    qtbot.addWidget(window)

    window._action_detail_toggles["detailed_action"].click()

    assert window._action_detail_panels["detailed_action"].isHidden() is False
    assert window._risk_view_detail_panels["detailed_action"].isHidden() is True


def test_close_during_active_batch_prompts_and_can_be_cancelled(qtbot, tmp_path, monkeypatch):
    # Regression test for the closeEvent gap: closing the window mid-batch
    # used to proceed immediately with no prompt, even though a subprocess
    # could still be running.
    base_dir = _make_base_dir(tmp_path)
    window = MainWindow(assets_dir=base_dir, state_dir=base_dir, settings=Settings(), is_admin=True, run_id="run_close_confirm")
    qtbot.addWidget(window)
    window.show()
    window._batch_active = True
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.No))

    window.close()

    assert window.isVisible() is True
    assert window._closed is False


def test_close_during_active_batch_confirmed_closes_normally(qtbot, tmp_path, monkeypatch):
    base_dir = _make_base_dir(tmp_path)
    window = MainWindow(assets_dir=base_dir, state_dir=base_dir, settings=Settings(), is_admin=True, run_id="run_close_confirm_yes")
    qtbot.addWidget(window)
    window.show()
    window._batch_active = True
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Yes))

    window.close()

    assert window._closed is True


def test_batch_summary_shows_space_freed_delta(qtbot, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QLabel

    base_dir = _make_base_dir(tmp_path)
    settings = Settings(language="en", dry_run=False)
    window = MainWindow(assets_dir=base_dir, state_dir=base_dir, settings=settings, is_admin=True, run_id="run_space_delta")
    qtbot.addWidget(window)
    window._action_checkboxes["hello"].setChecked(True)

    snapshots = iter([{"free_gb": 100.0, "total_gb": 200.0}, {"free_gb": 101.5, "total_gb": 200.0}])
    monkeypatch.setattr(window, "_take_snapshot", lambda: next(snapshots))

    window.run_selected_actions()

    qtbot.waitUntil(lambda: window._summary_dialog is not None, timeout=10000)
    texts = [w.text() for w in window._summary_dialog.findChildren(QLabel)]
    assert any("+1.5 GB" in t for t in texts)


def test_batch_summary_shows_before_after_metrics(qtbot, tmp_path):
    from PySide6.QtWidgets import QLabel

    base_dir = _make_base_dir(tmp_path)
    window = MainWindow(assets_dir=base_dir, state_dir=base_dir, settings=Settings(language="en", dry_run=False),
                        is_admin=True, run_id="run_snap_metrics")
    qtbot.addWidget(window)
    window._batch_results = [("hello", 0)]
    window._snapshot_before = {
        "free_gb": 40.0, "temp_user_mb": 3000.0, "temp_user_complete": False,
        "startup_entries": 8, "recycle_bin_mb": 500.0, "mem_available_mb": 4000,
    }
    window._snapshot_after = {
        "free_gb": 43.5, "temp_user_mb": 10.0, "temp_user_complete": True,
        "startup_entries": 9, "recycle_bin_mb": None, "mem_available_mb": 4000,
    }

    window._show_batch_summary(tmp_path / "report.html")

    dialog = window._summary_dialog
    names = [w.text() for w in dialog.findChildren(QLabel, "summaryMetricName")]
    assert names == [
        "Free space on the system drive", "User temporary files (%TEMP%)",
        "Startup programs (Run keys)", "Available memory (RAM)",
    ]  # Recycle Bin unknown after the batch -> omitted
    deltas = {w.text(): w.property("trend") for w in dialog.findChildren(QLabel, "summaryMetricDelta")}
    assert deltas["(+3.5 GB)"] == "good"
    assert deltas["(+1)"] == "bad"
    assert deltas["(0 MB)"] == "same"
    assert any(t.startswith("(≤ −") and trend == "good" for t, trend in deltas.items())
    texts = [w.text() for w in dialog.findChildren(QLabel)]
    assert "40 GB → 43.5 GB" in texts
    assert "≥ 2.93 GB → 10 MB" in texts
    assert window._t("snapshot_lower_bound_note") in texts


def test_batch_summary_without_comparable_metrics_shows_no_metrics(qtbot, tmp_path):
    from PySide6.QtWidgets import QLabel

    base_dir = _make_base_dir(tmp_path)
    window = MainWindow(assets_dir=base_dir, state_dir=base_dir, settings=Settings(language="en", dry_run=False),
                        is_admin=True, run_id="run_snap_empty")
    qtbot.addWidget(window)
    window._batch_results = [("hello", 0)]
    window._snapshot_before = {"free_gb": None, "startup_entries": 3}
    window._snapshot_after = {}

    window._show_batch_summary(tmp_path / "report.html")

    assert window._summary_dialog.findChildren(QLabel, "summaryMetricName") == []


def test_take_snapshot_returns_extended_metrics_without_raising(qtbot, tmp_path):
    base_dir = _make_base_dir(tmp_path)
    window = MainWindow(assets_dir=base_dir, state_dir=base_dir, settings=Settings(language="en"),
                        is_admin=True, run_id="run_snap_keys")
    qtbot.addWidget(window)

    snap = window._take_snapshot()

    for key in ("free_gb", "total_gb", "temp_user_mb", "temp_windows_mb", "recycle_bin_mb",
                "startup_entries", "mem_available_mb"):
        assert key in snap
    json.dumps(snap)


def test_batch_summary_open_undo_script_button_present_when_undo_steps_exist(qtbot, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QPushButton

    from portablefix import restore_point

    # REPAIR-category actions trigger a real System Restore Point attempt
    # (Checkpoint-Computer) before dispatch - must be mocked or this test
    # would hang on/actually invoke a real Windows system call.
    monkeypatch.setattr(restore_point, "create_restore_point", lambda description: (True, ""))

    module_dir = tmp_path / "Modules" / "m05_windows_update"
    module_dir.mkdir(parents=True)
    (module_dir / "actions.yaml").write_text(
        "module_id: m05_windows_update\n"
        "category: REPAIR\n"
        "actions:\n"
        "  - id: step_one\n"
        "    label_sk: \"X\"\n"
        "    label_en: \"X\"\n"
        "    risk: SAFE\n"
        "    command: \"Write-Output 'one'\"\n"
        "    undo_command: \"Write-Output 'undo-one'\"\n",
        encoding="utf-8",
    )
    settings = Settings(language="en", dry_run=False)
    window = MainWindow(assets_dir=tmp_path, state_dir=tmp_path, settings=settings, is_admin=True, run_id="run_undo_button")
    qtbot.addWidget(window)
    window._action_checkboxes["step_one"].setChecked(True)

    window.run_selected_actions()

    qtbot.waitUntil(lambda: window._summary_dialog is not None, timeout=10000)
    buttons = [b.text() for b in window._summary_dialog.findChildren(QPushButton)]
    assert "Open undo script" in buttons
    assert window._undo_script_path is not None
    assert window._undo_script_path.exists()


def test_batch_summary_has_no_open_undo_script_button_when_no_undo_steps(qtbot, tmp_path):
    from PySide6.QtWidgets import QPushButton

    base_dir = _make_base_dir(tmp_path)  # "hello" action has no undo_command
    settings = Settings(language="en", dry_run=False)
    window = MainWindow(assets_dir=base_dir, state_dir=base_dir, settings=settings, is_admin=True, run_id="run_no_undo_button")
    qtbot.addWidget(window)
    window._action_checkboxes["hello"].setChecked(True)

    window.run_selected_actions()

    qtbot.waitUntil(lambda: window._summary_dialog is not None, timeout=10000)
    buttons = [b.text() for b in window._summary_dialog.findChildren(QPushButton)]
    assert "Open undo script" not in buttons


def test_select_all_shortcut_guard_skips_when_search_box_focused(qtbot, tmp_path):
    base_dir = _make_base_dir(tmp_path, _TWO_SAFE_ACTIONS_YAML)
    settings = Settings(language="en")
    window = MainWindow(assets_dir=base_dir, state_dir=base_dir, settings=settings, is_admin=True, run_id="run_shortcut_guard")
    qtbot.addWidget(window)
    window.show()
    qtbot.waitActive(window, timeout=5000)

    window.search_box.setFocus()
    qtbot.waitUntil(lambda: window.search_box.hasFocus(), timeout=5000)
    window._on_select_all_shortcut()
    assert not window._action_checkboxes["first_action"].isChecked()

    window.search_box.clearFocus()
    window.category_list.setFocus()
    qtbot.waitUntil(lambda: window.category_list.hasFocus(), timeout=5000)
    window._on_select_all_shortcut()
    assert window._action_checkboxes["first_action"].isChecked()


def test_f5_shortcut_runs_selected_but_not_while_batch_already_active(qtbot, tmp_path):
    base_dir = _make_base_dir(tmp_path)
    settings = Settings(language="en", dry_run=False)
    window = MainWindow(assets_dir=base_dir, state_dir=base_dir, settings=settings, is_admin=True, run_id="run_f5")
    qtbot.addWidget(window)
    window._action_checkboxes["hello"].setChecked(True)

    # F5 while a batch is already flagged active must not re-enter
    # run_selected_actions - the run_button.setEnabled(False) guard that
    # protects a stray mouse click doesn't stop a keyboard shortcut.
    window._batch_active = True
    window._on_run_shortcut()
    assert window._queue == []
    window._batch_active = False

    window._on_run_shortcut()
    qtbot.waitUntil(lambda: not window._batch_active, timeout=10000)
    assert "hello-from-gui-test" in window.console.toPlainText()


def test_focus_qss_rules_present_for_keyboard_accessibility():
    from portablefix.gui import style

    assert "QPushButton:focus" in style.STYLE
    assert "QListWidget#categoryList::item:focus" in style.STYLE
    assert "QCheckBox::indicator:focus" in style.STYLE


def test_high_contrast_mode_drops_custom_theme_on_window_and_dialogs(qtbot, tmp_path, monkeypatch):
    # research-accessibility.md Finding 3: every place that used to set
    # style.STYLE goes through style.stylesheet(), so High Contrast users
    # get their system colors on the main window *and* its dialogs.
    from portablefix.gui import style

    monkeypatch.setattr(style, "is_high_contrast", lambda: True)
    base_dir = _make_base_dir(tmp_path)
    window = MainWindow(assets_dir=base_dir, state_dir=base_dir, settings=Settings(), is_admin=True, run_id="run_hc")
    qtbot.addWidget(window)
    assert window.styleSheet() == ""

    window.console_popout_button.click()
    assert window._console_window is not None
    assert window._console_window.styleSheet() == ""
    window._console_window.close()


def test_normal_mode_keeps_custom_theme(qtbot, tmp_path, monkeypatch):
    from portablefix.gui import style

    monkeypatch.setattr(style, "is_high_contrast", lambda: False)
    base_dir = _make_base_dir(tmp_path)
    window = MainWindow(assets_dir=base_dir, state_dir=base_dir, settings=Settings(), is_admin=True, run_id="run_nohc")
    qtbot.addWidget(window)
    assert window.styleSheet() == style.STYLE


def test_score_state_buckets():
    from portablefix.gui.main_window import _score_state

    assert _score_state(100) == "good"
    assert _score_state(80) == "good"
    assert _score_state(79) == "warn"
    assert _score_state(60) == "warn"
    assert _score_state(40) == "bad"


def test_dashboard_score_is_neutral_until_analysis_then_colored(qtbot, tmp_path):
    # A big green "not run yet" used to look like a healthy result before
    # anything had been checked; count pills also showed a green "0".
    base_dir = _make_base_dir(tmp_path)
    window = MainWindow(assets_dir=base_dir, state_dir=base_dir, settings=Settings(), is_admin=True, run_id="run_score")
    qtbot.addWidget(window)
    assert window._dashboard_score_label.property("state") == "none"
    assert all(p.property("state") == "idle" for p in window._dashboard_tile_count_labels.values())

    window._recommended_action_ids = {"a", "b", "c"}
    window._refresh_dashboard()
    assert window._dashboard_score_label.text() == "70"
    assert window._dashboard_score_label.property("state") == "warn"
    assert all(p.property("state") in ("ok", "warn") for p in window._dashboard_tile_count_labels.values())


def _two_action_base_dir(tmp_path):
    module_dir = tmp_path / "Modules" / "m01_diagnostics"
    module_dir.mkdir(parents=True)
    (module_dir / "actions.yaml").write_text(
        "module_id: m01_diagnostics\n"
        "actions:\n"
        "  - {id: one, label_sk: Jedna, label_en: One, risk: SAFE, command: \"Write-Output 1\"}\n"
        "  - {id: two, label_sk: Dva, label_en: Two, risk: SAFE, command: \"Write-Output 2\"}\n",
        encoding="utf-8",
    )
    return tmp_path


def test_custom_preset_save_apply_delete_persists(qtbot, tmp_path):
    from portablefix.settings import load_settings

    base_dir = _two_action_base_dir(tmp_path)
    window = MainWindow(assets_dir=base_dir, state_dir=base_dir, settings=Settings(), is_admin=True, run_id="run_preset")
    qtbot.addWidget(window)
    assert not window.save_preset_button.isEnabled()

    window._action_checkboxes["two"].setChecked(True)
    assert window.save_preset_button.isEnabled()
    assert window._save_custom_preset("  Môj servis  ", ["two"]) is True
    assert load_settings(base_dir).custom_presets == {"Môj servis": ["two"]}
    button = window._preset_buttons["custom:Môj servis"]
    assert button.text() == "Môj servis"

    window._apply_selection(list(window._action_checkboxes), "none")
    window._action_checkboxes["one"].setChecked(True)
    button.click()
    assert window._action_checkboxes["two"].isChecked()
    assert not window._action_checkboxes["one"].isChecked()

    window._delete_custom_preset("Môj servis")
    assert "custom:Môj servis" not in window._preset_buttons
    assert load_settings(base_dir).custom_presets == {}


def test_custom_preset_rejects_empty_name_and_respects_overwrite_answer(qtbot, tmp_path, monkeypatch):
    base_dir = _two_action_base_dir(tmp_path)
    window = MainWindow(assets_dir=base_dir, state_dir=base_dir, settings=Settings(), is_admin=True, run_id="run_preset2")
    qtbot.addWidget(window)
    assert window._save_custom_preset("   ", ["one"]) is False
    assert window._save_custom_preset("A", ["one"]) is True
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.No)
    assert window._save_custom_preset("A", ["two"]) is False
    assert window.settings.custom_presets["A"] == ["one"]
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    assert window._save_custom_preset("A", ["two"]) is True
    assert window.settings.custom_presets["A"] == ["two"]


def test_custom_presets_survive_language_toggle(qtbot, tmp_path):
    base_dir = _two_action_base_dir(tmp_path)
    settings = Settings(custom_presets={"Moje": ["one"]})
    window = MainWindow(assets_dir=base_dir, state_dir=base_dir, settings=settings, is_admin=True, run_id="run_preset3")
    qtbot.addWidget(window)
    assert "custom:Moje" in window._preset_buttons
    window._on_toggle_language()
    assert "custom:Moje" in window._preset_buttons


def test_console_line_count_is_capped(qtbot, tmp_path):
    from portablefix.gui.main_window import CONSOLE_MAX_LINES

    base_dir = _two_action_base_dir(tmp_path)
    window = MainWindow(assets_dir=base_dir, state_dir=base_dir, settings=Settings(), is_admin=True, run_id="run_console")
    qtbot.addWidget(window)
    assert window.console.maximumBlockCount() == CONSOLE_MAX_LINES


def test_dashboard_history_lists_past_runs(qtbot, tmp_path):
    import socket

    base_dir = _two_action_base_dir(tmp_path)
    reports = base_dir / "Reports"
    reports.mkdir()
    data = {"run_id": "20260924T100000-abcd", "generated_at": "2026-09-24T10:00:00+00:00",
            "actions": [{"exit_code": 1, "dry_run": False}]}
    (reports / f"{socket.gethostname()}_20260924T100000-abcd.json").write_text(json.dumps(data), encoding="utf-8")
    window = MainWindow(assets_dir=base_dir, state_dir=base_dir, settings=Settings(language="en"), is_admin=True, run_id="run_hist")
    qtbot.addWidget(window)
    rows = [window._history_layout.itemAt(i).widget() for i in range(window._history_layout.count())]
    assert len(rows) == 1
    assert rows[0].property("failed") is True


def test_job_details_are_kept_and_technician_persisted(qtbot, tmp_path):
    from portablefix.settings import load_settings

    base_dir = _two_action_base_dir(tmp_path)
    window = MainWindow(assets_dir=base_dir, state_dir=base_dir, settings=Settings(), is_admin=True, run_id="run_job")
    qtbot.addWidget(window)
    assert window.job_button.text() == "Zákazka…"
    window._set_job("  Ján Technik ", "Firma s.r.o. #1234", "Pomalý štart, vírus?")
    assert window._job_info() == {
        "technician": "Ján Technik",
        "client": "Firma s.r.o. #1234",
        "note": "Pomalý štart, vírus?",
    }
    assert window.job_button.text() == "Zákazka: Firma s.r.o. #1234"
    assert load_settings(base_dir).technician_name == "Ján Technik"
    # Survives the full UI rebuild of a language toggle.
    window._on_toggle_language()
    assert window.job_button.text() == "Job: Firma s.r.o. #1234"


def test_job_dialog_accept_applies_values(qtbot, tmp_path):
    from PySide6.QtWidgets import QLineEdit, QPlainTextEdit

    base_dir = _two_action_base_dir(tmp_path)
    window = MainWindow(assets_dir=base_dir, state_dir=base_dir, settings=Settings(), is_admin=True, run_id="run_job2")
    qtbot.addWidget(window)
    window._open_job_dialog()
    dialog = window._job_dialog
    edits = dialog.findChildren(QLineEdit)
    edits[0].setText("Eva")
    edits[1].setText("Klient X")
    dialog.findChild(QPlainTextEdit).setPlainText("poznámka")
    dialog.accept()
    assert window._job_info() == {"technician": "Eva", "client": "Klient X", "note": "poznámka"}


def test_ctrl_f_focuses_search_and_esc_clears_it(qtbot, tmp_path):
    from PySide6.QtCore import Qt

    base_dir = _two_action_base_dir(tmp_path)
    window = MainWindow(assets_dir=base_dir, state_dir=base_dir, settings=Settings(), is_admin=True, run_id="run_keys")
    qtbot.addWidget(window)
    window.show()
    qtbot.waitExposed(window)
    window.activateWindow()
    window._on_search_shortcut()
    # Offscreen windows never become active, so check the window's focus
    # child rather than application focus.
    assert window.focusWidget() is window.search_box
    window.search_box.setText("abc")
    qtbot.keyClick(window.search_box, Qt.Key.Key_Escape)
    assert window.search_box.text() == ""


def test_batch_finished_notification_is_silent_when_window_is_active(qtbot, tmp_path, monkeypatch):
    base_dir = _two_action_base_dir(tmp_path)
    window = MainWindow(assets_dir=base_dir, state_dir=base_dir, settings=Settings(), is_admin=True, run_id="run_notify")
    qtbot.addWidget(window)
    alerts = []
    monkeypatch.setattr(QApplication, "alert", lambda *a: alerts.append(a))
    monkeypatch.setattr(window, "isActiveWindow", lambda: True)
    window._notify_batch_finished()
    assert alerts == []
    monkeypatch.setattr(window, "isActiveWindow", lambda: False)
    window._batch_results = [("one", 0), ("two", 1)]
    window._notify_batch_finished()
    assert len(alerts) == 1


class _FakeSignal:
    def __init__(self):
        self._slots = []

    def connect(self, slot):
        self._slots.append(slot)

    def emit(self, *args):
        for slot in self._slots:
            slot(*args)


class _FakeRunner:
    """Stands in for ActionRunner so audit-trail wiring can be tested
    without a real powershell.exe (finishes synchronously with exit 0)."""

    def __init__(self, plan, parent=None, **kwargs):
        self.output_line = _FakeSignal()
        self.finished_with_code = _FakeSignal()
        self.captured_output = ["fake-output"]

    def start(self):
        self.finished_with_code.emit(0)

    def cancel(self):
        pass

    def wait(self, *args):
        return True


def _destructive_window(qtbot, tmp_path, monkeypatch, run_id, language="en"):
    base_dir = _make_destructive_base_dir(tmp_path)
    window = MainWindow(
        assets_dir=base_dir, state_dir=base_dir, settings=Settings(language=language, dry_run=False),
        is_admin=True, run_id=run_id,
    )
    qtbot.addWidget(window)
    # Tests below drive a single step directly - keep the batch loop (and
    # its end-of-batch report/snapshot) out of it.
    monkeypatch.setattr(window, "_run_next", lambda: None)
    return window, base_dir


def test_declined_destructive_confirmation_is_logged_with_exact_warning_text(qtbot, tmp_path, monkeypatch):
    # research-reporting.md F2: a "No" to the risk warning is evidence too.
    shown = []
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **kw: shown.append(a[2]) or QMessageBox.No))
    window, base_dir = _destructive_window(qtbot, tmp_path, monkeypatch, "run_decl_log")
    module, action = window._find_action("risky_thing")

    window._dispatch_action(module, action)

    log_path = audit_log_path(base_dir, "run_decl_log")
    assert _executed_action_ids(log_path) == []
    [event] = _system_events(log_path, "risk_declined")
    assert event["subject"] == "m02_cleanup/risky_thing"
    assert event["decision"] == "declined"
    assert event["warned"] is True
    assert event["risk"] == "DESTRUCTIVE"
    assert event["warning_text"] == shown[0]
    assert event["exit_code"] is None


def test_accepted_confirmation_records_the_warning_shown_in_the_action_entry(qtbot, tmp_path, monkeypatch):
    from portablefix.gui import main_window as mw

    shown = []
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **kw: shown.append(a[2]) or QMessageBox.Yes))
    monkeypatch.setattr(mw, "ActionRunner", _FakeRunner)
    window, base_dir = _destructive_window(qtbot, tmp_path, monkeypatch, "run_warn_ok")
    module, action = window._find_action("risky_thing")

    window._dispatch_action(module, action)

    [entry] = [e for e in _audit_entries(audit_log_path(base_dir, "run_warn_ok")) if e["action_id"] == "risky_thing"]
    assert entry["warned"] is True
    assert entry["warning_text"] == shown[0]
    assert "Risky thing" in entry["warning_text"]
    assert entry["risk"] == "DESTRUCTIVE"


def test_safe_action_is_logged_as_not_warned(qtbot, tmp_path, monkeypatch):
    from portablefix.gui import main_window as mw

    monkeypatch.setattr(mw, "ActionRunner", _FakeRunner)
    window, base_dir = _destructive_window(qtbot, tmp_path, monkeypatch, "run_safe_nowarn")
    module, action = window._find_action("safe_thing")

    window._dispatch_action(module, action)

    [entry] = _audit_entries(audit_log_path(base_dir, "run_safe_nowarn"))
    assert entry["warned"] is False and entry["warning_text"] == ""


def test_restore_point_failure_and_proceed_decision_are_logged(qtbot, tmp_path, monkeypatch):
    # research-reporting.md F1 + F3.
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **kw: QMessageBox.Yes))
    window, base_dir = _destructive_window(qtbot, tmp_path, monkeypatch, "run_rp_proceed")
    dispatched = []
    monkeypatch.setattr(window, "_dispatch_action", lambda m, a: dispatched.append(a.id))
    module, action = window._find_action("risky_thing")

    window._on_restore_point_checked(False, "System Restore is disabled", module, action)

    log_path = audit_log_path(base_dir, "run_rp_proceed")
    [rp] = _system_events(log_path, "restore_point")
    assert rp["exit_code"] == 1
    assert "System Restore is disabled" in rp["output"]
    assert rp["subject"] == "m02_cleanup/risky_thing"
    assert rp["elevated"] is True
    [decision] = _system_events(log_path, "restore_point_decision")
    assert decision["decision"] == "proceed"
    assert decision["warning_text"]
    assert dispatched == ["risky_thing"]


def test_restore_point_failure_declined_logs_skip_decision(qtbot, tmp_path, monkeypatch):
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **kw: QMessageBox.No))
    window, base_dir = _destructive_window(qtbot, tmp_path, monkeypatch, "run_rp_skip")
    dispatched = []
    monkeypatch.setattr(window, "_dispatch_action", lambda m, a: dispatched.append(a.id))
    module, action = window._find_action("risky_thing")

    window._on_restore_point_checked(False, "", module, action)

    [decision] = _system_events(audit_log_path(base_dir, "run_rp_skip"), "restore_point_decision")
    assert decision["decision"] == "skip"
    assert dispatched == []


def test_successful_restore_point_logs_no_decision(qtbot, tmp_path, monkeypatch):
    window, base_dir = _destructive_window(qtbot, tmp_path, monkeypatch, "run_rp_ok")
    monkeypatch.setattr(window, "_dispatch_action", lambda m, a: None)
    module, action = window._find_action("risky_thing")

    window._on_restore_point_checked(True, "", module, action)

    log_path = audit_log_path(base_dir, "run_rp_ok")
    assert [e["exit_code"] for e in _system_events(log_path, "restore_point")] == [0]
    assert _system_events(log_path, "restore_point_decision") == []


def test_restore_point_sequence_flows_from_runner_into_audit_log(qtbot, tmp_path, monkeypatch):
    # research-reporting.md F1: the real RestorePointRunner signal carries the
    # created point's SequenceNumber through to the "_system" audit entry.
    from portablefix import restore_point

    monkeypatch.setattr(
        restore_point, "create_restore_point",
        lambda description: restore_point.RestorePointResult(
            True, "", {"sequence_number": 123, "creation_time": "20260924101530.123456-000"},
        ),
    )
    _write_module(tmp_path, "m04_integrity", "REPAIR", "safe_repair_action")
    window = MainWindow(
        assets_dir=tmp_path, state_dir=tmp_path, settings=Settings(language="en", dry_run=False),
        is_admin=True, run_id="run_rp_seq",
    )
    qtbot.addWidget(window)
    window._action_checkboxes["safe_repair_action"].setChecked(True)
    dispatched = []
    monkeypatch.setattr(window, "_dispatch_action", lambda m, a: dispatched.append(a.id))
    # Only the restore-point step matters here: no real disk snapshot
    # (SystemDrive doesn't exist off Windows) ...
    monkeypatch.setattr(window, "_take_snapshot", lambda: {})

    window.run_selected_actions()

    qtbot.waitUntil(lambda: dispatched == ["safe_repair_action"], timeout=10000)
    # ... and the stubbed dispatch never finishes the batch - end it so
    # teardown's closeEvent doesn't ask about a running batch.
    window._batch_active = False
    [rp] = _system_events(audit_log_path(tmp_path, "run_rp_seq"), "restore_point")
    assert rp["exit_code"] == 0
    assert rp["restore_point_sequence"] == 123
    assert rp["restore_point_created"] == "20260924101530.123456-000"
    assert "#123" in rp["output"]


def test_restore_point_without_info_logs_no_sequence(qtbot, tmp_path, monkeypatch):
    # Lookup failed (or an older caller passes no info) - still "created".
    window, base_dir = _destructive_window(qtbot, tmp_path, monkeypatch, "run_rp_noseq")
    monkeypatch.setattr(window, "_dispatch_action", lambda m, a: None)
    module, action = window._find_action("risky_thing")

    window._on_restore_point_checked(True, "", module, action, {})

    [rp] = _system_events(audit_log_path(base_dir, "run_rp_noseq"), "restore_point")
    assert rp["exit_code"] == 0
    assert rp["restore_point_sequence"] is None
    assert rp["output"] == "System Restore Point created."


def test_irreversible_action_is_listed_in_undo_script(qtbot, tmp_path, monkeypatch):
    # research-reporting.md F9: undo.ps1 must not imply a DESTRUCTIVE change
    # without an undo_command was reversible.
    from types import SimpleNamespace

    window, base_dir = _destructive_window(qtbot, tmp_path, monkeypatch, "run_irrev")
    runner = SimpleNamespace(captured_output=["done"])

    window._on_action_finished("m02_cleanup", "safe_thing", "cmd", 0, runner)
    window._on_action_finished("m02_cleanup", "risky_thing", "cmd", 0, runner, "warned")

    content = (base_dir / "Backups" / "run_irrev" / "undo.ps1").read_text(encoding="utf-8")
    assert "# NOT reversible" in content
    assert "[DESTRUCTIVE] Risky thing (risky_thing)" in content
    assert "safe_thing" not in content
    assert "# full report:" in content


def test_dry_run_irreversible_action_writes_no_undo_script(qtbot, tmp_path, monkeypatch):
    from types import SimpleNamespace

    base_dir = _make_destructive_base_dir(tmp_path)
    window = MainWindow(
        assets_dir=base_dir, state_dir=base_dir, settings=Settings(language="en", dry_run=True),
        is_admin=True, run_id="run_irrev_dry",
    )
    qtbot.addWidget(window)
    monkeypatch.setattr(window, "_run_next", lambda: None)

    window._on_action_finished("m02_cleanup", "risky_thing", "cmd", 0, SimpleNamespace(captured_output=[]))

    assert not (base_dir / "Backups").exists()


def test_report_is_flagged_when_state_dir_is_the_temp_fallback(qtbot, tmp_path, monkeypatch):
    # research-reporting.md F4: main.py passes different assets/state dirs
    # only when the USB wasn't writable.
    from portablefix import report

    usb = tmp_path / "usb"
    usb.mkdir()
    assets_dir = _make_base_dir(usb)
    state_dir = tmp_path / "temp_fallback"
    state_dir.mkdir()
    window = MainWindow(assets_dir=assets_dir, state_dir=state_dir, settings=Settings(language="en"), is_admin=True, run_id="run_fb")
    qtbot.addWidget(window)
    captured = {}

    def fake_generate_report(*args, **kwargs):
        captured.update(kwargs)
        raise OSError("stop here")

    monkeypatch.setattr(report, "generate_report", fake_generate_report)
    monkeypatch.setattr(window, "_take_snapshot", lambda: {})
    window._batch_active = True
    window._queue = []
    window._run_next()

    qtbot.waitUntil(lambda: window._report_runner is None, timeout=10000)
    assert captured["storage_fallback"] is True
    same = MainWindow(assets_dir=assets_dir, state_dir=assets_dir, settings=Settings(language="en"), is_admin=True, run_id="run_nofb")
    qtbot.addWidget(same)
    assert same._storage_fallback is False


def test_action_accessible_name_is_translated(qtbot, tmp_path):
    base_dir = _make_base_dir(tmp_path, _TWO_SAFE_ACTIONS_YAML)
    window = MainWindow(assets_dir=base_dir, state_dir=base_dir, settings=Settings(language="sk"), is_admin=True, run_id="run_a11y_sk")
    qtbot.addWidget(window)

    name = window._action_checkboxes["first_action"].accessibleName()
    assert "riziko: SAFE" in name
    assert "risk:" not in name


def test_dashboard_tiles_are_keyboard_reachable_and_named(qtbot, tmp_path):
    from PySide6.QtCore import Qt

    from portablefix.models import ModuleCategory

    _write_module(tmp_path, "m01_diagnostics", "DIAGNOSTICS", "diag_action")
    _write_module(tmp_path, "m02_cleanup", "CLEANUP", "clean_action")
    window = MainWindow(assets_dir=tmp_path, state_dir=tmp_path, settings=Settings(language="en"), is_admin=True, run_id="run_tiles")
    qtbot.addWidget(window)

    tile = window._dashboard_tiles[ModuleCategory.CLEANUP]
    assert tile.focusPolicy() & Qt.FocusPolicy.TabFocus
    assert "Cleanup" in tile.accessibleName()
    assert "1 actions" in tile.accessibleName()
    assert tile.accessibleDescription()

    qtbot.keyClick(tile, Qt.Key.Key_Space)
    assert window.category_list.currentRow() == window._categories_order.index(ModuleCategory.CLEANUP)
    window.category_list.setCurrentRow(0)
    qtbot.keyClick(tile, Qt.Key.Key_Return)
    assert window.category_list.currentRow() == window._categories_order.index(ModuleCategory.CLEANUP)


def test_dashboard_tile_accessible_name_follows_recommended_count(qtbot, tmp_path):
    from portablefix.models import ModuleCategory

    _write_module(tmp_path, "m02_cleanup", "CLEANUP", "clean_action")
    window = MainWindow(assets_dir=tmp_path, state_dir=tmp_path, settings=Settings(language="en"), is_admin=True, run_id="run_tiles2")
    qtbot.addWidget(window)
    window._recommended_action_ids = {"clean_action"}

    window._refresh_dashboard()

    assert "1 recommended fixes" in window._dashboard_tiles[ModuleCategory.CLEANUP].accessibleName()


def test_batch_summary_dialog_focuses_open_report_button(qtbot, tmp_path):
    # research-accessibility.md Finding 7.
    base_dir = _make_base_dir(tmp_path)
    window = MainWindow(assets_dir=base_dir, state_dir=base_dir, settings=Settings(language="en"), is_admin=True, run_id="run_sum_focus")
    qtbot.addWidget(window)
    window._batch_results = [("hello", 0)]

    window._show_batch_summary(tmp_path / "report.html")

    dialog = window._summary_dialog
    focused = dialog.focusWidget()
    assert focused is not None and focused.text() == window._t("open_report")
    assert focused.isDefault()


def test_batch_progress_is_announced_to_screen_readers(qtbot, tmp_path, monkeypatch):
    # research-accessibility.md Finding 4.
    from portablefix.gui import main_window as mw

    announced = []
    monkeypatch.setattr(mw, "_announce_to_screen_reader", lambda widget, text: announced.append(text))
    base_dir = _make_base_dir(tmp_path)
    window = MainWindow(assets_dir=base_dir, state_dir=base_dir, settings=Settings(language="en"), is_admin=True, run_id="run_announce")
    qtbot.addWidget(window)
    monkeypatch.setattr(window, "_dispatch_action", lambda m, a: None)
    monkeypatch.setattr(window, "_take_snapshot", lambda: {})
    window._action_checkboxes["hello"].setChecked(True)

    window.run_selected_actions()

    assert announced == ["Running 1/1: Greeting"]

    # End of batch: the outcome is announced too, once the report exists.
    from portablefix import report

    monkeypatch.setattr(report, "generate_report", lambda *a, **kw: (tmp_path / "r.html", tmp_path / "r.json"))
    monkeypatch.setattr(window, "_show_batch_summary", lambda path: None)
    window._batch_results = [("hello", 0)]
    window._run_next()
    qtbot.waitUntil(lambda: window._report_runner is None, timeout=10000)
    assert announced[-1] == window._t("batch_done_message").format(ok=1, failed=0)
    assert window._batch_active is False


def test_announce_to_screen_reader_is_safe_to_call(qtbot, tmp_path):
    from portablefix.gui import main_window as mw

    base_dir = _make_base_dir(tmp_path)
    window = MainWindow(assets_dir=base_dir, state_dir=base_dir, settings=Settings(), is_admin=True, run_id="run_announce2")
    qtbot.addWidget(window)
    mw._announce_to_screen_reader(window, "text")  # must not raise on any Qt version
    mw._announce_to_screen_reader(window, "")


def test_style_muted_text_meets_wcag_aa_contrast():
    # research-accessibility.md §3 (contrast): 9pt muted labels and the idle
    # dashboard count must reach 4.5:1 on the card/button surfaces.
    import re

    from portablefix.gui import style

    def luminance(hex_color):
        channels = [int(hex_color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
        lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
        return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]

    def contrast(a, b):
        hi, lo = sorted((luminance(a), luminance(b)), reverse=True)
        return (hi + 0.05) / (lo + 0.05)

    for selector in ("QLabel#selectionScope", 'QLabel#countPill[state="idle"]', "QLabel#actionDetailLabel"):
        block = style.STYLE.split(selector + " {", 1)[1].split("}", 1)[0]
        color = re.search(r"\bcolor:\s*(#[0-9a-fA-F]{6})", block).group(1)
        for surface in ("#10141c", "#141a24", "#0b0e14"):
            assert contrast(color, surface) >= 4.5, (selector, color, surface)
    assert "QToolButton:focus" in style.STYLE
    assert 'QFrame#actionCard[tile="true"]:focus' in style.STYLE


def _report_window(qtbot, tmp_path, monkeypatch, run_id):
    base_dir = _make_base_dir(tmp_path)
    window = MainWindow(assets_dir=base_dir, state_dir=base_dir, settings=Settings(language="en"), is_admin=True, run_id=run_id)
    qtbot.addWidget(window)
    monkeypatch.setattr(window, "_take_snapshot", lambda: {})
    # What run_selected_actions leaves behind right before the last action ends.
    window._batch_active = True
    window.run_button.setEnabled(False)
    window.language_button.setEnabled(False)
    window._queue = []
    window._batch_results = [("hello", 0)]
    return window


def test_batch_end_report_is_generated_off_the_gui_thread_then_summary_follows(qtbot, tmp_path, monkeypatch):
    # research-app-performance.md 4.3: the report used to be written on the
    # GUI thread, freezing the window at every batch end.
    import threading

    from portablefix import report

    window = _report_window(qtbot, tmp_path, monkeypatch, "run_report_thread")
    release = threading.Event()
    report_threads = []
    order = []

    def slow_generate_report(*args, **kwargs):
        report_threads.append(threading.current_thread())
        release.wait(5)
        return tmp_path / "r.html", tmp_path / "r.json"

    monkeypatch.setattr(report, "generate_report", slow_generate_report)
    real_refresh = window._refresh_dashboard
    monkeypatch.setattr(window, "_refresh_dashboard", lambda: order.append("dashboard") or real_refresh())
    monkeypatch.setattr(window, "_notify_batch_finished", lambda: order.append("notify"))
    monkeypatch.setattr(window, "_show_batch_summary", lambda path: order.append(("summary", path)))

    window._run_next()

    # _run_next returned while the report is still being written - the GUI
    # thread is free, and nothing that needs the report has happened yet.
    assert window._batch_active is False
    assert order == []
    assert window.run_button.isEnabled() is False
    assert window.language_button.isEnabled() is False
    release.set()
    qtbot.waitUntil(lambda: window._report_runner is None, timeout=10000)

    assert report_threads and report_threads[0] is not threading.main_thread()
    assert order == ["dashboard", "notify", ("summary", tmp_path / "r.html")]
    assert window.run_button.isEnabled() is True
    assert window.language_button.isEnabled() is True


def test_batch_end_report_write_failure_still_finishes_the_batch(qtbot, tmp_path, monkeypatch):
    from portablefix import report

    window = _report_window(qtbot, tmp_path, monkeypatch, "run_report_oserror")
    order = []

    def failing_generate_report(*args, **kwargs):
        raise OSError("USB unplugged")

    monkeypatch.setattr(report, "generate_report", failing_generate_report)
    monkeypatch.setattr(window, "_notify_batch_finished", lambda: order.append("notify"))
    monkeypatch.setattr(window, "_show_batch_summary", lambda path: order.append("summary"))

    window._run_next()
    qtbot.waitUntil(lambda: window._report_runner is None, timeout=10000)

    assert window.console.toPlainText().count(window._t("disk_write_failed")) == 1
    # No report, so no "report is ready" summary - but the batch still ends.
    assert order == ["notify"]
    assert window.run_button.isEnabled() is True


def test_new_batch_is_refused_while_previous_report_is_being_written(qtbot, tmp_path, monkeypatch):
    # A second batch would append to the audit log the report thread is
    # reading and race it for the same report files.
    window = _report_window(qtbot, tmp_path, monkeypatch, "run_report_busy")
    window._batch_active = False
    window._report_runner = object()
    window._action_checkboxes["hello"].setChecked(True)

    window.run_selected_actions()
    window._on_run_shortcut()

    assert window._batch_active is False
    assert window._queue == []
    window._report_runner = None


def test_close_event_waits_for_an_in_flight_report_runner(qtbot, tmp_path, monkeypatch):
    import threading
    import time

    from portablefix import report

    window = _report_window(qtbot, tmp_path, monkeypatch, "run_report_close")
    started = threading.Event()
    done = threading.Event()

    def slow_generate_report(*args, **kwargs):
        started.set()
        time.sleep(0.5)
        done.set()
        return tmp_path / "r.html", tmp_path / "r.json"

    monkeypatch.setattr(report, "generate_report", slow_generate_report)
    monkeypatch.setattr(window, "_show_batch_summary", lambda path: pytest.fail("summary after close"))
    window._run_next()
    assert started.wait(5)

    window.close()

    # closeEvent returned only once the thread finished (Qt aborts the
    # process if a running QThread is destroyed with its parent).
    assert done.is_set()
    qtbot.waitUntil(lambda: window._report_runner is None, timeout=10000)


def test_batch_ending_after_close_writes_the_report_without_a_thread(qtbot, tmp_path, monkeypatch):
    # closeEvent has already waited on every runner by then - a new thread
    # could outlive the window.
    import threading

    from portablefix import report

    window = _report_window(qtbot, tmp_path, monkeypatch, "run_report_closed")
    calls = []
    monkeypatch.setattr(
        report, "generate_report",
        lambda *a, **kw: calls.append(threading.current_thread()) or (tmp_path / "r.html", tmp_path / "r.json"),
    )
    monkeypatch.setattr(window, "_show_batch_summary", lambda path: pytest.fail("summary after close"))
    window._closed = True

    window._run_next()

    assert calls == [threading.main_thread()]
    assert window._report_runner is None


_UNDO_PAIR_YAML = """
module_id: m05_windows_update
category: REPAIR
actions:
  - id: step_one
    label_sk: "X"
    label_en: "X"
    risk: SAFE
    command: "Write-Output 'one'"
    undo_command: "Write-Output 'undo-one'"
  - id: step_two
    label_sk: "Y"
    label_en: "Y"
    risk: SAFE
    command: "Write-Output 'two'"
    undo_command: "Write-Output 'undo-two'"
"""


def test_undo_script_is_only_rewritten_when_its_content_changes(qtbot, tmp_path, monkeypatch):
    # research-app-performance.md 4.2: every later batch's pre-restore-point
    # write used to rewrite an identical undo.ps1.
    from types import SimpleNamespace

    from portablefix import undo

    module_dir = tmp_path / "Modules" / "m05_windows_update"
    module_dir.mkdir(parents=True)
    (module_dir / "actions.yaml").write_text(_UNDO_PAIR_YAML, encoding="utf-8")
    window = MainWindow(assets_dir=tmp_path, state_dir=tmp_path, settings=Settings(language="en", dry_run=False), is_admin=True, run_id="run_undo_cheap")
    qtbot.addWidget(window)
    monkeypatch.setattr(window, "_run_next", lambda: None)
    writes = []
    real_create = undo.create_undo_script
    monkeypatch.setattr(undo, "create_undo_script", lambda *a, **kw: writes.append(1) or real_create(*a, **kw))
    undo_path = tmp_path / "Backups" / "run_undo_cheap" / "undo.ps1"
    runner = SimpleNamespace(captured_output=[])

    window._write_undo_script()
    window._write_undo_script()
    assert len(writes) == 1 and undo_path.exists()

    # Still written after every successful action, newest step first, so a
    # crash mid-batch leaves a correct script.
    window._on_action_finished("m05_windows_update", "step_one", "cmd", 0, runner)
    assert len(writes) == 2
    assert "Write-Output 'undo-one'" in undo_path.read_text(encoding="utf-8-sig")
    window._on_action_finished("m05_windows_update", "step_two", "cmd", 0, runner)
    content = undo_path.read_text(encoding="utf-8-sig")
    assert content.index("Write-Output 'undo-two'") < content.index("Write-Output 'undo-one'")
    assert content.startswith("# PortableFix undo script")
    assert "# full report:" in content

    window._write_undo_script()  # next batch's pre-restore-point write
    assert len(writes) == 3  # unchanged -> skipped

    undo_path.unlink()
    window._write_undo_script()
    assert len(writes) == 4 and undo_path.exists()


def test_close_event_waits_long_enough_for_a_running_restore_point(qtbot, tmp_path):
    from portablefix import restore_point

    base_dir = _two_action_base_dir(tmp_path)
    window = MainWindow(assets_dir=base_dir, state_dir=base_dir, settings=Settings(), is_admin=True, run_id="run_rp_close")
    qtbot.addWidget(window)
    waits = []

    class _FakeRunner:
        def wait(self, timeout_ms):
            waits.append(timeout_ms)
            return True

    window._pending_restore_point_runner = _FakeRunner()
    window.close()
    assert waits == [restore_point.RESTORE_POINT_TIMEOUT_SEC * 1000 + 5_000]


def _fake_running_rp_runner():
    from PySide6.QtCore import QObject, Signal

    class _Runner(QObject):
        finished = Signal()

        def __init__(self):
            super().__init__()
            self.running = True
            self.waits = []

        def isRunning(self):
            return self.running

        def wait(self, timeout_ms):
            self.waits.append(timeout_ms)
            return True

    return _Runner()


def test_close_while_restore_point_runs_waits_without_blocking_then_closes(qtbot, tmp_path):
    # Blocking closeEvent for up to ~5 min froze the window; instead the
    # close is deferred, the batch cancelled, and the window closes itself
    # once the restore point has finished.
    base_dir = _two_action_base_dir(tmp_path)
    window = MainWindow(assets_dir=base_dir, state_dir=base_dir, settings=Settings(language="en"), is_admin=True, run_id="run_rp_defer")
    qtbot.addWidget(window)
    window.show()
    runner = _fake_running_rp_runner()
    window._pending_restore_point_runner = runner
    window._queue = ["one", "two"]

    window.close()

    assert window.isVisible()
    assert window._cancel_requested is True
    assert window._queue == []
    assert "restore point" in window.statusBar().currentMessage()

    runner.running = False
    runner.finished.emit()

    assert not window.isVisible()
    assert window._closed is True


def test_restore_point_result_after_close_never_dispatches_the_guarded_action(qtbot, tmp_path, monkeypatch):
    # Regression: closing during Checkpoint-Computer used to let the
    # restore point's result dispatch the guarded repair action with no
    # window left - unlogged, outliving the app.
    base_dir = _two_action_base_dir(tmp_path)
    window = MainWindow(assets_dir=base_dir, state_dir=base_dir, settings=Settings(language="en"), is_admin=True, run_id="run_rp_after_close")
    qtbot.addWidget(window)
    window.show()
    monkeypatch.setattr(window, "_dispatch_action", lambda *a, **k: pytest.fail("dispatched after close"))
    questions = []
    monkeypatch.setattr(
        QMessageBox, "question", staticmethod(lambda *a, **k: questions.append(a) or QMessageBox.StandardButton.Yes)
    )
    runner = _fake_running_rp_runner()
    window._pending_restore_point_runner = runner
    window._batch_active = True
    window._queue = ["two"]
    module, action = window._find_action("one")

    window.close()  # deferred: batch cancelled
    window._on_restore_point_checked(True, "", module, action)
    runner.running = False
    runner.finished.emit()

    _wait_batch_idle(qtbot, window)
    assert not window.isVisible()
    # Asked once ("batch running, close anyway?"), not again on the real close.
    assert len(questions) == 1


def _handoff_buttons(container):
    from PySide6.QtWidgets import QPushButton

    return [b for b in container.findChildren(QPushButton) if b.text() == "Save client package"]


def test_batch_summary_handoff_button_saves_client_package(qtbot, tmp_path, monkeypatch):
    import socket
    import zipfile

    from PySide6.QtWidgets import QFileDialog

    base_dir = _make_base_dir(tmp_path)
    window = MainWindow(assets_dir=base_dir, state_dir=base_dir, settings=Settings(language="en"), is_admin=True, run_id="run_handoff")
    qtbot.addWidget(window)
    window._action_checkboxes["hello"].setChecked(True)
    window.run_selected_actions()
    qtbot.waitUntil(lambda: window._summary_dialog is not None, timeout=15000)
    _wait_batch_idle(qtbot, window)

    buttons = _handoff_buttons(window._summary_dialog)
    assert len(buttons) == 1
    offered = []
    dest = tmp_path / "out" / "package.zip"

    def _fake_save(parent, title, default, filters):
        offered.append(default)
        return str(dest), filters

    monkeypatch.setattr(QFileDialog, "getSaveFileName", _fake_save)
    buttons[0].click()

    expected_name = f"PortableFix_{socket.gethostname()}_run_handoff.zip"
    assert offered == [str(base_dir / "Reports" / expected_name)]
    with zipfile.ZipFile(dest) as zf:
        names = set(zf.namelist())
    assert {"README.txt", "report.html", "report.json", "audit_log.jsonl"} <= names
    # isHidden, not isVisible - the main window itself is never shown here.
    assert not window._handoff_folder_button.isHidden()
    assert window._handoff_folder_button.property("folder") == str(dest.parent)
    assert "package.zip" in window.statusBar().currentMessage()

    from PySide6.QtGui import QDesktopServices

    opened = []
    monkeypatch.setattr(QDesktopServices, "openUrl", lambda url: opened.append(url.toLocalFile()))
    window._handoff_folder_button.click()
    # QUrl.toLocalFile() uses forward slashes on Windows - compare as paths.
    assert [Path(p) for p in opened] == [dest.parent]
    assert window._handoff_folder_button.isHidden()


def test_dashboard_history_row_has_handoff_button(qtbot, tmp_path, monkeypatch):
    import socket
    import zipfile

    from PySide6.QtWidgets import QFileDialog

    base_dir = _two_action_base_dir(tmp_path)
    run_id = "20260924T100000-abcd"
    host = socket.gethostname()
    reports = base_dir / "Reports"
    reports.mkdir()
    data = {"run_id": run_id, "generated_at": "2026-09-24T10:00:00+00:00", "actions": [{"exit_code": 0, "dry_run": False}]}
    (reports / f"{host}_{run_id}.json").write_text(json.dumps(data), encoding="utf-8")
    (reports / f"{host}_{run_id}.html").write_text("<html></html>", encoding="utf-8")
    (base_dir / "Logs").mkdir(exist_ok=True)
    (base_dir / "Logs" / f"{run_id}.jsonl").write_text("{}\n", encoding="utf-8")
    window = MainWindow(assets_dir=base_dir, state_dir=base_dir, settings=Settings(language="en"), is_admin=True, run_id="run_hist_handoff")
    qtbot.addWidget(window)
    rows = [window._history_layout.itemAt(i).widget() for i in range(window._history_layout.count())]
    assert len(rows) == 1
    buttons = _handoff_buttons(rows[0])
    assert len(buttons) == 1 and buttons[0].property("handoffRunId") == run_id

    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda parent, title, default, filters: (default, filters))
    buttons[0].click()

    package = reports / f"PortableFix_{host}_{run_id}.zip"
    with zipfile.ZipFile(package) as zf:
        assert sorted(zf.namelist()) == ["README.txt", "audit_log.jsonl", "report.html", "report.json"]


def test_handoff_cancelled_save_dialog_writes_nothing(qtbot, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog

    base_dir = _make_base_dir(tmp_path)
    window = MainWindow(assets_dir=base_dir, state_dir=base_dir, settings=Settings(language="en"), is_admin=True, run_id="run_handoff_cancel")
    qtbot.addWidget(window)
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: ("", ""))
    assert window._save_handoff_package("run_handoff_cancel") is None
    assert not list(base_dir.rglob("*.zip"))


def test_handoff_write_failure_shows_disk_write_message(qtbot, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog

    from portablefix import handoff

    base_dir = _make_base_dir(tmp_path)
    window = MainWindow(assets_dir=base_dir, state_dir=base_dir, settings=Settings(language="en"), is_admin=True, run_id="run_handoff_fail")
    qtbot.addWidget(window)
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(tmp_path / "x.zip"), ""))

    def _boom(*args, **kwargs):
        raise OSError("USB gone")

    monkeypatch.setattr(handoff, "build_handoff_zip", _boom)
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda parent, title, text, *a: warnings.append(text))

    assert window._save_handoff_package("run_handoff_fail") is None
    assert len(warnings) == 1 and window._t("handoff_failed") in warnings[0] and "USB gone" in warnings[0]
    assert window._t("handoff_failed") in window.console.toPlainText()
    assert not (tmp_path / "x.zip").exists()


def _fake_installed_program(name, quiet=None, plain="uninst.exe /x", location=None, registry_path=None):
    from portablefix import uninstaller

    return uninstaller.InstalledProgram(
        name=name, publisher="", version="1.0", estimated_size_kb=None, install_location=location,
        install_date=None, uninstall_string=plain, quiet_uninstall_string=quiet, display_icon=None,
        registry_hive=uninstaller.winreg.HKEY_LOCAL_MACHINE,
        registry_path=registry_path or rf"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\{name}",
    )


def _uninstaller_window(qtbot, tmp_path, monkeypatch, run_id, programs, dry_run, orphans=()):
    # The card reads the registry once, when it is built - fake it first.
    # No real winget scan either: on a Windows runner that takes seconds.
    from portablefix import uninstaller, winget_updates
    from portablefix.models import ModuleCategory

    monkeypatch.setattr(uninstaller, "list_installed_programs", lambda *a, **k: list(programs))
    monkeypatch.setattr(uninstaller, "find_orphaned_uninstall_entries", lambda *a, **k: list(orphans))
    monkeypatch.setattr(winget_updates, "list_outdated_packages", lambda: [])
    base_dir = _make_base_dir(tmp_path)
    window = MainWindow(
        assets_dir=base_dir, state_dir=base_dir, settings=Settings(language="en", dry_run=dry_run),
        is_admin=True, run_id=run_id,
    )
    qtbot.addWidget(window)
    return window, window._category_groups[ModuleCategory.UNINSTALLER]


def _panel_button(container, text):
    from PySide6.QtWidgets import QPushButton

    return next(b for b in container.findChildren(QPushButton) if b.text() == text)


def _panel_checkbox(container, prefix):
    from PySide6.QtWidgets import QCheckBox

    return next(cb for cb in container.findChildren(QCheckBox) if cb.text().startswith(prefix))


def _panel_console_text(container) -> str:
    from PySide6.QtWidgets import QPlainTextEdit

    return "\n".join(w.toPlainText() for w in container.findChildren(QPlainTextEdit) if w.objectName() == "console")


def _refuse_runner(label):
    def _raise(*args, **kwargs):
        raise AssertionError(f"{label} must not be started")

    return _raise


def test_uninstaller_dry_run_previews_the_command_and_logs_without_uninstalling(qtbot, tmp_path, monkeypatch):
    from portablefix import uninstaller

    program = _fake_installed_program("Ghost Tool", quiet="ghost-uninst.exe /S")
    calls = []
    monkeypatch.setattr(uninstaller, "uninstall_program", lambda p, *a, **k: calls.append(p) or (True, ""))
    monkeypatch.setattr(uninstaller, "UninstallRunner", _refuse_runner("UninstallRunner"))
    window, card = _uninstaller_window(qtbot, tmp_path, monkeypatch, "run_uninst_dry", [program], dry_run=True)

    _panel_checkbox(card, "Ghost Tool").setChecked(True)
    _panel_button(card, window._t("uninstaller_uninstall_button")).click()

    assert calls == []
    console_text = _panel_console_text(card)
    assert "[DRY-RUN]" in console_text and "ghost-uninst.exe /S" in console_text
    entries = [e for e in _audit_entries(audit_log_path(tmp_path, "run_uninst_dry")) if e["module_id"] == "_uninstaller"]
    assert len(entries) == 1
    assert entries[0]["dry_run"] is True
    assert entries[0]["action_id"] == "Ghost Tool"
    assert entries[0]["command"] == "ghost-uninst.exe /S"
    assert entries[0]["risk"] == "DESTRUCTIVE"
    # Nothing was uninstalled, so the row stays.
    assert _panel_checkbox(card, "Ghost Tool").isChecked()


def test_uninstaller_declined_confirmation_uninstalls_nothing_and_logs_the_decline(qtbot, tmp_path, monkeypatch):
    from portablefix import uninstaller

    silent = _fake_installed_program("Silent App", quiet="silent.exe /S")
    loud = _fake_installed_program("Loud App")
    calls = []
    monkeypatch.setattr(uninstaller, "uninstall_program", lambda p, *a, **k: calls.append(p) or (True, ""))
    monkeypatch.setattr(uninstaller, "UninstallRunner", _refuse_runner("UninstallRunner"))
    window, card = _uninstaller_window(qtbot, tmp_path, monkeypatch, "run_uninst_no", [silent, loud], dry_run=False)
    shown = []
    monkeypatch.setattr(QMessageBox, "warning", lambda parent, title, text, *a: shown.append(text) or QMessageBox.No)

    _panel_checkbox(card, "Silent App").setChecked(True)
    _panel_checkbox(card, "Loud App").setChecked(True)
    _panel_button(card, window._t("uninstaller_uninstall_button")).click()

    assert calls == []
    assert len(shown) == 1
    # The dialog says which uninstall runs silently (no uninstaller window).
    marker = window._t("uninstaller_confirm_silent_marker")
    lines = shown[0].splitlines()
    assert any("Silent App" in line and marker in line for line in lines)
    assert any("Loud App" in line and marker not in line for line in lines)
    log_path = audit_log_path(tmp_path, "run_uninst_no")
    declined = _system_events(log_path, "risk_declined")
    assert sorted(e["subject"] for e in declined) == ["_uninstaller/Loud App", "_uninstaller/Silent App"]
    assert all(
        e["decision"] == "declined" and e["warned"] is True and e["warning_text"] == shown[0] and e["risk"] == "DESTRUCTIVE"
        for e in declined
    )
    assert not [e for e in _audit_entries(log_path) if e["module_id"] == "_uninstaller"]


def test_uninstaller_confirmed_run_logs_each_program_as_warned_and_irreversible(qtbot, tmp_path, monkeypatch):
    from portablefix import uninstaller
    from portablefix.gui.main_window import _thread_running

    program = _fake_installed_program("Real App", plain="realapp-uninst.exe")
    monkeypatch.setattr(uninstaller, "uninstall_program", lambda p, *a, **k: (True, "ok"))
    window, card = _uninstaller_window(qtbot, tmp_path, monkeypatch, "run_uninst_yes", [program], dry_run=False)
    shown = []
    monkeypatch.setattr(QMessageBox, "warning", lambda parent, title, text, *a: shown.append(text) or QMessageBox.Yes)

    _panel_checkbox(card, "Real App").setChecked(True)
    button = _panel_button(card, window._t("uninstaller_uninstall_button"))
    button.click()

    log_path = audit_log_path(tmp_path, "run_uninst_yes")

    def uninstall_logged() -> bool:
        return button.isEnabled() and any(e["module_id"] == "_uninstaller" for e in _audit_entries(log_path))

    qtbot.waitUntil(uninstall_logged, timeout=10000)
    qtbot.waitUntil(lambda: not _thread_running(window._uninstall_runner), timeout=10000)
    entries = [e for e in _audit_entries(log_path) if e["module_id"] == "_uninstaller"]
    assert len(entries) == 1
    entry = entries[0]
    assert entry["action_id"] == "Real App"
    assert entry["command"] == "realapp-uninst.exe"
    assert entry["exit_code"] == 0
    assert entry["warned"] is True and entry["warning_text"] == shown[0]
    assert entry["dry_run"] is False and entry["risk"] == "DESTRUCTIVE"
    undo_text = (tmp_path / "Backups" / "run_uninst_yes" / "undo.ps1").read_text(encoding="utf-8-sig")
    assert "NOT reversible" in undo_text and "Real App" in undo_text


def test_orphan_cleanup_dry_run_starts_unchecked_and_deletes_nothing(qtbot, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QCheckBox

    from portablefix import uninstaller

    program = _fake_installed_program("Some App", quiet="someapp.exe /S")
    # Same DisplayName in HKLM and WOW6432Node: the rows must be keyed by
    # registry location (the dataclass itself is unhashable - TypeError).
    orphans = [
        _fake_installed_program("Old Tool", location=r"C:\Gone\OldTool"),
        _fake_installed_program(
            "Old Tool", location=r"C:\Gone\OldTool",
            registry_path=r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\Old Tool",
        ),
    ]
    removed, backed_up = [], []
    monkeypatch.setattr(uninstaller, "remove_registry_key", lambda *a: removed.append(a) or True)
    monkeypatch.setattr(uninstaller, "backup_registry_key", lambda *a, **k: backed_up.append(a) or True)
    window, card = _uninstaller_window(
        qtbot, tmp_path, monkeypatch, "run_orphan_dry", [program], dry_run=True, orphans=orphans,
    )

    _panel_checkbox(card, "Some App").setChecked(True)
    _panel_button(card, window._t("uninstaller_uninstall_button")).click()

    orphan_boxes = [cb for cb in card.findChildren(QCheckBox) if cb.text() == "Old Tool"]
    assert len(orphan_boxes) == 2
    assert not any(cb.isChecked() for cb in orphan_boxes)
    clean_button = _panel_button(card, window._t("uninstaller_clean_leftovers_button"))
    log_path = audit_log_path(tmp_path, "run_orphan_dry")

    clean_button.click()  # nothing ticked - nothing happens
    assert not [e for e in _audit_entries(log_path) if e["action_id"].startswith("orphan_cleanup:")]

    orphan_boxes[0].setChecked(True)
    clean_button.click()

    assert removed == [] and backed_up == []
    assert "[DRY-RUN] reg delete" in _panel_console_text(card)
    entries = [e for e in _audit_entries(log_path) if e["action_id"] == "orphan_cleanup:Old Tool"]
    assert len(entries) == 1
    assert entries[0]["module_id"] == "_uninstaller" and entries[0]["dry_run"] is True


def test_orphan_cleanup_deletes_only_entries_whose_registry_backup_succeeded(qtbot, tmp_path, monkeypatch):
    from portablefix import uninstaller

    program = _fake_installed_program("Some App", quiet="someapp.exe /S")
    orphans = [
        _fake_installed_program("Tool Unbackupable", location=r"C:\Gone\A"),
        _fake_installed_program("Tool Backed Up", location=r"C:\Gone\B"),
    ]
    removed = []

    def fake_backup(hive, path, dest_file):
        if "Unbackupable" in path:
            return False
        Path(dest_file).parent.mkdir(parents=True, exist_ok=True)
        Path(dest_file).write_text("REGEDIT", encoding="utf-8")
        return True

    monkeypatch.setattr(uninstaller, "backup_registry_key", fake_backup)
    monkeypatch.setattr(uninstaller, "remove_registry_key", lambda hive, path: removed.append(path) or True)
    # Reach the leftover panel through a DRY-RUN preview, then switch
    # DRY-RUN off like the technician would before cleaning for real.
    window, card = _uninstaller_window(
        qtbot, tmp_path, monkeypatch, "run_orphan_real", [program], dry_run=True, orphans=orphans,
    )
    _panel_checkbox(card, "Some App").setChecked(True)
    _panel_button(card, window._t("uninstaller_uninstall_button")).click()
    window.dry_run_checkbox.setChecked(False)
    assert window.settings.dry_run is False
    shown = []
    monkeypatch.setattr(QMessageBox, "warning", lambda parent, title, text, *a: shown.append(text) or QMessageBox.Yes)
    _panel_checkbox(card, "Tool Unbackupable").setChecked(True)
    _panel_checkbox(card, "Tool Backed Up").setChecked(True)

    _panel_button(card, window._t("uninstaller_clean_leftovers_button")).click()

    assert len(shown) == 1 and "Tool Unbackupable" in shown[0] and "Tool Backed Up" in shown[0]
    assert removed == [orphans[1].registry_path]
    backup_file = tmp_path / "Backups" / "run_orphan_real" / "uninstall_Tool_Backed_Up.reg"
    assert backup_file.is_file()
    assert window._t("uninstaller_orphan_backup_failed").format(name="Tool Unbackupable") in _panel_console_text(card)
    entries = {
        e["action_id"]: e for e in _audit_entries(audit_log_path(tmp_path, "run_orphan_real"))
        if e["action_id"].startswith("orphan_cleanup:") and e["dry_run"] is False
    }
    assert entries["orphan_cleanup:Tool Unbackupable"]["exit_code"] == 1
    assert entries["orphan_cleanup:Tool Backed Up"]["exit_code"] == 0
    assert str(backup_file) in entries["orphan_cleanup:Tool Backed Up"]["output"]
    assert all(e["warned"] is True and e["warning_text"] == shown[0] for e in entries.values())
    # The backup makes the deletion reversible - undo.ps1 re-imports it.
    undo_text = (tmp_path / "Backups" / "run_orphan_real" / "undo.ps1").read_text(encoding="utf-8-sig")
    assert f"reg import '{backup_file}'" in undo_text


def _winget_window(qtbot, tmp_path, monkeypatch, run_id, dry_run, package, **settings):
    from PySide6.QtWidgets import QCheckBox

    from portablefix import winget_updates
    from portablefix.models import ModuleCategory

    monkeypatch.setattr(winget_updates, "list_outdated_packages", lambda: [package])
    base_dir = _make_base_dir(tmp_path)
    window = MainWindow(
        assets_dir=base_dir, state_dir=base_dir, settings=Settings(language="en", dry_run=dry_run, **settings),
        is_admin=True, run_id=run_id,
    )
    qtbot.addWidget(window)
    card = window._category_groups[ModuleCategory.DASHBOARD]

    def package_row():
        return next((cb for cb in card.findChildren(QCheckBox) if cb.toolTip() == package.id), None)

    qtbot.waitUntil(lambda: package_row() is not None, timeout=10000)
    return window, card, package_row()


def _fake_outdated_package():
    from portablefix import winget_updates

    return winget_updates.OutdatedPackage(
        name="Fake Editor", id="Fake.Editor", installed_version="1.0", available_version="2.0", source="winget",
    )


def test_winget_update_dry_run_starts_no_runner_and_logs_each_package(qtbot, tmp_path, monkeypatch):
    from portablefix import winget_updates

    updated = []
    monkeypatch.setattr(winget_updates, "update_package", lambda p, *a, **k: updated.append(p) or (True, ""))
    monkeypatch.setattr(winget_updates, "WingetUpdateRunner", _refuse_runner("WingetUpdateRunner"))
    window, card, row = _winget_window(qtbot, tmp_path, monkeypatch, "run_winget_dry", True, _fake_outdated_package())

    row.setChecked(True)
    _panel_button(card, window._t("winget_update_selected_button")).click()

    assert updated == [] and window._winget_update_runner is None
    assert "[DRY-RUN] winget upgrade --id Fake.Editor" in _panel_console_text(card)
    entries = [e for e in _audit_entries(audit_log_path(tmp_path, "run_winget_dry")) if e["module_id"] == "_winget"]
    assert len(entries) == 1
    assert entries[0]["action_id"] == "Fake.Editor" and entries[0]["dry_run"] is True
    assert entries[0]["command"].startswith("winget upgrade --id Fake.Editor")


def test_winget_update_declined_confirmation_starts_no_runner_and_logs_the_decline(qtbot, tmp_path, monkeypatch):
    from portablefix import winget_updates

    monkeypatch.setattr(winget_updates, "WingetUpdateRunner", _refuse_runner("WingetUpdateRunner"))
    window, card, row = _winget_window(qtbot, tmp_path, monkeypatch, "run_winget_no", False, _fake_outdated_package())
    shown = []
    monkeypatch.setattr(QMessageBox, "question", lambda parent, title, text, *a: shown.append(text) or QMessageBox.No)

    row.setChecked(True)
    _panel_button(card, window._t("winget_update_selected_button")).click()

    assert len(shown) == 1 and "Fake Editor" in shown[0]
    assert window._winget_update_runner is None
    declined = _system_events(audit_log_path(tmp_path, "run_winget_no"), "risk_declined")
    assert [e["subject"] for e in declined] == ["_winget/Fake.Editor"]
    assert declined[0]["decision"] == "declined" and declined[0]["warning_text"] == shown[0]


def test_winget_update_confirmed_logs_each_package_result(qtbot, tmp_path, monkeypatch):
    from portablefix import winget_updates
    from portablefix.gui.main_window import _thread_running

    monkeypatch.setattr(winget_updates, "update_package", lambda p, *a, **k: (True, "Successfully installed"))
    window, card, row = _winget_window(qtbot, tmp_path, monkeypatch, "run_winget_yes", False, _fake_outdated_package())
    shown = []
    monkeypatch.setattr(QMessageBox, "question", lambda parent, title, text, *a: shown.append(text) or QMessageBox.Yes)

    row.setChecked(True)
    _panel_button(card, window._t("winget_update_selected_button")).click()

    log_path = audit_log_path(tmp_path, "run_winget_yes")
    qtbot.waitUntil(lambda: any(e["module_id"] == "_winget" for e in _audit_entries(log_path)), timeout=10000)
    qtbot.waitUntil(lambda: not _thread_running(window._winget_update_runner), timeout=10000)
    qtbot.waitUntil(lambda: not _thread_running(window._winget_scan_runner), timeout=10000)
    entries = [e for e in _audit_entries(log_path) if e["module_id"] == "_winget"]
    assert len(entries) == 1
    entry = entries[0]
    assert entry["action_id"] == "Fake.Editor" and entry["exit_code"] == 0 and entry["dry_run"] is False
    assert entry["warned"] is True and entry["warning_text"] == shown[0]
    assert entry["output"] == "Successfully installed"
    undo_text = (tmp_path / "Backups" / "run_winget_yes" / "undo.ps1").read_text(encoding="utf-8-sig")
    assert "NOT reversible" in undo_text and "Fake.Editor" in undo_text


def _winget_failed_window(qtbot, tmp_path, monkeypatch, run_id, error, ignored_ids=()):
    from PySide6.QtWidgets import QLabel

    from portablefix import winget_updates
    from portablefix.gui.main_window import _thread_running
    from portablefix.models import ModuleCategory

    def failing_scan():
        raise error

    monkeypatch.setattr(winget_updates, "list_outdated_packages", failing_scan)
    base_dir = _make_base_dir(tmp_path)
    window = MainWindow(
        assets_dir=base_dir, state_dir=base_dir,
        settings=Settings(language="en", dry_run=True, winget_ignored_ids=list(ignored_ids)),
        is_admin=True, run_id=run_id,
    )
    qtbot.addWidget(window)
    card = window._category_groups[ModuleCategory.DASHBOARD]
    banner = next(label for label in card.findChildren(QLabel) if label.objectName() == "wingetBanner")
    qtbot.waitUntil(lambda: not _thread_running(window._winget_scan_runner), timeout=10000)
    qtbot.waitUntil(lambda: banner.text() != window._t("winget_scanning"), timeout=10000)
    return window, card, banner


def test_winget_panel_says_winget_is_missing_instead_of_no_updates(qtbot, tmp_path, monkeypatch):
    # A PC without App Installer used to show "No winget updates found." -
    # a false all-clear that ended up in the technician's handover.
    from portablefix.winget_updates import WingetScanError

    window, card, banner = _winget_failed_window(
        qtbot, tmp_path, monkeypatch, "run_winget_missing", WingetScanError("unavailable", "not_found"),
    )
    assert banner.text() == window._t("winget_unavailable_not_found")
    assert banner.property("state") == "warn"
    assert window._t("winget_no_updates") not in banner.text()
    # Refresh stays reachable to re-check once App Installer is fixed.
    assert _panel_button(card, window._t("winget_refresh_button")).isVisibleTo(card)


def test_winget_panel_shows_a_failed_scan_with_its_hex_exit_code(qtbot, tmp_path, monkeypatch):
    from portablefix.winget_updates import WingetScanError

    error = WingetScanError("error", "sources", exit_code=0x8A15004B, detail="Zlyhanie pri otváraní zdrojov.")
    window, card, banner = _winget_failed_window(qtbot, tmp_path, monkeypatch, "run_winget_failed", error)
    assert "0x8A15004B" in banner.text()
    assert window._t("winget_scan_hint_sources") in banner.text()
    assert banner.toolTip() == "Zlyhanie pri otváraní zdrojov."


def test_winget_panel_keeps_rows_listed_before_a_failure(qtbot, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QCheckBox

    from portablefix.winget_updates import WingetScanError

    error = WingetScanError("error", "failed", exit_code=0x8A150001, packages=[_fake_outdated_package()])
    window, card, banner = _winget_failed_window(qtbot, tmp_path, monkeypatch, "run_winget_partial", error)
    assert any(cb.toolTip() == "Fake.Editor" for cb in card.findChildren(QCheckBox))
    assert "0x8A150001" in banner.text() and window._t("winget_scan_partial") in banner.text()


def test_winget_panel_does_not_point_at_a_list_it_hides(qtbot, tmp_path, monkeypatch):
    # Every partial row is ignored, so no list is shown - the banner must
    # not refer to "the list below".
    from portablefix.winget_updates import WingetScanError

    error = WingetScanError("error", "failed", exit_code=0x8A150001, packages=[_fake_outdated_package()])
    window, card, banner = _winget_failed_window(
        qtbot, tmp_path, monkeypatch, "run_winget_partial_ignored", error, ignored_ids=["Fake.Editor"],
    )
    assert "0x8A150001" in banner.text()
    assert window._t("winget_scan_partial") not in banner.text()


def test_winget_panel_says_which_rows_were_unreadable(qtbot, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QCheckBox

    from portablefix.winget_updates import WingetScanError

    error = WingetScanError("error", "unparsed", packages=[_fake_outdated_package()])
    window, card, banner = _winget_failed_window(qtbot, tmp_path, monkeypatch, "run_winget_unreadable_rows", error)
    assert any(cb.toolTip() == "Fake.Editor" for cb in card.findChildren(QCheckBox))
    assert banner.text().startswith(window._t("winget_scan_unparsed_rows"))
    assert window._t("winget_scan_unparsed") not in banner.text()


def test_panel_confirmation_list_is_capped(qtbot, tmp_path):
    base_dir = _make_base_dir(tmp_path)
    window = MainWindow(assets_dir=base_dir, state_dir=base_dir, settings=Settings(language="en"), is_admin=True, run_id="run_confirm_cap")
    qtbot.addWidget(window)

    text = window._panel_confirm_list([f"Program {i}" for i in range(25)])

    lines = text.splitlines()
    assert len(lines) == 21
    assert lines[0] == "• Program 0" and lines[19] == "• Program 19"
    assert lines[20] == window._t("uninstaller_and_more").format(count=5)
    assert window._panel_confirm_list(["Only one"]) == "• Only one"


SLOW_AND_QUICK_ACTIONS_YAML = """
module_id: m01_diagnostics
actions:
  - id: slow_one
    label_sk: "Pomalá"
    label_en: "Slow one"
    risk: SAFE
    command: "Start-Sleep -Seconds 2; Write-Output 'slow-done'"
  - id: quick_one
    label_sk: "Rýchla"
    label_en: "Quick one"
    risk: SAFE
    command: "Write-Output 'quick-done'"
"""


def _checked_ids(window) -> set[str]:
    return {aid for aid, cb in window._action_checkboxes.items() if cb.isChecked()}


def test_dashboard_analyze_is_ignored_and_disabled_mid_batch(qtbot, tmp_path, monkeypatch):
    # Analyze applies a preset and starts a batch. Mid-batch it used to stay
    # clickable: it wiped the checkbox selection, overwrote the running
    # _queue and started a second ActionRunner next to the first one.
    import threading

    from portablefix import report
    from portablefix.gui.main_window import PRESETS

    monkeypatch.setitem(PRESETS, "full_diagnostic", ["quick_one"])
    release_report = threading.Event()
    real_generate_report = report.generate_report

    def gated_generate_report(*args, **kwargs):
        release_report.wait(10)
        return real_generate_report(*args, **kwargs)

    monkeypatch.setattr(report, "generate_report", gated_generate_report)
    base_dir = _make_base_dir(tmp_path, SLOW_AND_QUICK_ACTIONS_YAML)
    window = MainWindow(assets_dir=base_dir, state_dir=base_dir, settings=Settings(language="en"), is_admin=True, run_id="run_analyze_mid_batch")
    qtbot.addWidget(window)
    assert window.dashboard_analyze_button.isEnabled() is True
    window._action_checkboxes["slow_one"].setChecked(True)

    window.run_selected_actions()
    assert window._batch_active is True
    runner = window._runner
    assert runner is not None
    queue = list(window._queue)
    checked = _checked_ids(window)
    assert window.dashboard_analyze_button.isEnabled() is False

    window._run_dashboard_analysis()
    window.dashboard_analyze_button.click()

    assert window._runner is runner
    assert window._queue == queue
    assert _checked_ids(window) == checked

    # Still locked after the batch while its report is being written...
    qtbot.waitUntil(lambda: not window._batch_active, timeout=15000)
    assert window._report_runner is not None
    assert window.dashboard_analyze_button.isEnabled() is False
    release_report.set()
    _wait_batch_idle(qtbot, window)
    # ...and unlocked together with run_button once it is written.
    assert window.dashboard_analyze_button.isEnabled() is True
    assert window.run_button.isEnabled() is True
    assert "slow-done" in window.console.toPlainText()
    assert "quick-done" not in window.console.toPlainText()


def test_analyze_button_disabled_after_language_toggle_mid_batch(qtbot, tmp_path):
    # The language button itself is locked mid-batch (see
    # test_language_toggle_mid_batch_restores_run_state_on_the_rebuilt_widgets),
    # so drive the rebuild directly: a freshly built dashboard must not hand
    # back an enabled Analyze button while a batch or its report is running.
    base_dir = _make_base_dir(tmp_path)
    window = MainWindow(assets_dir=base_dir, state_dir=base_dir, settings=Settings(language="en"), is_admin=True, run_id="run_analyze_toggle_mid_batch")
    qtbot.addWidget(window)
    window._batch_active = True
    window._queue = ["hello"]
    window._queue_total = 2

    window._on_toggle_language()
    assert window.dashboard_analyze_button.isEnabled() is False
    assert window.run_button.isEnabled() is False

    window._batch_active = False
    window._queue = []
    window._report_runner = object()
    window._on_toggle_language()
    assert window.dashboard_analyze_button.isEnabled() is False

    window._report_runner = None
    window._on_toggle_language()
    assert window.dashboard_analyze_button.isEnabled() is True


def test_analyze_button_unlocks_when_update_download_spanning_a_language_toggle_ends(qtbot, tmp_path):
    # The language button stays usable during an update download, and the
    # rebuilt dashboard locks Analyze for it - the download's end must
    # unlock it again, or it stays dead until some later batch finishes.
    from portablefix.updater import UpdateInfo

    base_dir = _make_base_dir(tmp_path)
    window = MainWindow(assets_dir=base_dir, state_dir=base_dir, settings=Settings(language="en"), is_admin=True, run_id="run_analyze_toggle_mid_dl")
    qtbot.addWidget(window)
    window._on_update_check_finished(UpdateInfo(version="9.9.9", package_url="https://x", sha256_url=None, notes=""))
    window._update_in_progress = True
    window.update_button.setEnabled(False)
    window.update_dismiss_button.setEnabled(False)

    window._on_toggle_language()
    assert window.dashboard_analyze_button.isEnabled() is False

    window._on_update_download_finished(None, "network down")
    assert window.dashboard_analyze_button.isEnabled() is True


def test_cancel_then_analyze_during_restore_point_does_not_uncancel(qtbot, tmp_path, monkeypatch):
    # Cancel clicked while Checkpoint-Computer runs, then Analyze (or any
    # other path into run_selected_actions): the re-entry used to reset
    # _cancel_requested, so the restore point's result then dispatched the
    # DESTRUCTIVE action the technician had just cancelled.
    import time

    from portablefix import restore_point
    from portablefix.gui.main_window import PRESETS

    def slow_create_restore_point(description):
        time.sleep(0.4)
        return True, ""

    monkeypatch.setattr(restore_point, "create_restore_point", slow_create_restore_point)
    monkeypatch.setitem(PRESETS, "full_diagnostic", ["risky_thing"])
    dispatched = []
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: dispatched.append(a) or QMessageBox.Yes))
    reviews = _answer_review(monkeypatch)  # G12: the first batch is confirmed on the review screen
    base_dir = _make_destructive_base_dir(tmp_path)
    window = MainWindow(assets_dir=base_dir, state_dir=base_dir, settings=Settings(language="en", dry_run=False), is_admin=True, run_id="run_cancel_analyze_rp")
    qtbot.addWidget(window)
    window._action_checkboxes["risky_thing"].setChecked(True)

    window.run_selected_actions()
    rp_runner = window._pending_restore_point_runner
    assert rp_runner is not None
    window._on_cancel_clicked()
    assert window._batch_active is True
    assert window._cancel_requested is True

    window.run_selected_actions()
    window._run_dashboard_analysis()

    assert window._cancel_requested is True
    assert window._queue == []
    assert window._pending_restore_point_runner is rp_runner
    _wait_batch_idle(qtbot, window)
    assert dispatched == []
    assert len(reviews) == 1  # the blocked re-entries never reached the review
    assert "destructive-ran" not in window.console.toPlainText()
    assert "risky_thing" not in _executed_action_ids(audit_log_path(base_dir, "run_cancel_analyze_rp"))


def _write_module_with_excluded_risk_actions(base_dir):
    module_dir = base_dir / "Modules" / "m08_security"
    module_dir.mkdir(parents=True)
    actions = [
        ("safe_normal", "SAFE", False),
        ("safe_excluded", "SAFE", True),
        ("mod_normal", "MODERATE", False),
        ("mod_excluded", "MODERATE", True),
        ("reboot_normal", "REQUIRES_REBOOT", False),
        ("reboot_excluded", "REQUIRES_REBOOT", True),
    ]
    lines = ["module_id: m08_security", "category: SECURITY", "actions:"]
    for action_id, risk, excluded in actions:
        lines += [
            f"  - id: {action_id}",
            "    label_sk: \"Akcia\"",
            f"    label_en: \"{action_id}\"",
            f"    risk: {risk}",
            "    command: \"Write-Output 'x'\"",
        ]
        if excluded:
            lines.append("    exclude_from_select_all: true")
    (module_dir / "actions.yaml").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return base_dir


def test_global_risk_buttons_skip_excluded_actions(qtbot, tmp_path):
    # "Select MODERATE only" used to sweep in hard_disable_rdp,
    # drv_restore_backup, backup_restore_latest and crash_dumps, and
    # "REQUIRES_REBOOT only" hard_lsa_protection_enable - the opt-out
    # applied to "select all" alone.
    from portablefix.models import ModuleCategory

    base_dir = _write_module_with_excluded_risk_actions(tmp_path)
    window = MainWindow(assets_dir=base_dir, state_dir=base_dir, settings=Settings(language="en"), is_admin=True, run_id="run_risk_excl")
    qtbot.addWidget(window)

    window.global_select_moderate_button.click()
    assert _checked_ids(window) == {"mod_normal"}

    window.global_select_reboot_button.click()
    assert _checked_ids(window) == {"reboot_normal"}

    window.global_select_safe_button.click()
    assert _checked_ids(window) == {"safe_normal"}

    window.global_select_none_button.click()
    _all_btn, category_safe_btn, _none_btn = window._category_select_buttons[ModuleCategory.SECURITY]
    category_safe_btn.click()
    assert _checked_ids(window) == {"safe_normal"}

    # Deliberate selection still works, from either view.
    window._action_checkboxes["mod_excluded"].setChecked(True)
    window._risk_view_checkboxes["reboot_excluded"].setChecked(True)
    assert _checked_ids(window) == {"safe_normal", "mod_excluded", "reboot_excluded"}


def test_custom_preset_restores_excluded_action(qtbot, tmp_path, monkeypatch):
    # A custom preset is saved from boxes checked by hand, opt-out actions
    # included - applying it used to go through the "select all" path,
    # which silently dropped exactly those.
    from portablefix.gui.main_window import PRESETS

    _write_module_with_excluded_action(tmp_path, "m10_drivers", "DRIVER_UPDATES", "drv_safe", "drv_restore_backup")
    window = MainWindow(assets_dir=tmp_path, state_dir=tmp_path, settings=Settings(language="en"), is_admin=True, run_id="run_custom_excl")
    qtbot.addWidget(window)
    saved = ["drv_safe", "drv_restore_backup"]
    assert window._save_custom_preset("Obnova ovládačov", saved) is True
    # An id that has since left the catalog is skipped, not an error.
    window.settings.custom_presets["Obnova ovládačov"].append("gone_from_catalog")
    window._apply_selection(list(window._action_checkboxes), "none")
    assert _checked_ids(window) == set()

    window._preset_buttons["custom:Obnova ovládačov"].click()

    assert _checked_ids(window) == set(saved)
    assert window._preset_buttons["custom:Obnova ovládačov"].isChecked() is True

    # Built-in presets keep the bulk path and its opt-out.
    monkeypatch.setitem(PRESETS, "_test_builtin_excl", saved)
    window._apply_preset("_test_builtin_excl")
    assert _checked_ids(window) == {"drv_safe"}


# --- In-app update: stage, confirm, guard, hand-off, close ---


def _update_window(qtbot, tmp_path, run_id, language="en", **settings):
    from portablefix.updater import UpdateInfo

    base_dir = _make_base_dir(tmp_path)
    window = MainWindow(
        assets_dir=base_dir, state_dir=base_dir, settings=Settings(language=language, **settings),
        is_admin=True, run_id=run_id,
    )
    qtbot.addWidget(window)
    window.show()
    # The winget panel starts a real scan with the window; where winget
    # exists (the Windows runner) closing for the update waits for it, so
    # let it finish first instead of racing the tests' 5 s waits.
    from portablefix.gui.main_window import _thread_running

    qtbot.waitUntil(lambda: not _thread_running(window._winget_scan_runner), timeout=90_000)
    window._on_update_check_finished(UpdateInfo(version="9.9.9", package_url="https://x", sha256_url=None, notes=""))
    return window


def test_update_handshake_ok_closes_the_window_for_the_updater(qtbot, tmp_path, monkeypatch):
    from portablefix.updater import LaunchResult

    questions = []
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda p, t, text, *a, **k: questions.append(text) or QMessageBox.Yes))
    calls = _patch_update_flow(monkeypatch, tmp_path, [LaunchResult(ok=True, route="direct")])
    window = _update_window(qtbot, tmp_path, "run_update_handoff")

    window.update_button.click()

    qtbot.waitUntil(lambda: not window.isVisible(), timeout=5000)
    assert window._closing_for_update is True
    assert calls["launch"] == 1
    assert len(questions) == 2 and "9.9.9" in questions[1]


def test_close_for_update_skips_the_batch_prompt(qtbot, tmp_path):
    # The updater is already waiting for this process: a "close anyway?"
    # question nobody answers would leave it to time out and give up.
    window = _update_window(qtbot, tmp_path, "run_update_close_no_prompt")
    window._closing_for_update = True
    window._batch_active = True

    # conftest turns any QMessageBox.question into a test failure.
    window.close()

    assert not window.isVisible()
    window._batch_active = False


def test_update_launch_failure_stays_open_explains_and_retries_without_a_new_download(qtbot, tmp_path, monkeypatch):
    from portablefix import i18n, updater
    from portablefix.updater import LaunchResult

    questions, warnings = [], []
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda p, t, text, *a, **k: questions.append(text) or QMessageBox.Yes))
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda p, t, text, *a, **k: warnings.append(text)))
    failure = LaunchResult(
        ok=False, reason=updater.REASON_EXITED, exit_code=1,
        detail="exit code 0x00000001: ParserError: Unexpected token", log_dir=tmp_path / "logs",
    )
    calls = _patch_update_flow(monkeypatch, tmp_path, [failure, LaunchResult(ok=True)])
    window = _update_window(qtbot, tmp_path, "run_update_launch_fail")

    window.update_button.click()
    qtbot.waitUntil(lambda: len(warnings) == 1, timeout=5000)

    assert window.isVisible() and window._closing_for_update is False
    text = warnings[0]
    assert i18n.translate("update_reason_exited", "en") in text
    assert "0x00000001" in text and "ParserError" in text
    assert str(tmp_path / "logs") in text
    assert updater.RELEASES_PAGE_URL in text
    assert window.update_banner_label.text() == i18n.translate("update_apply_failed", "en")
    assert window.update_button.isEnabled() and window.progress_bar.isVisibleTo(window) is False

    # The verified stage is reused: straight to the restart question.
    window.update_button.click()
    qtbot.waitUntil(lambda: not window.isVisible(), timeout=5000)
    assert calls["download"] == 1 and calls["stage"] == 1 and calls["launch"] == 2
    assert len(questions) == 3
    assert questions[1] == questions[2] == i18n.translate("update_confirm_restart", "en").format(version="9.9.9")


def test_update_is_staged_again_when_the_kept_stage_disappeared(qtbot, tmp_path, monkeypatch):
    import shutil as shutil_module

    from portablefix import updater
    from portablefix.updater import LaunchResult

    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Yes))
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: None))
    failure = LaunchResult(ok=False, reason=updater.REASON_TIMEOUT, detail="no response")
    calls = _patch_update_flow(monkeypatch, tmp_path, [failure, LaunchResult(ok=True)])
    window = _update_window(qtbot, tmp_path, "run_update_stage_gone")

    window.update_button.click()
    qtbot.waitUntil(lambda: calls["launch"] == 1 and window.update_button.isEnabled(), timeout=5000)
    shutil_module.rmtree(tmp_path / "_update_stage")

    window.update_button.click()
    qtbot.waitUntil(lambda: not window.isVisible(), timeout=5000)
    assert calls["download"] == 2 and calls["stage"] == 2


def test_update_hand_off_is_refused_while_a_winget_update_runs(qtbot, tmp_path, monkeypatch):
    from portablefix import i18n

    class _BusyRunner:
        def isRunning(self):
            return True

        def request_stop(self):
            pass

        def wait(self, *args):
            return True

    warnings = []
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Yes))
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda p, t, text, *a, **k: warnings.append(text)))
    calls = _patch_update_flow(monkeypatch, tmp_path, [])
    window = _update_window(qtbot, tmp_path, "run_update_busy")
    window._winget_update_runner = _BusyRunner()

    window.update_button.click()
    qtbot.waitUntil(lambda: len(warnings) == 1, timeout=5000)

    assert i18n.translate("update_busy_winget_update", "en") in warnings[0]
    assert calls["launch"] == 0
    assert window._staged_update is not None
    assert window.update_banner_label.text() == "Version 9.9.9 is available"
    assert window.update_button.isEnabled()
    window._winget_update_runner = None


def test_long_running_tasks_names_each_kind_of_work(qtbot, tmp_path):
    window = _update_window(qtbot, tmp_path, "run_update_tasks")
    assert window._long_running_tasks() == []

    window._batch_active = True
    window._report_runner = object()
    window._speed_test_busy = True
    assert window._long_running_tasks() == [
        window._t("update_busy_batch"), window._t("update_busy_report"), window._t("update_busy_speed_test"),
    ]
    window._batch_active = False
    window._report_runner = None
    window._speed_test_busy = False


def test_long_running_tasks_names_an_uninstall_in_progress(qtbot, tmp_path):
    # An uninstaller can wait minutes for the user - the updater would give
    # up waiting for this process to exit.
    class _Running:
        def isRunning(self):
            return True

    window = _update_window(qtbot, tmp_path, "run_update_tasks_uninstall")
    window._uninstall_runner = _Running()
    try:
        assert window._long_running_tasks() == [window._t("update_busy_uninstall")]
    finally:
        window._uninstall_runner = None


def test_long_running_tasks_ignore_the_automatic_winget_scan(qtbot, tmp_path):
    # The panel starts a read-only scan with the window; on a PC with winget
    # it was still running when the technician clicked "Update" right after
    # start, and the update was refused for no reason.
    class _Running:
        def isRunning(self):
            return True

    window = _update_window(qtbot, tmp_path, "run_update_tasks_scan")
    window._winget_scan_runner = _Running()
    try:
        assert window._long_running_tasks() == []
    finally:
        window._winget_scan_runner = None


def test_closing_during_an_uninstall_waits_for_the_running_one_and_skips_the_rest(qtbot, tmp_path, monkeypatch):
    # Not waited for, the uninstall's QThread was destroyed with the window
    # and the process aborted.
    import threading

    from portablefix import uninstaller

    started = threading.Event()
    release = threading.Event()
    ran = []

    def fake_uninstall(program, *a, **k):
        ran.append(program.name)
        started.set()
        release.wait(10)
        return True, ""

    waits = []

    class _RecordingRunner(uninstaller.UninstallRunner):
        def wait(self, *args):
            waits.append(args)
            return super().wait(*args)

    monkeypatch.setattr(uninstaller, "uninstall_program", fake_uninstall)
    window = _update_window(qtbot, tmp_path, "run_close_mid_uninstall")
    programs = [_fake_installed_program(name, plain=f"{name}.exe") for name in ("One", "Two", "Three")]
    runner = _RecordingRunner(programs, parent=window)
    window._uninstall_runner = runner
    runner.start()
    assert started.wait(5)
    threading.Timer(0.5, release.set).start()

    window.close()

    assert waits == [(uninstaller.UNINSTALL_TIMEOUT_SEC * 1000 + 10_000,)]
    assert runner.isFinished()
    assert ran == ["One"]


def test_winget_update_says_why_it_does_nothing_while_the_app_update_starts(qtbot, tmp_path, monkeypatch):
    from portablefix import winget_updates

    monkeypatch.setattr(winget_updates, "WingetUpdateRunner", _refuse_runner("WingetUpdateRunner"))
    window, card, row = _winget_window(qtbot, tmp_path, monkeypatch, "run_winget_app_update", False, _fake_outdated_package())
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: pytest.fail("no confirmation while the update starts"))
    window._update_phase = "launch"

    row.setChecked(True)
    _panel_button(card, window._t("winget_update_selected_button")).click()

    assert window._winget_update_runner is None
    assert window.statusBar().currentMessage() == window._t("winget_update_blocked_by_app_update")
    window._update_phase = None


def test_closing_during_an_update_download_stops_it_cleanly(qtbot, tmp_path, monkeypatch):
    import threading
    import time

    from portablefix import updater

    started = threading.Event()

    def slow_download(info, dest, on_progress=None, should_stop=None):
        partial = dest / "PortableFix-update.zip"
        partial.write_bytes(b"partial")
        started.set()
        deadline = time.monotonic() + 20
        while not should_stop() and time.monotonic() < deadline:
            time.sleep(0.01)
        partial.unlink()
        raise updater.UpdateDownloadCancelled()

    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Yes))
    monkeypatch.setattr(updater, "download_update", slow_download)
    window = _update_window(qtbot, tmp_path, "run_update_close_mid_download")

    window.update_button.click()
    assert started.wait(5)
    runner = window._update_download_runner
    download_dir = window._update_download_dir
    began = time.monotonic()

    window.close()

    # Returned because the download stopped, not because a wait ran out -
    # a still-running QThread destroyed with the window aborts the process.
    assert time.monotonic() - began < 10
    assert runner.isFinished()
    assert not download_dir.exists()
    # The queued "download finished" signal must not act on a closed window.
    qtbot.wait(100)


def test_language_toggle_during_staging_keeps_the_progress_bar_and_step_text(qtbot, tmp_path, monkeypatch):
    import threading

    from portablefix import i18n, updater

    release = threading.Event()

    def slow_stage(zip_path, install_dir, should_stop=None, progress=None, version=None):
        progress(5, 10)
        release.wait(10)
        raise updater.UpdateStageError("SHA256 mismatch: App/python312.dll")

    warnings = []
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Yes))
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda p, t, text, *a, **k: warnings.append(text)))
    _patch_update_flow(monkeypatch, tmp_path, [])
    monkeypatch.setattr(updater, "stage_update", slow_stage)
    window = _update_window(qtbot, tmp_path, "run_update_toggle_stage")

    window.update_button.click()
    qtbot.waitUntil(lambda: window._update_phase == "stage" and window.progress_bar.maximum() == 10, timeout=5000)
    window._on_toggle_language()

    # The shown window shows its rebuilt widgets on the next event loop pass
    # - unless one was hidden explicitly, which is the bug this guards.
    qtbot.waitUntil(lambda: window.progress_bar.isVisibleTo(window), timeout=2000)
    assert (window.progress_bar.value(), window.progress_bar.maximum()) == (5, 10)
    assert window.update_banner_label.text() == i18n.translate("update_preparing", "sk")
    assert window.update_button.isEnabled() is False

    release.set()
    qtbot.waitUntil(lambda: len(warnings) == 1, timeout=5000)
    assert "SHA256 mismatch: App/python312.dll" in warnings[0]
    assert updater.RELEASES_PAGE_URL in warnings[0]
    assert window.update_banner_label.text() == i18n.translate("update_stage_failed", "sk")
    assert window.progress_bar.isVisibleTo(window) is False
    assert window.update_button.isEnabled() is True
    assert window._update_download_dir is None


def test_winget_auto_check_skips_while_the_app_updates(qtbot, tmp_path, monkeypatch):
    from PySide6.QtCore import QTimer

    from portablefix import winget_updates

    window, card, _row = _winget_window(
        qtbot, tmp_path, monkeypatch, "run_update_winget_tick", False, _fake_outdated_package(),
        winget_auto_check_minutes=15,
    )
    timers = [t for t in card.findChildren(QTimer) if t.isActive() and t.interval() == 15 * 60_000]
    assert len(timers) == 1
    started = []

    class _RecordingScan:
        def __init__(self, parent=None):
            started.append(True)
            self.scan_finished = self
            self.scan_failed = self

        def connect(self, slot):
            pass

        def start(self):
            pass

    monkeypatch.setattr(winget_updates, "WingetScanRunner", _RecordingScan)
    window._update_in_progress = True
    timers[0].timeout.emit()
    assert started == []

    window._update_in_progress = False
    window._closing_for_update = True
    timers[0].timeout.emit()
    assert started == []

    window._closing_for_update = False
    timers[0].timeout.emit()
    assert started == [True]
    window._winget_scan_runner = None


def test_dev_update_switch_runs_a_local_zip_through_the_same_flow(qtbot, tmp_path, monkeypatch):
    import hashlib

    from portablefix import updater

    source = tmp_path / "PortableFix-Portable.zip"
    source.write_bytes(b"local release")
    staged_from = []
    questions = []
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda p, t, text, *a, **k: questions.append(text) or QMessageBox.No))
    calls = _patch_update_flow(monkeypatch, tmp_path, [])

    def record_stage(zip_path, install_dir, should_stop=None, progress=None, version=None):
        staged_from.append(zip_path.read_bytes())
        return _fake_staged(tmp_path, version)

    monkeypatch.setattr(updater, "stage_update", record_stage)
    base_dir = _make_base_dir(tmp_path)
    window = MainWindow(assets_dir=base_dir, state_dir=base_dir, settings=Settings(language="en"), is_admin=True, run_id="run_dev_update")
    qtbot.addWidget(window)

    window.start_local_update(source, hashlib.sha256(b"local release").hexdigest())

    qtbot.waitUntil(lambda: len(questions) == 1, timeout=5000)
    # No download question, the real copy-and-verify, then the usual
    # restart question; declining it launches nothing.
    assert staged_from == [b"local release"]
    assert "PortableFix-Portable.zip (dev)" in questions[0]
    assert source.exists()
    assert calls["download"] == 0 and calls["launch"] == 0


def test_a_late_release_check_does_not_replace_the_update_in_progress(qtbot, tmp_path):
    from portablefix.updater import UpdateInfo

    window = _update_window(qtbot, tmp_path, "run_update_late_check")
    local = window._pending_update_info
    window._update_in_progress = True

    window._on_update_check_finished(UpdateInfo(version="10.0.0", package_url="https://y", sha256_url=None, notes=""))

    assert window._pending_update_info is local
    window._update_in_progress = False


# --- G11/G12: pre-flight check and the one batch review screen -------------


def _answer_review(monkeypatch, accept=True, tick=True, override=False):
    """Stands in for the modal BatchReviewDialog.exec: drives the real
    dialog's widgets the way a technician would and returns the list of
    dialogs shown (their .review holds what was on screen)."""
    from portablefix.gui.batch_review import BatchReviewDialog

    shown = []

    def fake_exec(self):
        shown.append(self)
        for checkbox in self.destructive_checkboxes.values():
            checkbox.setChecked(tick)
        if override and self.override_checkbox is not None:
            self.override_checkbox.setChecked(True)
        if accept and self.confirm_button.isEnabled():
            self.confirm_button.click()
        else:
            self.cancel_button.click()
        return self.result()

    monkeypatch.setattr(BatchReviewDialog, "exec", fake_exec)
    return shown


REVIEW_BATCH_YAML = """
module_id: m02_cleanup
category: CLEANUP
actions:
  - id: wipe_thing
    label_sk: "Zmazat vec"
    label_en: "Wipe thing"
    risk: DESTRUCTIVE
    command: "Write-Output 'wipe-ran'"
    preview_command: "Write-Output 'wipe-preview'"
  - id: tweak_one
    label_sk: "Uprava 1"
    label_en: "Tweak one"
    risk: MODERATE
    command: "Write-Output 'tweak-one-ran'"
    undo_command: "Write-Output 'undo-one'"
  - id: tweak_two
    label_sk: "Uprava 2"
    label_en: "Tweak two"
    risk: MODERATE
    command: "Write-Output 'tweak-two-ran'"
  - id: reboot_thing
    label_sk: "Restart vec"
    label_en: "Reboot thing"
    risk: REQUIRES_REBOOT
    command: "Write-Output 'reboot-ran'"
  - id: look_thing
    label_sk: "Pozriet"
    label_en: "Look thing"
    risk: SAFE
    command: "Write-Output 'look-ran'"
  - id: rescue_thing
    label_sk: "Zachrana"
    label_en: "Rescue thing"
    risk: MODERATE
    command: "Write-Output 'rescue-ran'"
    exclude_from_select_all: true
"""


def _review_window(qtbot, tmp_path, monkeypatch, run_id, dry_run=False, is_admin=True, probes=None):
    from portablefix import preflight, restore_point

    module_dir = tmp_path / "Modules" / "m02_cleanup"
    module_dir.mkdir(parents=True)
    (module_dir / "actions.yaml").write_text(REVIEW_BATCH_YAML, encoding="utf-8")
    monkeypatch.setattr(restore_point, "create_restore_point", lambda description: (True, ""))
    window = MainWindow(
        assets_dir=tmp_path, state_dir=tmp_path, settings=Settings(language="en", dry_run=dry_run),
        is_admin=is_admin, run_id=run_id,
    )
    qtbot.addWidget(window)
    # A healthy PC unless the test says otherwise - never the real machine.
    healthy = preflight.Probes(
        power=lambda: preflight.PowerStatus(on_battery=False, percent=100),
        pending_reboot=lambda: [],
        system_free_bytes=lambda: 100 * 1024**3,
        is_admin=lambda: window.is_admin,
        busy_tasks=lambda: [],
    )
    monkeypatch.setattr(window, "_preflight_probes", lambda: probes or healthy)
    return window


def _check(window, *action_ids):
    for action_id in action_ids:
        window._action_checkboxes[action_id].setChecked(True)


def test_batch_review_is_one_screen_for_all_risky_actions_and_quotes_it_in_the_audit(qtbot, tmp_path, monkeypatch):
    # conftest turns every QMessageBox into a failure: the old one-box-per-
    # action flow would fail here four times over.
    window = _review_window(qtbot, tmp_path, monkeypatch, "run_review_one")
    reviews = _answer_review(monkeypatch)
    _check(window, "look_thing", "tweak_one", "wipe_thing", "tweak_two", "reboot_thing")

    window.run_selected_actions()
    _wait_batch_idle(qtbot, window)

    assert len(reviews) == 1
    review = reviews[0].review
    # Every selected action, most dangerous first, with its risk tier.
    assert [(i.action_id, i.risk.value) for i in review.items] == [
        ("wipe_thing", "DESTRUCTIVE"), ("reboot_thing", "REQUIRES_REBOOT"),
        ("tweak_one", "MODERATE"), ("tweak_two", "MODERATE"), ("look_thing", "SAFE"),
    ]
    assert review.restore_point_planned is True  # DESTRUCTIVE needs one
    assert {i.action_id for i in review.items if i.irreversible} == {"wipe_thing", "tweak_two", "reboot_thing"}
    assert [i.action_id for i in review.items if i.needs_reboot] == ["reboot_thing"]
    shown = {i.action_id: i.warning_text for i in review.items}
    assert shown["look_thing"] == ""
    assert "Wipe thing" in shown["wipe_thing"] and "[DESTRUCTIVE]" in shown["wipe_thing"]

    log_path = audit_log_path(tmp_path, "run_review_one")
    entries = {e["action_id"]: e for e in _audit_entries(log_path) if e["module_id"] != "_system"}
    assert set(entries) == {"look_thing", "tweak_one", "wipe_thing", "tweak_two", "reboot_thing"}
    for action_id, entry in entries.items():
        # warned/warning_text: exactly the row text the technician confirmed.
        assert entry["warning_text"] == shown[action_id]
        assert entry["warned"] is bool(shown[action_id])
    [event] = _system_events(log_path, "batch_review")
    assert event["decision"] == "confirmed" and event["warned"] is False
    assert _system_events(log_path, "risk_declined") == []
    assert len(_system_events(log_path, "restore_point")) == 1


def test_batch_review_lists_restore_point_irreversible_and_reboot_on_screen(qtbot, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QLabel

    from portablefix import i18n
    from portablefix.gui.batch_review import BatchReviewDialog

    window = _review_window(qtbot, tmp_path, monkeypatch, "run_review_text")
    texts = []

    def capture(self):
        # Unticked, the DESTRUCTIVE action will not run - and it was the
        # only one that needed a restore point.
        texts.append(self.restore_point_label.text())
        self.destructive_checkboxes["wipe_thing"].setChecked(True)
        texts.extend(label.text() for label in self.findChildren(QLabel))
        self.cancel_button.click()
        return self.result()

    monkeypatch.setattr(BatchReviewDialog, "exec", capture)
    _check(window, "wipe_thing", "reboot_thing", "tweak_one")
    window.run_selected_actions()

    assert texts[0] == i18n.translate("review_restore_point_no", "en")
    assert i18n.translate("review_restore_point_yes", "en") in texts[1:]
    assert "Cannot be undone through PortableFix: Wipe thing, Reboot thing" in texts
    assert "Needs a restart to finish: Reboot thing" in texts
    assert any("Tweak one" in text and "[MODERATE]" in text for text in texts)


def test_batch_review_cancel_runs_nothing_and_logs_every_decline(qtbot, tmp_path, monkeypatch):
    from portablefix import restore_point

    window = _review_window(qtbot, tmp_path, monkeypatch, "run_review_cancel")
    monkeypatch.setattr(restore_point, "create_restore_point", lambda d: pytest.fail("no restore point for a cancelled batch"))
    reviews = _answer_review(monkeypatch, accept=False)
    _check(window, "tweak_one", "wipe_thing", "look_thing")

    window.run_selected_actions()

    assert window._batch_active is False and window._queue == []
    assert window.run_button.isEnabled()
    log_path = audit_log_path(tmp_path, "run_review_cancel")
    assert _executed_action_ids(log_path) == []
    shown = {i.subject: i.warning_text for i in reviews[0].review.items if i.warning_text}
    declined = _system_events(log_path, "risk_declined")
    assert {e["subject"]: e["warning_text"] for e in declined} == shown
    assert set(shown) == {"m02_cleanup/tweak_one", "m02_cleanup/wipe_thing"}
    assert all(e["warned"] is True and e["decision"] == "declined" for e in declined)
    [event] = _system_events(log_path, "batch_review")
    assert event["decision"] == "cancelled"
    assert "look-ran" not in window.console.toPlainText()


def test_batch_review_unticked_destructive_is_declined_rest_runs(qtbot, tmp_path, monkeypatch):
    window = _review_window(qtbot, tmp_path, monkeypatch, "run_review_untick")
    reviews = _answer_review(monkeypatch, tick=False)
    _check(window, "wipe_thing", "tweak_one")

    window.run_selected_actions()
    _wait_batch_idle(qtbot, window)

    log_path = audit_log_path(tmp_path, "run_review_untick")
    assert _executed_action_ids(log_path) == ["tweak_one"]
    [declined] = _system_events(log_path, "risk_declined")
    wipe = next(i for i in reviews[0].review.items if i.action_id == "wipe_thing")
    assert declined["subject"] == "m02_cleanup/wipe_thing" and declined["warning_text"] == wipe.warning_text
    # The declined DESTRUCTIVE action was the only reason for a restore point.
    assert _system_events(log_path, "restore_point") == []


def test_batch_review_of_only_destructive_actions_needs_a_tick_to_confirm(qtbot, tmp_path, monkeypatch):
    from portablefix import preflight
    from portablefix.gui.batch_review import BatchReviewDialog, build_review

    window = _review_window(qtbot, tmp_path, monkeypatch, "run_review_tick")
    items = [window._find_action("wipe_thing")]
    dialog = BatchReviewDialog(build_review(items, preflight.PreflightResult(), "en"), parent=window)
    # Unticked, "confirm" would mean running nothing - not a confirmation.
    assert not dialog.confirm_button.isEnabled()
    # Enter must never confirm by accident.
    assert dialog.cancel_button.isDefault() and not dialog.confirm_button.autoDefault()
    dialog.destructive_checkboxes["wipe_thing"].setChecked(True)
    assert dialog.confirm_button.isEnabled()
    dialog.confirm_button.click()
    decision = dialog.decision()
    assert decision.confirmed and [i.action_id for i in decision.accepted] == ["wipe_thing"]


def test_dry_run_shows_no_review_no_confirmation_and_makes_no_restore_point(qtbot, tmp_path, monkeypatch):
    from portablefix import restore_point
    from portablefix.gui.batch_review import BatchReviewDialog

    window = _review_window(qtbot, tmp_path, monkeypatch, "run_review_dry", dry_run=True)
    monkeypatch.setattr(BatchReviewDialog, "exec", lambda self: pytest.fail("no review in DRY-RUN"))
    monkeypatch.setattr(restore_point, "create_restore_point", lambda d: pytest.fail("no restore point in DRY-RUN"))
    monkeypatch.setattr(window, "_preflight_probes", lambda: pytest.fail("no pre-flight in DRY-RUN"))
    _check(window, "wipe_thing", "tweak_one", "reboot_thing")

    window.run_selected_actions()
    _wait_batch_idle(qtbot, window)

    assert "wipe-preview" in window.console.toPlainText()
    assert "wipe-ran" not in window.console.toPlainText()
    log_path = audit_log_path(tmp_path, "run_review_dry")
    assert sorted(_executed_action_ids(log_path)) == ["reboot_thing", "tweak_one", "wipe_thing"]
    assert _system_events(log_path, "batch_review") == []
    assert _system_events(log_path, "restore_point") == []


def test_safe_only_batch_starts_without_review_or_preflight(qtbot, tmp_path, monkeypatch):
    from portablefix import preflight
    from portablefix.gui.batch_review import BatchReviewDialog

    # Even a PC every check would flag: a SAFE batch changes nothing.
    bad = preflight.Probes(
        power=lambda: preflight.PowerStatus(True, 1), pending_reboot=lambda: ["cbs"],
        system_free_bytes=lambda: 1, is_admin=lambda: False, busy_tasks=lambda: ["x"],
    )
    window = _review_window(qtbot, tmp_path, monkeypatch, "run_review_safe", probes=bad)
    monkeypatch.setattr(BatchReviewDialog, "exec", lambda self: pytest.fail("nothing to review"))
    _check(window, "look_thing")

    window.run_selected_actions()
    _wait_batch_idle(qtbot, window)

    log_path = audit_log_path(tmp_path, "run_review_safe")
    assert _executed_action_ids(log_path) == ["look_thing"]
    assert _system_events(log_path, "batch_review") == []


def test_preflight_blocker_disables_confirm_until_overridden_and_logs_the_override(qtbot, tmp_path, monkeypatch):
    from portablefix import i18n, preflight
    from portablefix.gui.batch_review import BatchReviewDialog

    low_disk = preflight.Probes(system_free_bytes=lambda: 2 * 1024**3, is_admin=lambda: True)
    window = _review_window(qtbot, tmp_path, monkeypatch, "run_review_override", probes=low_disk)
    enabled_before = []

    def technician(self):
        enabled_before.append(self.confirm_button.isEnabled())
        self.override_checkbox.setChecked(True)
        self.confirm_button.click()
        return self.result()

    monkeypatch.setattr(BatchReviewDialog, "exec", technician)
    _check(window, "tweak_one")

    window.run_selected_actions()
    _wait_batch_idle(qtbot, window)

    assert enabled_before == [False]
    log_path = audit_log_path(tmp_path, "run_review_override")
    assert _executed_action_ids(log_path) == ["tweak_one"]
    [event] = _system_events(log_path, "batch_review")
    assert event["decision"] == "override" and event["warned"] is True
    assert "low_disk" in event["output"]
    assert event["warning_text"] == i18n.translate("preflight_low_disk", "en").format(free_gb="2.0", min_gb=5)


def test_preflight_blocker_without_override_cannot_start_the_batch(qtbot, tmp_path, monkeypatch):
    from portablefix import preflight

    pending = preflight.Probes(pending_reboot=lambda: ["cbs"], is_admin=lambda: True)
    window = _review_window(qtbot, tmp_path, monkeypatch, "run_review_blocked", probes=pending)
    reviews = _answer_review(monkeypatch)  # tries to confirm, never overrides
    _check(window, "reboot_thing")

    window.run_selected_actions()

    assert [i.code for i in reviews[0].review.preflight.blockers] == ["pending_reboot"]
    log_path = audit_log_path(tmp_path, "run_review_blocked")
    assert _executed_action_ids(log_path) == []
    assert window._batch_active is False
    [event] = _system_events(log_path, "batch_review")
    assert event["decision"] == "cancelled"
    assert [e["subject"] for e in _system_events(log_path, "risk_declined")] == ["m02_cleanup/reboot_thing"]


def test_busy_job_blocker_cannot_be_overridden(qtbot, tmp_path, monkeypatch):
    from portablefix import preflight

    busy = preflight.Probes(busy_tasks=lambda: ["winget"], is_admin=lambda: True)
    window = _review_window(qtbot, tmp_path, monkeypatch, "run_review_busy", probes=busy)
    reviews = _answer_review(monkeypatch, override=True)
    _check(window, "tweak_one")

    window.run_selected_actions()

    assert reviews[0].override_checkbox is None
    assert not reviews[0].confirm_button.isEnabled()
    assert _executed_action_ids(audit_log_path(tmp_path, "run_review_busy")) == []


def test_real_preflight_probes_use_the_window_elevation_and_jobs(qtbot, tmp_path):
    from portablefix import preflight

    base_dir = _make_base_dir(tmp_path, MODERATE_ACTIONS_YAML)
    window = MainWindow(assets_dir=base_dir, state_dir=base_dir, settings=Settings(language="en", dry_run=False), is_admin=False, run_id="run_review_admin")
    qtbot.addWidget(window)

    probes = window._preflight_probes()
    assert probes.is_admin() is False
    assert probes.busy_tasks() == []
    result = preflight.run_preflight(preflight.profile_for([window._find_action("risky")]), probes)
    assert "no_admin" in [i.code for i in result.blockers]


def test_warning_only_preflight_still_shows_review_for_a_safe_repair_batch(qtbot, tmp_path, monkeypatch):
    # A SAFE action in a REPAIR module gets a restore point - it changes the
    # system, so a pre-flight warning (battery) is worth one look.
    from portablefix import preflight, restore_point

    module_dir = tmp_path / "Modules" / "m03_disk"
    module_dir.mkdir(parents=True)
    (module_dir / "actions.yaml").write_text(
        "module_id: m03_disk\ncategory: REPAIR\nactions:\n"
        "  - id: scan_disk\n    label_sk: \"Sken\"\n    label_en: \"Scan\"\n    risk: SAFE\n"
        "    command: \"Write-Output 'scan-ran'\"\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(restore_point, "create_restore_point", lambda description: (True, ""))
    window = MainWindow(assets_dir=tmp_path, state_dir=tmp_path, settings=Settings(language="en", dry_run=False), is_admin=True, run_id="run_review_warn")
    qtbot.addWidget(window)
    monkeypatch.setattr(window, "_preflight_probes", lambda: preflight.Probes(power=lambda: preflight.PowerStatus(True, 80)))
    reviews = _answer_review(monkeypatch)
    _check(window, "scan_disk")

    window.run_selected_actions()
    _wait_batch_idle(qtbot, window)

    assert [i.code for i in reviews[0].review.preflight.warnings] == ["on_battery"]
    log_path = audit_log_path(tmp_path, "run_review_warn")
    assert _executed_action_ids(log_path) == ["scan_disk"]
    [event] = _system_events(log_path, "batch_review")
    assert event["decision"] == "confirmed" and "on_battery" in event["output"]


def test_select_all_keeps_excluded_actions_out_of_the_review(qtbot, tmp_path, monkeypatch):
    window = _review_window(qtbot, tmp_path, monkeypatch, "run_review_exclude")
    reviews = _answer_review(monkeypatch, accept=False)
    window._apply_selection(list(window._action_checkboxes), "all")

    window.run_selected_actions()

    ids = [i.action_id for i in reviews[0].review.items]
    assert "rescue_thing" not in ids
    assert set(ids) == {"wipe_thing", "tweak_one", "tweak_two", "reboot_thing", "look_thing"}


def test_review_confirmation_never_carries_over_to_a_later_dispatch(qtbot, tmp_path, monkeypatch):
    window = _review_window(qtbot, tmp_path, monkeypatch, "run_review_carry")
    _answer_review(monkeypatch)
    _check(window, "tweak_one")
    window.run_selected_actions()
    _wait_batch_idle(qtbot, window)
    assert window._reviewed_warnings == {}

    # A dispatch outside a reviewed batch falls back to its own question.
    asked = []
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda p, t, text, *a, **k: asked.append(text) or QMessageBox.No))
    module, action = window._find_action("tweak_one")
    window._dispatch_action(module, action)
    assert len(asked) == 1 and "Tweak one" in asked[0]
    declined = _system_events(audit_log_path(tmp_path, "run_review_carry"), "risk_declined")
    assert declined[-1]["warning_text"] == asked[0]


def test_batch_review_never_calls_a_destructive_action_with_undo_irreversible(qtbot, tmp_path, monkeypatch):
    from portablefix import i18n, preflight
    from portablefix.gui.batch_review import BatchReviewDialog, build_review
    from portablefix.models import ActionDef, ModuleDef, RiskLevel

    window = _review_window(qtbot, tmp_path, monkeypatch, "run_review_undo_destructive")
    action = ActionDef(
        id="salvage", label_sk="S", label_en="Salvage", risk=RiskLevel.DESTRUCTIVE,
        command="x", undo_command="y",
    )
    review = build_review([(ModuleDef("m04_integrity", [action]), action)], preflight.PreflightResult(), "en")
    [item] = review.items
    assert item.irreversible is False
    assert i18n.translate("review_note_destructive_undo", "en") in item.warning_text
    assert i18n.translate("review_note_destructive", "en") not in item.warning_text
    dialog = BatchReviewDialog(review, parent=window)
    assert dialog.destructive_checkboxes["salvage"].text() == i18n.translate("review_destructive_undo_tick", "en")


def test_batch_review_confirmed_after_the_window_closed_starts_nothing(qtbot, tmp_path, monkeypatch):
    from portablefix.gui.batch_review import BatchReviewDialog

    window = _review_window(qtbot, tmp_path, monkeypatch, "run_review_closed")

    def close_then_confirm(self):
        window._closed = True
        self.confirm_button.click()
        return self.result()

    monkeypatch.setattr(BatchReviewDialog, "exec", close_then_confirm)
    _check(window, "tweak_one")

    window.run_selected_actions()
    window._closed = False  # let qtbot's teardown close it normally

    assert window._batch_active is False
    log_path = audit_log_path(tmp_path, "run_review_closed")
    assert _executed_action_ids(log_path) == []
    [event] = _system_events(log_path, "batch_review")
    assert event["decision"] == "cancelled"
    assert [e["subject"] for e in _system_events(log_path, "risk_declined")] == ["m02_cleanup/tweak_one"]
