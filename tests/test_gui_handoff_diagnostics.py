"""GUI side of the Windows diagnostics in the client package (G19).

A module of its own (not test_gui_main_window.py) so these run without a
real batch: the reports are faked, the summary dialog is built directly.
Only the report commands are faked - any other Popen (none is expected
here) still goes to the real subprocess.
"""

import json
import os
import socket
import subprocess
import time
import zipfile

import pytest

from PySide6.QtWidgets import QCheckBox, QDialog, QFileDialog, QMessageBox, QPushButton

from portablefix import handoff
from portablefix.gui.main_window import MainWindow
from portablefix.settings import Settings

RUN_ID = "20260924T100000-abcd"
REPORT_COMMANDS = {os.path.basename(spec.argv[0]).lower() for spec in handoff.DIAGNOSTIC_REPORTS}
_REAL_POPEN = subprocess.Popen


class _FakeReport:
    """A report command that writes a small file and exits 0 - or, while
    `hang` is set, never exits until it is killed."""

    calls: list = []
    procs: list = []
    hang = False

    def __new__(cls, argv, *args, **kwargs):
        if os.path.basename(str(argv[0])).lower().removesuffix(".exe") not in REPORT_COMMANDS:
            return _REAL_POPEN(argv, *args, **kwargs)
        return object.__new__(cls)

    def __init__(self, argv, stdin=None, stdout=None, stderr=None, creationflags=0):
        type(self).calls.append(list(argv))
        type(self).procs.append(self)
        self.killed = False
        if hasattr(stdout, "write"):
            stdout.write(b"data")
        else:
            out_path = next(a for a in argv[1:] if a.count(os.sep) > 1)
            with open(out_path, "wb") as fh:
                fh.write(b"data")

    def wait(self, timeout=None):
        if type(self).hang and not self.killed:
            # Like a real wait: sleep the poll interval, do not spin.
            time.sleep(min(timeout or 0, 0.05))
            raise subprocess.TimeoutExpired("fake", timeout)
        return 0

    def kill(self):
        self.killed = True


@pytest.fixture
def fake_reports(monkeypatch):
    _FakeReport.calls = []
    _FakeReport.procs = []
    _FakeReport.hang = False
    monkeypatch.setattr(handoff.subprocess, "Popen", _FakeReport)
    monkeypatch.setattr(handoff, "_has_battery", lambda: True)
    monkeypatch.setattr(handoff, "_find_winget", lambda: None)
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda parent, title, default, filters: (default, filters))
    return _FakeReport


def _window(qtbot, tmp_path, dry_run=False):
    module_dir = tmp_path / "Modules" / "m01_diagnostics"
    module_dir.mkdir(parents=True)
    (module_dir / "actions.yaml").write_text(
        "module_id: m01_diagnostics\n"
        "actions:\n"
        "  - {id: one, label_sk: Jedna, label_en: One, risk: SAFE, command: \"Write-Output 1\"}\n",
        encoding="utf-8",
    )
    reports = tmp_path / "Reports"
    reports.mkdir()
    host = socket.gethostname()
    data = {"run_id": RUN_ID, "generated_at": "2026-09-24T10:00:00+00:00", "actions": [{"exit_code": 0, "dry_run": dry_run}]}
    (reports / f"{host}_{RUN_ID}.json").write_text(json.dumps(data), encoding="utf-8")
    (reports / f"{host}_{RUN_ID}.html").write_text("<html></html>", encoding="utf-8")
    window = MainWindow(
        assets_dir=tmp_path, state_dir=tmp_path, settings=Settings(language="en", dry_run=dry_run),
        is_admin=True, run_id=RUN_ID,
    )
    qtbot.addWidget(window)
    return window


def _package(tmp_path):
    return tmp_path / "Reports" / f"PortableFix_{socket.gethostname()}_{RUN_ID}.zip"


def _history_handoff_button(window):
    rows = [window._history_layout.itemAt(i).widget() for i in range(window._history_layout.count())]
    assert len(rows) == 1
    [button] = [b for b in rows[0].findChildren(QPushButton) if b.text() == "Save client package"]
    return button


def _wait_idle(qtbot, window):
    qtbot.waitUntil(lambda: window._handoff_runner is None, timeout=10000)


def test_diagnostics_checkboxes_are_off_by_default_and_off_runs_no_report(qtbot, tmp_path, fake_reports):
    window = _window(qtbot, tmp_path)
    assert window._history_handoff_diag_checkbox.objectName() == "handoffDiagnostics"
    assert not window._history_handoff_diag_checkbox.isChecked()
    window._show_batch_summary(tmp_path / "report.html")
    [summary_box] = window._summary_dialog.findChildren(QCheckBox, "handoffDiagnostics")
    assert not summary_box.isChecked()

    _history_handoff_button(window).click()

    # Unticked: built at once on the GUI thread, no report ran.
    assert window._handoff_runner is None
    assert fake_reports.calls == []
    with zipfile.ZipFile(_package(tmp_path)) as zf:
        assert not any(n.startswith("diagnostics/") for n in zf.namelist())


