"""GUI side of research G20's intake / hand-over forms, the manual work
timer, the batch duration and the report branding - reached from the Job
details dialog. Run one test per process, like the other GUI tests."""

import json
import time

from PySide6.QtCore import QDateTime

from portablefix import branding, intake, report
from portablefix.audit_log import audit_log_path
from portablefix.gui import job_forms
from portablefix.gui.main_window import MainWindow
from portablefix.settings import Settings, load_settings

RUN_ID = "20260925T120000-g20a"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + b"\x01" * 32


def _window(qtbot, tmp_path, settings=None, run_id=RUN_ID):
    module_dir = tmp_path / "Modules" / "m01_diagnostics"
    module_dir.mkdir(parents=True, exist_ok=True)
    (module_dir / "actions.yaml").write_text(
        "module_id: m01_diagnostics\n"
        "actions:\n"
        "  - {id: one, label_sk: Jedna, label_en: One, risk: SAFE, command: \"Write-Output 1\"}\n",
        encoding="utf-8",
    )
    window = MainWindow(
        assets_dir=tmp_path, state_dir=tmp_path, settings=settings or Settings(language="en"),
        is_admin=True, run_id=run_id,
    )
    qtbot.addWidget(window)
    return window


def _system_events(tmp_path, kind, run_id=RUN_ID):
    path = audit_log_path(tmp_path, run_id)
    if not path.exists():
        return []
    entries = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return [e for e in entries if e["module_id"] == "_system" and e["action_id"] == kind]


def _fill_forms(dialog: job_forms.FormsDialog) -> None:
    dialog.problem_edit.setPlainText("Pomalý štart")
    dialog.condition_checkboxes["scratches"].setChecked(True)
    dialog.condition_checkboxes["cracked_screen"].setChecked(True)
    dialog.condition_edit.setPlainText("Škrabanec na veku")
    dialog.accessories_edit.setText("nabíjačka")
    dialog.backup_combo.setCurrentIndex(dialog.backup_combo.findData("waiver"))
    dialog.password_combo.setCurrentIndex(dialog.password_combo.findData("reset"))
    dialog.check_combos["wifi"].setCurrentIndex(dialog.check_combos["wifi"].findData("pass"))
    dialog.check_combos["usb"].setCurrentIndex(dialog.check_combos["usb"].findData("fail"))
    dialog.handed_to_edit.setText("Ján Novák")
    dialog.handed_at_edit.setDateTime(QDateTime.fromString("2026-09-25 16:30", job_forms.HANDED_AT_FORMAT))


def test_job_dialog_offers_the_forms_branding_and_timer(qtbot, tmp_path):
    window = _window(qtbot, tmp_path, Settings(language="sk"))
    window._open_job_dialog()
    texts = {b.text() for b in window._job_dialog.findChildren(job_forms.QPushButton)}
    assert {"Prevzatie / odovzdanie…", "Branding reportu…", "Spustiť"} <= texts
    assert window._work_timer_widget.time_label.text() == "0:00:00"
    # Both open over the Job details window.
    buttons = {b.text(): b for b in window._job_dialog.findChildren(job_forms.QPushButton)}
    buttons["Prevzatie / odovzdanie…"].click()
    assert window._forms_dialog.parent() is window._job_dialog
    window._forms_dialog.reject()
    buttons["Branding reportu…"].click()
    assert window._branding_dialog.parent() is window._job_dialog
    window._branding_dialog.reject()


