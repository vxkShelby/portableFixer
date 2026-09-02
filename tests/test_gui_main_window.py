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

    monkeypatch.setattr(restore_point, "create_restore_point", lambda description: True)
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
    qtbot.waitUntil(lambda: log_path.exists() and log_path.read_text(encoding="utf-8").strip() != "", timeout=10000)

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

    qtbot.waitUntil(lambda: window.run_button.isEnabled() is True, timeout=10000)


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

    monkeypatch.setattr(restore_point, "create_restore_point", lambda description: True)
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


def test_restore_point_failure_declined_skips_remaining_destructive_but_runs_safe(qtbot, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    from portablefix import restore_point

    monkeypatch.setattr(restore_point, "create_restore_point", lambda description: False)
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
    assert window.category_list.count() == 1
    assert window.category_list.item(0).text() == "Diagnostics"


def test_category_list_shows_distinct_entries_for_different_categories(qtbot, tmp_path):
    _write_module(tmp_path, "m01_diagnostics", "DIAGNOSTICS", "a1")
    _write_module(tmp_path, "m04_integrity", "REPAIR", "a2")
    settings = Settings(language="en", dry_run=True)
    window = MainWindow(assets_dir=tmp_path, state_dir=tmp_path, settings=settings, is_admin=True, run_id="run_cat2")
    qtbot.addWidget(window)
    assert window.category_list.count() == 2
    labels = {window.category_list.item(i).text() for i in range(window.category_list.count())}
    assert labels == {"Diagnostics", "System repair"}


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
        return True

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

    monkeypatch.setattr(restore_point, "create_restore_point", lambda description: False)
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

    monkeypatch.setattr(restore_point, "create_restore_point", lambda description: True)

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

    monkeypatch.setattr(restore_point, "create_restore_point", lambda description: True)

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

    monkeypatch.setattr(restore_point, "create_restore_point", lambda description: True)

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

    monkeypatch.setattr(restore_point, "create_restore_point", lambda description: True)
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

    info = UpdateInfo(version="9.9.9", download_url="https://x", sha256_url=None, notes="")
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

    window._on_update_check_finished(UpdateInfo(version="9.9.9", download_url="https://x", sha256_url=None, notes=""))
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
    window._on_update_check_finished(UpdateInfo(version="9.9.9", download_url="https://x", sha256_url=None, notes=""))

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
    window._on_update_check_finished(UpdateInfo(version="9.9.9", download_url="https://x", sha256_url=None, notes=""))

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
    window._on_update_check_finished(UpdateInfo(version="9.9.9", download_url="https://x", sha256_url=None, notes=""))

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
    window._on_update_check_finished(UpdateInfo(version="9.9.9", download_url="https://x", sha256_url=None, notes=""))

    window.update_button.click()

    qtbot.waitUntil(lambda: window.update_banner_label.text() == "The app folder is not writable, the update cannot be applied.", timeout=5000)
    assert applied.get("called") is None


def test_language_toggle_mid_download_keeps_buttons_disabled(qtbot, tmp_path):
    from portablefix.updater import UpdateInfo

    base_dir = _make_base_dir(tmp_path)
    window = MainWindow(assets_dir=base_dir, state_dir=base_dir, settings=Settings(language="en"), is_admin=True, run_id="run_toggle_mid_dl")
    qtbot.addWidget(window)
    window._on_update_check_finished(UpdateInfo(version="9.9.9", download_url="https://x", sha256_url=None, notes=""))

    # Simulate a download in progress (mirrors what _on_update_button_clicked
    # sets before starting the QThread) without actually starting one.
    window._update_in_progress = True
    window.update_button.setEnabled(False)
    window.update_dismiss_button.setEnabled(False)

    window._on_toggle_language()

    assert window.update_button.isEnabled() is False
    assert window.update_dismiss_button.isEnabled() is False


def test_update_button_click_does_nothing_during_active_batch(qtbot, tmp_path):
    from portablefix.updater import UpdateInfo

    base_dir = _make_base_dir(tmp_path)
    window = MainWindow(assets_dir=base_dir, state_dir=base_dir, settings=Settings(language="en"), is_admin=True, run_id="run_update_batch_guard")
    qtbot.addWidget(window)
    window._on_update_check_finished(UpdateInfo(version="9.9.9", download_url="https://x", sha256_url=None, notes=""))
    window._batch_active = True

    window.update_button.click()

    assert window._update_download_runner is None


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
    window._on_update_check_finished(UpdateInfo(version="9.9.9", download_url="https://x", sha256_url=None, notes=""))

    window.update_button.click()

    qtbot.waitUntil(lambda: window.update_banner_label.text() == "Version 9.9.9 is available", timeout=5000)
    assert applied.get("called") is None
    assert applied.get("quit_called") is None
