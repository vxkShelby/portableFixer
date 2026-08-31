import json
from pathlib import Path

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


def _make_base_dir(tmp_path: Path) -> Path:
    module_dir = tmp_path / "Modules" / "m01_diagnostics"
    module_dir.mkdir(parents=True)
    (module_dir / "actions.yaml").write_text(ACTIONS_YAML, encoding="utf-8")
    return tmp_path


def test_main_window_loads_m01_actions(qtbot, tmp_path):
    base_dir = _make_base_dir(tmp_path)
    window = MainWindow(base_dir=base_dir, settings=Settings(), is_admin=True, run_id="testrun")
    qtbot.addWidget(window)
    assert "hello" in window._action_checkboxes


def test_run_selected_action_writes_console_and_audit_log(qtbot, tmp_path):
    base_dir = _make_base_dir(tmp_path)
    settings = Settings(language="sk", dry_run=False)
    window = MainWindow(base_dir=base_dir, settings=settings, is_admin=True, run_id="testrun")
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
    readonly_window = MainWindow(base_dir=base_dir, settings=Settings(), is_admin=False, run_id="testrun")
    qtbot.addWidget(readonly_window)
    readonly_window.show()
    assert readonly_window.restart_admin_button.isVisible()

    admin_window = MainWindow(base_dir=base_dir, settings=Settings(), is_admin=True, run_id="testrun")
    qtbot.addWidget(admin_window)
    admin_window.show()
    assert not admin_window.restart_admin_button.isVisible()
