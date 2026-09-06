import json
import os
import shutil
from pathlib import Path

from PySide6.QtWidgets import QMessageBox

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


def _make_base_dir(tmp_path: Path, yaml_text: str = ACTIONS_YAML) -> Path:
    module_dir = tmp_path / "Modules" / "m01_diagnostics"
    module_dir.mkdir(parents=True)
    (module_dir / "actions.yaml").write_text(yaml_text, encoding="utf-8")
    return tmp_path


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

    window.category_list.setCurrentRow(1)
    window._action_checkboxes["clean_action"].setFocus()
    # hasFocus() only reflects reality once the OS has actually handed this
    # window keyboard focus, which is asynchronous even after activateWindow().
    qtbot.waitUntil(lambda: window._action_checkboxes["clean_action"].hasFocus(), timeout=5000)

    window._on_toggle_language()

    assert window.category_list.currentRow() == 1
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

    assert calls[0][1] is None


def test_language_toggle_flips_language_and_labels(qtbot, tmp_path):
    base_dir = _make_base_dir(tmp_path)
    settings = Settings(language="sk")
    window = MainWindow(assets_dir=base_dir, state_dir=base_dir, settings=settings, is_admin=True, run_id="testrun")
    qtbot.addWidget(window)

    assert window.settings.language == "sk"
    assert window.run_button.text() == "Spustit vybrane"

    window.language_button.click()

    assert window.settings.language == "en"
    assert window.run_button.text() == "Run selected"
    assert window.language_button.text() == "EN"


def test_moderate_risk_action_declined_does_not_run_or_log(qtbot, tmp_path, monkeypatch):
    base_dir = _make_base_dir(tmp_path, MODERATE_ACTIONS_YAML)
    settings = Settings(language="sk", dry_run=False)
    window = MainWindow(assets_dir=base_dir, state_dir=base_dir, settings=settings, is_admin=True, run_id="testrun")
    qtbot.addWidget(window)
    window._action_checkboxes["risky"].setChecked(True)

    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.No)

    window.run_selected_actions()

    qtbot.wait(300)
    assert "risky-ran" not in window.console.toPlainText()
    assert not audit_log_path(base_dir, "testrun").exists()


def test_moderate_risk_action_accepted_runs_and_logs(qtbot, tmp_path, monkeypatch):
    base_dir = _make_base_dir(tmp_path, MODERATE_ACTIONS_YAML)
    settings = Settings(language="sk", dry_run=False)
    window = MainWindow(assets_dir=base_dir, state_dir=base_dir, settings=settings, is_admin=True, run_id="testrun")
    qtbot.addWidget(window)
    window._action_checkboxes["risky"].setChecked(True)

    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)

    window.run_selected_actions()

    log_path = audit_log_path(base_dir, "testrun")
    qtbot.waitUntil(lambda: log_path.exists() and log_path.read_text(encoding="utf-8").strip() != "", timeout=10000)

    assert "risky-ran" in window.console.toPlainText()
    entry = json.loads(log_path.read_text(encoding="utf-8").splitlines()[0])
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


def test_destructive_action_declined_at_hard_confirm_is_not_run(qtbot, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    from portablefix import restore_point

    monkeypatch.setattr(restore_point, "create_restore_point", lambda description: (True, ""))
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **kw: QMessageBox.No))

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

    log_content = log_path.read_text(encoding="utf-8")
    assert "risky_thing" not in log_content
    assert "safe_thing" in log_content


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
    from PySide6.QtWidgets import QMessageBox

    from portablefix import restore_point

    monkeypatch.setattr(restore_point, "create_restore_point", lambda description: (True, ""))
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **kw: QMessageBox.Yes))

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
    qtbot.waitUntil(lambda: log_path.exists() and "risky_thing" in log_path.read_text(encoding="utf-8"), timeout=10000)
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
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **kw: QMessageBox.No))

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
    assert "risky_thing" not in log_path.read_text(encoding="utf-8")


def test_category_list_deduplicates_same_category_across_modules(qtbot, tmp_path):
    _write_module(tmp_path, "m01_diagnostics", "DIAGNOSTICS", "a1")
    _write_module(tmp_path, "m02_other", "DIAGNOSTICS", "a2")
    settings = Settings(language="en", dry_run=True)
    window = MainWindow(assets_dir=tmp_path, state_dir=tmp_path, settings=settings, is_admin=True, run_id="run_cat1")
    qtbot.addWidget(window)
    # +1 for the "Risk: SAFE" tab appended after the categories (both test
    # actions are risk SAFE).
    assert window.category_list.count() == 2
    assert window.category_list.item(0).text() == "Diagnostics"