def test_history_checkbox_collects_diagnostics_in_worker_with_dry_run_note(qtbot, tmp_path, fake_reports):
    window = _window(qtbot, tmp_path, dry_run=True)
    window._history_handoff_diag_checkbox.setChecked(True)

    _history_handoff_button(window).click()

    assert window._handoff_runner is not None
    _wait_idle(qtbot, window)
    with zipfile.ZipFile(_package(tmp_path)) as zf:
        names = set(zf.namelist())
    assert {"diagnostics/README.txt", "diagnostics/systeminfo.csv", "diagnostics/msinfo32.nfo"} <= names
    console = window.console.toPlainText()
    assert window._t("handoff_diag_dry_run_note") in console
    assert window._t("handoff_diag_started") in console
    assert window._t("handoff_saved").format(path=_package(tmp_path)) in console
    assert not window._handoff_folder_button.isHidden()


def test_summary_dialog_checkbox_collects_diagnostics(qtbot, tmp_path, fake_reports):
    window = _window(qtbot, tmp_path)
    window._show_batch_summary(tmp_path / "report.html")
    dialog = window._summary_dialog
    [checkbox] = dialog.findChildren(QCheckBox, "handoffDiagnostics")
    checkbox.setChecked(True)
    [button] = [b for b in dialog.findChildren(QPushButton) if b.text() == "Save client package"]

    button.click()

    assert window._handoff_runner is not None
    _wait_idle(qtbot, window)
    with zipfile.ZipFile(_package(tmp_path)) as zf:
        assert "diagnostics/README.txt" in zf.namelist()
    # Not in DRY-RUN: no note about it.
    assert window._t("handoff_diag_dry_run_note") not in window.console.toPlainText()


def test_second_diagnostics_package_is_refused_while_one_runs(qtbot, tmp_path, fake_reports, monkeypatch):
    window = _window(qtbot, tmp_path)
    fake_reports.hang = True
    infos = []
    monkeypatch.setattr(QMessageBox, "information", lambda parent, title, text: infos.append(text))
    asked = []
    monkeypatch.setattr(
        QFileDialog, "getSaveFileName",
        lambda parent, title, default, filters: (asked.append(default), (default, filters))[1],
    )
    window._history_handoff_diag_checkbox.setChecked(True)
    button = _history_handoff_button(window)

    button.click()
    first = window._handoff_runner
    qtbot.waitUntil(lambda: bool(fake_reports.procs), timeout=5000)
    button.click()

    # Told to wait, not even asked where to save; the first one untouched.
    assert infos == [window._t("handoff_diag_busy")]
    assert len(asked) == 1
    assert window._handoff_runner is first and first.isRunning()
    fake_reports.hang = False
    _wait_idle(qtbot, window)
    assert _package(tmp_path).is_file()


def test_handoff_result_with_deleted_summary_dialog_uses_main_window(qtbot, tmp_path, monkeypatch):
    from shiboken6 import Shiboken

    window = _window(qtbot, tmp_path)
    dialog = QDialog(window)
    Shiboken.delete(dialog)
    warnings = []
    monkeypatch.setattr(
        QMessageBox, "warning", lambda parent, title, text: warnings.append((parent, text))
    )

    window._on_handoff_result(None, "failed", "USB gone", dialog)

    # The dialog closed during the minutes the reports took: the message
    # goes to the main window instead of crashing on a deleted parent.
    assert len(warnings) == 1
    parent, text = warnings[0]
    assert parent is window and "USB gone" in text
    assert window._t("handoff_failed") in window.console.toPlainText()


def test_cancelled_handoff_result_shows_nothing(qtbot, tmp_path):
    window = _window(qtbot, tmp_path)
    # conftest fails the test on any message box.
    window._on_handoff_result(None, "cancelled", "", None)
    assert window._handoff_runner is None


def test_closing_window_stops_diagnostics_and_kills_report(qtbot, tmp_path, fake_reports):
    window = _window(qtbot, tmp_path)
    fake_reports.hang = True
    window._history_handoff_diag_checkbox.setChecked(True)
    _history_handoff_button(window).click()
    runner = window._handoff_runner
    qtbot.waitUntil(lambda: bool(fake_reports.procs), timeout=5000)
    started = time.monotonic()
    window.close()
    elapsed = time.monotonic() - started

    # Not the rest of msinfo32's 5-minute timeout: interrupted within a poll.
    assert elapsed < 5
    try:
        assert not runner.isRunning()
    except RuntimeError:
        pass  # already deleted by finished->deleteLater: it has ended
    assert fake_reports.procs[-1].killed
    # A stopped collection writes no zip at all.
    assert not _package(tmp_path).exists()
