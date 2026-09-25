"""GUI side of over-the-shoulder elevation (research G25): the banner, the
prelude handed to every action, the audit fields and undo.ps1. A module of
its own, run one test per process like the other GUI tests."""

import json
from types import SimpleNamespace

from portablefix import target_user
from portablefix.audit_log import audit_log_path
from portablefix.gui.main_window import MainWindow
from portablefix.settings import Settings
from portablefix.target_user import TargetUser

TECH = "S-1-5-21-1111-2222-3333-1001"
CLIENT = "S-1-5-21-1111-2222-3333-1002"
CLIENT_HIVE = f"Registry::HKEY_USERS\\{CLIENT}"
RUN_ID = "20260925T100000-g25a"

DIFFERENT = TargetUser(
    status=target_user.DIFFERENT, process_sid=TECH, process_user="PC\\technik",
    target_sid=CLIENT, target_user="PC\\klient", session_id=1,
)
SAME = TargetUser(
    status=target_user.SAME, process_sid=TECH, process_user="PC\\technik",
    target_sid=TECH, target_user="PC\\technik", session_id=1,
)
AMBIGUOUS = TargetUser(
    status=target_user.AMBIGUOUS, process_sid=TECH, process_user="PC\\technik",
    target_sid=TECH, target_user="PC\\technik", session_id=1,
)


def _window(qtbot, tmp_path, target=None, language="sk", dry_run=False):
    module_dir = tmp_path / "Modules" / "m13_debloat"
    module_dir.mkdir(parents=True, exist_ok=True)
    (module_dir / "actions.yaml").write_text(
        "module_id: m13_debloat\n"
        "category: CLEANUP\n"
        "actions:\n"
        "  - {id: user_tweak, label_sk: Tweak, label_en: Tweak, risk: SAFE,"
        " command: \"Write-Output $__pfUserHive\","
        " undo_command: \"Remove-ItemProperty -Path ($__pfUserHive + '\\\\X') -Name Y\"}\n"
        "  - {id: machine_tweak, label_sk: Stroj, label_en: Machine, risk: SAFE,"
        " command: \"Write-Output 1\", undo_command: \"Write-Output undo-machine\"}\n",
        encoding="utf-8",
    )
    window = MainWindow(
        assets_dir=tmp_path, state_dir=tmp_path, settings=Settings(language=language, dry_run=dry_run),
        is_admin=True, run_id=RUN_ID, target=target,
    )
    qtbot.addWidget(window)
    return window


def _entries(tmp_path):
    path = audit_log_path(tmp_path, RUN_ID)
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_banner_names_the_signed_in_user_when_elevated_as_someone_else(qtbot, tmp_path):
    window = _window(qtbot, tmp_path, DIFFERENT)
    banner = window.target_user_banner
    assert not banner.isHidden()
    assert banner.text() == (
        "Nastavenia používateľa pôjdu do profilu PC\\klient (prihlásený), nie do profilu technika (PC\\technik)."
    )
    assert f"HKEY_USERS\\{CLIENT}" in banner.toolTip()
    assert banner.accessibleName() == banner.text()


def test_banner_is_hidden_when_the_desktop_user_runs_portablefix(qtbot, tmp_path):
    window = _window(qtbot, tmp_path, SAME)
    assert window.target_user_banner.isHidden()
    assert window.target_user_banner.text() == ""


def test_banner_is_hidden_when_detection_did_not_run(qtbot, tmp_path):
    # An UNKNOWN target (detection failed, or off Windows) must stay silent.
    window = _window(qtbot, tmp_path, TargetUser())
    assert window.target_user_banner.isHidden()