def test_category_list_shows_distinct_entries_for_different_categories(qtbot, tmp_path):
    _write_module(tmp_path, "m01_diagnostics", "DIAGNOSTICS", "a1")
    _write_module(tmp_path, "m04_integrity", "REPAIR", "a2")
    settings = Settings(language="en", dry_run=True)
    window = MainWindow(assets_dir=tmp_path, state_dir=tmp_path, settings=settings, is_admin=True, run_id="run_cat2")
    qtbot.addWidget(window)
    # +1 for the "Risk: SAFE" tab appended after the categories (both test
    # actions are risk SAFE).
    assert window.category_list.count() == 3
    labels = {window.category_list.item(i).text() for i in range(window.category_list.count())}
    assert labels == {"Diagnostics", "System repair", "Risk: SAFE"}


def test_category_click_shows_only_selected_category_group(qtbot, tmp_path):
    from portablefix.models import ModuleCategory

    _write_module(tmp_path, "m01_diagnostics", "DIAGNOSTICS", "a1")
    _write_module(tmp_path, "m04_integrity", "REPAIR", "a2")
    settings = Settings(language="en", dry_run=True)
    window = MainWindow(assets_dir=tmp_path, state_dir=tmp_path, settings=settings, is_admin=True, run_id="run_filter")
    qtbot.addWidget(window)

    assert window.category_list.currentRow() == 0
    assert not window._category_groups[ModuleCategory.DIAGNOSTICS].isHidden()
    assert window._category_groups[ModuleCategory.REPAIR].isHidden()

    window.category_list.setCurrentRow(1)
    assert window._category_groups[ModuleCategory.DIAGNOSTICS].isHidden()
    assert not window._category_groups[ModuleCategory.REPAIR].isHidden()


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
    reports_dir = tmp_path / "Reports"
    qtbot.waitUntil(lambda: reports_dir.exists(), timeout=10000)
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
    # MODERATE-risk actions trigger a QMessageBox.question confirmation dialog
    # in _dispatch_action; auto-confirm so the test doesn't hang on a real modal.
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **kw: QMessageBox.Yes))

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


def test_search_box_hints_when_matches_exist_only_in_another_view(qtbot, tmp_path):
    base_dir = _make_base_dir(tmp_path, MIXED_RISK_ACTIONS_YAML)
    window = MainWindow(assets_dir=base_dir, state_dir=base_dir, settings=Settings(language="en"), is_admin=True, run_id="run_search_hint")
    qtbot.addWidget(window)
    window.category_list.setCurrentRow(1)  # SAFE risk tab - only safe_one lives here

    window.search_box.setText("moderate")

    assert "1 match" in window.statusBar().currentMessage()


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


def test_status_bar_shows_selection_count_and_highest_risk(qtbot, tmp_path):
    base_dir = _make_base_dir(tmp_path, _TWO_ACTIONS_YAML)
    window = MainWindow(assets_dir=base_dir, state_dir=base_dir, settings=Settings(language="en"), is_admin=True, run_id="run_statusbar")
    qtbot.addWidget(window)

    assert window.statusBar().currentMessage() == "Nothing selected"

    window._action_checkboxes["temp_cleanup"].setChecked(True)
    assert window.statusBar().currentMessage() == "Selected: 1  |  Highest risk: SAFE"

    window._action_checkboxes["firewall_check"].setChecked(True)
    assert window.statusBar().currentMessage() == "Selected: 2  |  Highest risk: MODERATE"


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


def test_update_button_click_confirmed_downloads_and_applies_update(qtbot, tmp_path, monkeypatch):
    from portablefix.updater import UpdateInfo
    from portablefix.gui import main_window as mw_module

    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Yes))
    fake_exe = tmp_path / "PortableFix.new.exe"
    fake_exe.write_bytes(b"x")
    monkeypatch.setattr(mw_module.updater, "download_update", lambda info, dest: fake_exe)
    monkeypatch.setattr(mw_module.updater, "is_writable", lambda p: True)
    applied = {}
    monkeypatch.setattr(mw_module.updater, "apply_update", lambda *a, **k: applied.setdefault("called", True))

    base_dir = _make_base_dir(tmp_path)
    window = MainWindow(assets_dir=base_dir, state_dir=base_dir, settings=Settings(language="en"), is_admin=True, run_id="run_update7")
    qtbot.addWidget(window)
    monkeypatch.setattr(window, "_quit_app", lambda: applied.setdefault("quit_called", True))
    window._on_update_check_finished(UpdateInfo(version="9.9.9", package_url="https://x", sha256_url=None, notes=""))

    window.update_button.click()

    qtbot.waitUntil(lambda: applied.get("called") is True, timeout=5000)
    assert applied.get("quit_called") is True


