"""GUI side of "Redact for the client" (research G20): the toggle in the job
dialog, its persistence and where the setting is handed on. A module of its
own so it runs without a real batch."""

import socket
import zipfile

from PySide6.QtWidgets import QCheckBox, QFileDialog

from portablefix import handoff, report
from portablefix.gui.main_window import MainWindow
from portablefix.settings import Settings, load_settings

RUN_ID = "20260925T100000-abcd"


def _window(qtbot, tmp_path, settings=None):
    module_dir = tmp_path / "Modules" / "m01_diagnostics"
    module_dir.mkdir(parents=True)
    (module_dir / "actions.yaml").write_text(
        "module_id: m01_diagnostics\n"
        "actions:\n"
        "  - {id: one, label_sk: Jedna, label_en: One, risk: SAFE, command: \"Write-Output 1\"}\n",
        encoding="utf-8",
    )
    window = MainWindow(
        assets_dir=tmp_path, state_dir=tmp_path, settings=settings or Settings(language="en"),
        is_admin=True, run_id=RUN_ID,
    )
    qtbot.addWidget(window)
    return window


def _redact_box(window):
    window._open_job_dialog()
    [box] = window._job_dialog.findChildren(QCheckBox, "jobRedact")
    return box


def test_redact_toggle_is_off_by_default_and_persists_on_accept(qtbot, tmp_path):
    window = _window(qtbot, tmp_path)
    box = _redact_box(window)
    assert box.text() == "Redact for the client"
    assert not box.isChecked()
    assert "audit log is not changed" in box.toolTip()
    box.setChecked(True)
    window._job_dialog.accept()
    assert window.settings.redact_for_client is True
    assert load_settings(tmp_path).redact_for_client is True
    # Shown as it is saved the next time the dialog opens.
    assert _redact_box(window).isChecked()


def test_redact_toggle_is_not_saved_on_cancel(qtbot, tmp_path):
    window = _window(qtbot, tmp_path)
    box = _redact_box(window)
    box.setChecked(True)
    window._job_dialog.reject()
    assert window.settings.redact_for_client is False
    assert load_settings(tmp_path).redact_for_client is False


def test_redact_toggle_is_translated(qtbot, tmp_path):
    window = _window(qtbot, tmp_path, Settings(language="sk"))
    assert _redact_box(window).text() == "Redigovať pre klienta"


def test_report_runner_gets_the_setting(qtbot, tmp_path, monkeypatch):
    window = _window(qtbot, tmp_path, Settings(language="en", redact_for_client=True))
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
    assert captured["redact"] is True


def test_client_package_is_redacted_when_the_setting_is_on(qtbot, tmp_path, monkeypatch):
    window = _window(qtbot, tmp_path, Settings(language="en", redact_for_client=True))
    host = socket.gethostname()
    reports = tmp_path / "Reports"
    reports.mkdir()
    (reports / f"{host}_{RUN_ID}.json").write_text('{"run_id": "x", "output": "IP 192.168.1.23"}', encoding="utf-8")
    (reports / f"{host}_{RUN_ID}.html").write_text("<html>192.168.1.23</html>", encoding="utf-8")
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda parent, title, default, filters: (default, filters))
    monkeypatch.setattr(window, "_show_handoff_saved", lambda saved: None)

    saved = window._save_handoff_package(RUN_ID)

    with zipfile.ZipFile(saved) as zf:
        assert "192.168.1.23" not in zf.read("report.html").decode("utf-8")
        assert "REDACTED FOR THE CLIENT" in zf.read(handoff.ARC_README).decode("utf-8-sig")