def test_window_detects_the_target_itself_when_none_is_handed_over(qtbot, tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(target_user, "detect", lambda: calls.append(1) or DIFFERENT)
    window = _window(qtbot, tmp_path, None)
    assert calls == [1]
    assert window.target_user is DIFFERENT
    assert not window.target_user_banner.isHidden()


def test_banner_warns_when_the_signed_in_user_is_uncertain(qtbot, tmp_path):
    window = _window(qtbot, tmp_path, AMBIGUOUS, language="en")
    assert not window.target_user_banner.isHidden()
    assert "Could not tell for sure who is signed in" in window.target_user_banner.text()
    assert "PC\\technik" in window.target_user_banner.text()


def test_banner_survives_a_language_toggle(qtbot, tmp_path):
    window = _window(qtbot, tmp_path, DIFFERENT)
    window._on_toggle_language()
    assert not window.target_user_banner.isHidden()
    assert window.target_user_banner.text().startswith("User settings will go to the profile of PC\\klient")


def test_action_gets_the_prelude_and_its_audit_entry_the_target(qtbot, tmp_path, monkeypatch):
    from portablefix.gui import main_window as mw

    plans = []

    class FakeRunner:
        def __init__(self, plan, parent=None, **kwargs):
            plans.append(plan)
            self.output_line = SimpleNamespace(connect=lambda slot: None)
            self._slots = []
            self.finished_with_code = SimpleNamespace(connect=self._slots.append)
            self.captured_output = []

        def start(self):
            for slot in self._slots:
                slot(0)

        def cancel(self):
            pass

        def wait(self, *args):
            return True

    monkeypatch.setattr(mw, "ActionRunner", FakeRunner)
    window = _window(qtbot, tmp_path, DIFFERENT)
    monkeypatch.setattr(window, "_run_next", lambda: None)
    module, action = window._find_action("user_tweak")

    window._dispatch_action(module, action)

    [plan] = plans
    assert plan.argv[-1].startswith(f"$__pfUserHive = '{CLIENT_HIVE}'; $__pfUserSid = '{CLIENT}'; ")
    [entry] = [e for e in _entries(tmp_path) if e["action_id"] == "user_tweak"]
    assert entry["target_user"] == "PC\\klient"
    assert entry["target_user_sid"] == CLIENT
    assert entry["target_user_status"] == "different"


def test_undo_script_restores_the_same_profile(qtbot, tmp_path, monkeypatch):
    window = _window(qtbot, tmp_path, DIFFERENT)
    monkeypatch.setattr(window, "_run_next", lambda: None)
    runner = SimpleNamespace(captured_output=["done"])

    window._on_action_finished("m13_debloat", "user_tweak", "cmd", 0, runner)
    window._on_action_finished("m13_debloat", "machine_tweak", "cmd", 0, runner)

    lines = (tmp_path / "Backups" / RUN_ID / "undo.ps1").read_text(encoding="utf-8-sig").splitlines()
    # undo.ps1 may run later as anyone - it carries the hive itself.
    assert f"$__pfUserHive = '{CLIENT_HIVE}'; $__pfUserSid = '{CLIENT}'; Remove-ItemProperty" in "\n".join(lines)
    # Steps that do not use it stay as they were.
    assert "Write-Output undo-machine" in lines


def test_system_events_and_panel_actions_record_the_target(qtbot, tmp_path):
    window = _window(qtbot, tmp_path, DIFFERENT)

    window._log_system_event("risk_declined", None, "declined", decision="declined")
    window._log_panel_action("m11_uninstaller", "uninstall", "cmd", 0, "", False, "MODERATE")

    entries = _entries(tmp_path)
    assert len(entries) == 2
    for entry in entries:
        assert (entry["target_user_sid"], entry["target_user_status"]) == (CLIENT, "different")


def test_undo_step_without_a_known_target_clears_an_earlier_steps_hive(qtbot, tmp_path, monkeypatch):
    # undo.ps1 runs every step in one scope: a step recorded while the
    # target was UNKNOWN must not inherit the hive an earlier step's prelude
    # set (a batch continued after a restart with a different detection).
    window = _window(qtbot, tmp_path, DIFFERENT)
    monkeypatch.setattr(window, "_run_next", lambda: None)
    runner = SimpleNamespace(captured_output=["done"])
    window._on_action_finished("m13_debloat", "user_tweak", "cmd", 0, runner)
    window.target_user = TargetUser()
    window._on_action_finished("m13_debloat", "user_tweak", "cmd", 0, runner)

    text = (tmp_path / "Backups" / RUN_ID / "undo.ps1").read_text(encoding="utf-8-sig")
    assert "Remove-Variable __pfUserHive,__pfUserSid -EA SilentlyContinue; Remove-ItemProperty" in text
    assert f"$__pfUserHive = '{CLIENT_HIVE}'; $__pfUserSid = '{CLIENT}'; Remove-ItemProperty" in text


def test_integrity_guard_entry_records_the_target(qtbot, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    window = _window(qtbot, tmp_path, DIFFERENT)
    monkeypatch.setattr(window, "_app_dir_intact", lambda: False)
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: QMessageBox.Ok)
    window._queue = ["user_tweak"]

    window._run_next()

    [entry] = [e for e in _entries(tmp_path) if e["action_id"] == "integrity_guard"]
    assert (entry["target_user"], entry["target_user_sid"], entry["target_user_status"]) == (
        "PC\\klient", CLIENT, "different",
    )