def test_update_download_failure_shows_error_and_reenables_button(qtbot, tmp_path, monkeypatch):
    from portablefix.updater import UpdateInfo
    from portablefix.gui import main_window as mw_module

    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Yes))

    def raise_it(info, dest):
        raise Exception("boom")

    monkeypatch.setattr(mw_module.updater, "download_update", raise_it)

    base_dir = _make_base_dir(tmp_path)
    window = MainWindow(assets_dir=base_dir, state_dir=base_dir, settings=Settings(language="en"), is_admin=True, run_id="run_update8")
    qtbot.addWidget(window)
    window._on_update_check_finished(UpdateInfo(version="9.9.9", package_url="https://x", sha256_url=None, notes=""))

    window.update_button.click()

    qtbot.waitUntil(lambda: window.update_button.isEnabled() is True, timeout=5000)
    assert window.update_banner_label.text() == "Downloading the update failed. Try again later."


def test_update_not_writable_shows_error_without_applying(qtbot, tmp_path, monkeypatch):
    from portablefix.updater import UpdateInfo
    from portablefix.gui import main_window as mw_module

    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Yes))
    fake_exe = tmp_path / "PortableFix.new.exe"
    fake_exe.write_bytes(b"x")
    monkeypatch.setattr(mw_module.updater, "download_update", lambda info, dest: fake_exe)
    monkeypatch.setattr(mw_module.updater, "is_writable", lambda p: False)
    applied = {}
    monkeypatch.setattr(mw_module.updater, "apply_update", lambda *a, **k: applied.setdefault("called", True))

    base_dir = _make_base_dir(tmp_path)
    window = MainWindow(assets_dir=base_dir, state_dir=base_dir, settings=Settings(language="en"), is_admin=True, run_id="run_update9")
    qtbot.addWidget(window)
    window._on_update_check_finished(UpdateInfo(version="9.9.9", package_url="https://x", sha256_url=None, notes=""))

    window.update_button.click()

    qtbot.waitUntil(lambda: window.update_banner_label.text() == "The app folder is not writable, the update cannot be applied.", timeout=5000)
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


def test_update_button_click_does_nothing_during_active_batch(qtbot, tmp_path):
    from portablefix.updater import UpdateInfo

    base_dir = _make_base_dir(tmp_path)
    window = MainWindow(assets_dir=base_dir, state_dir=base_dir, settings=Settings(language="en"), is_admin=True, run_id="run_update_batch_guard")
    qtbot.addWidget(window)
    window._on_update_check_finished(UpdateInfo(version="9.9.9", package_url="https://x", sha256_url=None, notes=""))
    window._batch_active = True

    window.update_button.click()

    assert window._update_download_runner is None


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


def test_update_restart_declined_reverts_banner_without_applying(qtbot, tmp_path, monkeypatch):
    from portablefix.updater import UpdateInfo
    from portablefix.gui import main_window as mw_module

    calls = {"n": 0}

    def fake_question(*a, **k):
        calls["n"] += 1
        return QMessageBox.Yes if calls["n"] == 1 else QMessageBox.No

    monkeypatch.setattr(QMessageBox, "question", staticmethod(fake_question))
    fake_exe = tmp_path / "PortableFix.new.exe"
    fake_exe.write_bytes(b"x")
    monkeypatch.setattr(mw_module.updater, "download_update", lambda info, dest: fake_exe)
    monkeypatch.setattr(mw_module.updater, "is_writable", lambda p: True)
    applied = {}
    monkeypatch.setattr(mw_module.updater, "apply_update", lambda *a, **k: applied.setdefault("called", True))

    base_dir = _make_base_dir(tmp_path)
    window = MainWindow(assets_dir=base_dir, state_dir=base_dir, settings=Settings(language="en"), is_admin=True, run_id="run_update10")
    qtbot.addWidget(window)
    monkeypatch.setattr(window, "_quit_app", lambda: applied.setdefault("quit_called", True))
    window._on_update_check_finished(UpdateInfo(version="9.9.9", package_url="https://x", sha256_url=None, notes=""))

    window.update_button.click()

    qtbot.waitUntil(lambda: window.update_banner_label.text() == "Version 9.9.9 is available", timeout=5000)
    assert applied.get("called") is None
    assert applied.get("quit_called") is None


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
    from portablefix import updater as updater_module

    class _FakeRunner:
        def __init__(self):
            self.wait_calls = []

        def wait(self, timeout_ms):
            self.wait_calls.append(timeout_ms)
            return True

    base_dir = _make_base_dir(tmp_path)
    window = MainWindow(assets_dir=base_dir, state_dir=base_dir, settings=Settings(language="en"), is_admin=True, run_id="run_close_slow")
    qtbot.addWidget(window)

    # The speed test and update download each make one blocking,
    # uninterruptible network call - closeEvent can't cancel them, so it
    # must wait long enough to cover their real worst-case duration instead
    # of the 5s used for everything else, or it risks destroying a live
    # QThread.
    speed_test_runner = _FakeRunner()
    update_download_runner = _FakeRunner()
    window._speed_test_runner = speed_test_runner
    window._update_download_runner = update_download_runner

    window.close()

    assert speed_test_runner.wait_calls == [25_000]
    assert update_download_runner.wait_calls == [updater_module.DOWNLOAD_TIMEOUT_SEC * 1000 + 5_000]


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