def test_forms_are_saved_as_system_events_and_shown_again(qtbot, tmp_path):
    window = _window(qtbot, tmp_path)
    window._open_forms_dialog()
    dialog = window._forms_dialog
    assert dialog.tabs.count() == 2
    assert [dialog.tabs.tabText(i) for i in range(2)] == ["Intake", "Hand-over"]
    _fill_forms(dialog)
    dialog.accept()

    [intake_event] = _system_events(tmp_path, intake.INTAKE_EVENT)
    [outtake_event] = _system_events(tmp_path, intake.OUTTAKE_EVENT)
    saved_intake = json.loads(intake_event["output"])
    assert saved_intake == {
        "problem": "Pomalý štart", "condition": "Škrabanec na veku",
        "condition_flags": ["scratches", "cracked_screen"], "accessories": "nabíjačka",
        "backup": "waiver", "password_handling": "reset",
    }
    assert json.loads(outtake_event["output"]) == {
        "checks": {"wifi": "pass", "usb": "fail"}, "handed_to": "Ján Novák", "handed_at": "2026-09-25 16:30",
    }

    # Opened again, the dialog shows what was saved.
    window._open_forms_dialog()
    again = window._forms_dialog
    assert again.problem_edit.toPlainText() == "Pomalý štart"
    assert again.condition_checkboxes["cracked_screen"].isChecked()
    assert not again.condition_checkboxes["liquid_damage"].isChecked()
    assert again.backup_combo.currentData() == "waiver"
    assert again.check_combos["usb"].currentData() == "fail"
    assert again.check_combos["camera"].currentData() == ""
    assert again.handed_at_edit.dateTime().toString(job_forms.HANDED_AT_FORMAT) == "2026-09-25 16:30"
    # OK without a change adds no copy to the audit log.
    again.accept()
    assert len(_system_events(tmp_path, intake.INTAKE_EVENT)) == 1
    assert len(_system_events(tmp_path, intake.OUTTAKE_EVENT)) == 1


def test_forms_have_no_password_input(qtbot, tmp_path):
    window = _window(qtbot, tmp_path)
    window._open_forms_dialog()
    dialog = window._forms_dialog
    # The password row is a choice of how it was handled, not a text field.
    assert [dialog.password_combo.itemData(i) for i in range(dialog.password_combo.count())] == [
        "", "not_needed", "given_by_client", "reset",
    ]
    assert dialog.password_combo.isEditable() is False
    line_edits = dialog.findChildren(job_forms.QLineEdit)
    assert all(edit.echoMode() == edit.EchoMode.Normal for edit in line_edits)
    # Only the accessories, the hand-over name and the date editor's own line edit.
    assert dialog.accessories_edit in line_edits and dialog.handed_to_edit in line_edits


def test_untouched_forms_log_nothing_and_cancel_logs_nothing(qtbot, tmp_path):
    window = _window(qtbot, tmp_path)
    window._open_forms_dialog()
    window._forms_dialog.accept()
    window._open_forms_dialog()
    _fill_forms(window._forms_dialog)
    window._forms_dialog.reject()
    assert _system_events(tmp_path, intake.INTAKE_EVENT) == []
    assert _system_events(tmp_path, intake.OUTTAKE_EVENT) == []


def test_clearing_a_saved_form_removes_it_from_the_report(qtbot, tmp_path):
    window = _window(qtbot, tmp_path)
    window._open_forms_dialog()
    window._forms_dialog.problem_edit.setPlainText("Nejde Wi-Fi")
    window._forms_dialog.accept()
    window._open_forms_dialog()
    window._forms_dialog.problem_edit.setPlainText("")
    window._forms_dialog.accept()
    assert len(_system_events(tmp_path, intake.INTAKE_EVENT)) == 2
    data = report.build_report_data(tmp_path, RUN_ID, window.modules, "en", {}, {})
    assert "intake" not in data


def test_work_timer_starts_and_stops_and_is_kept_for_the_run(qtbot, tmp_path):
    window = _window(qtbot, tmp_path)
    window._open_job_dialog()
    timer = window._work_timer_widget
    assert timer.toggle_button.text() == "Start"
    timer.toggle()
    assert timer.toggle_button.text() == "Stop"
    assert [e["decision"] for e in _system_events(tmp_path, intake.WORK_TIMER_EVENT)] == ["start"]
    window._job_dialog.reject()

    # Another window of the same run (PortableFix started again) finds it running.
    other = _window(qtbot, tmp_path)
    other._open_job_dialog()
    assert other._work_timer_widget.toggle_button.text() == "Stop"
    other._work_timer_widget.toggle()
    assert [e["decision"] for e in _system_events(tmp_path, intake.WORK_TIMER_EVENT)] == ["start", "stop"]
    assert other._work_timer_widget.toggle_button.text() == "Start"
    assert intake.timer_state(report.read_audit_entries(tmp_path, RUN_ID)).running_since is None


