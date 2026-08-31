import json
from pathlib import Path

from PySide6.QtWidgets import QMessageBox

from portablefix import elevation
from portablefix.audit_log import audit_log_path
from portablefix.gui.main_window import MainWindow
from portablefix.settings import Settings

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