def test_work_timer_that_could_not_be_written_does_not_pretend_to_run(qtbot, tmp_path, monkeypatch):
    window = _window(qtbot, tmp_path)
    window._open_job_dialog()
    monkeypatch.setattr(window, "_log_system_event", lambda *a, **kw: None)
    window._work_timer_widget.toggle()
    assert window._work_timer_widget.toggle_button.text() == "Start"


def test_batch_end_logs_its_duration_and_the_report_gets_the_branding(qtbot, tmp_path, monkeypatch):
    logo = branding.encode_logo(PNG)
    window = _window(qtbot, tmp_path, Settings(language="en", branding_company="Servis s.r.o.", branding_logo=logo))
    captured = {}

    def fake_generate_report(*args, **kwargs):
        captured.update(kwargs)
        raise OSError("stop here")

    monkeypatch.setattr(report, "generate_report", fake_generate_report)
    monkeypatch.setattr(window, "_take_snapshot", lambda: {})
    window._batch_active = True
    window._batch_started_at = time.monotonic() - 125
    window._queue = []
    window._run_next()
    qtbot.waitUntil(lambda: window._report_runner is None, timeout=10000)
    [event] = _system_events(tmp_path, intake.BATCH_DURATION_EVENT)
    assert 125 <= json.loads(event["output"])["seconds"] <= 135
    assert window._batch_started_at is None
    assert captured["branding"]["company"] == "Servis s.r.o."
    assert captured["branding"]["logo"] == logo


def test_branding_dialog_saves_to_settings_and_validates_the_logo(qtbot, tmp_path):
    window = _window(qtbot, tmp_path)
    window._open_branding_dialog()
    dialog = window._branding_dialog
    assert dialog.logo_label.text() == "no logo"
    assert not dialog.remove_logo_button.isEnabled()
    fake = tmp_path / "logo.png"
    fake.write_bytes(b"GIF89a" + b"\x00" * 10)
    assert dialog.load_logo(fake) is False
    assert dialog.logo_error.text() == "The logo must be a PNG or JPEG image."
    big = tmp_path / "big.png"
    big.write_bytes(PNG + b"\x00" * branding.MAX_LOGO_BYTES)
    assert dialog.load_logo(big) is False
    assert dialog.logo_error.text() == "The logo is larger than 256 KB."
    assert dialog.logo == ""
    good = tmp_path / "good.jpg"
    good.write_bytes(PNG)
    assert dialog.load_logo(good) is True
    assert dialog.logo_error.isHidden()
    assert dialog.logo_label.text() == "logo set (1 KB)"
    dialog.company_edit.setText("  Servis s.r.o. ")
    dialog.company_id_edit.setText("12345678")
    dialog.contact_edit.setPlainText("0900 123 456")
    dialog.accept()

    saved = load_settings(tmp_path)
    assert saved.branding_company == "Servis s.r.o."
    assert saved.branding_company_id == "12345678"
    assert saved.branding_contact == "0900 123 456"
    assert branding.decode_logo(saved.branding_logo)[0] == "image/png"

    # Remove and cancel: nothing changes; remove and OK: the logo is gone.
    window._open_branding_dialog()
    window._branding_dialog.remove_logo()
    window._branding_dialog.reject()
    assert load_settings(tmp_path).branding_logo
    window._open_branding_dialog()
    window._branding_dialog.remove_logo()
    window._branding_dialog.accept()
    assert load_settings(tmp_path).branding_logo == ""
    assert load_settings(tmp_path).branding_company == "Servis s.r.o."


def test_forms_dialog_is_slovak(qtbot, tmp_path):
    window = _window(qtbot, tmp_path, Settings(language="sk"))
    window._open_forms_dialog()
    dialog = window._forms_dialog
    assert dialog.windowTitle() == "Prevzatie a odovzdanie PC"
    assert dialog.condition_checkboxes["liquid_damage"].text() == "poškodenie tekutinou"
    assert dialog.backup_combo.itemText(2) == "Klient zálohu odmieta a riziko straty dát berie na seba"
    assert dialog.check_combos["display"].itemText(1) == "V poriadku"
