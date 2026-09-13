import sys
import zipfile

from portablefix.diagnostics import crash_log_path, export_diagnostics_zip, install_excepthook


def test_export_diagnostics_zip_bundles_logs_and_reports(tmp_path):
    (tmp_path / "Logs").mkdir()
    (tmp_path / "Logs" / "run1.jsonl").write_text('{"a": 1}\n', encoding="utf-8")
    (tmp_path / "Reports").mkdir()
    (tmp_path / "Reports" / "run1.html").write_text("<html></html>", encoding="utf-8")

    dest = tmp_path / "out.zip"
    export_diagnostics_zip(tmp_path, dest)

    with zipfile.ZipFile(dest) as zf:
        names = set(zf.namelist())
    assert names == {"Logs/run1.jsonl", "Reports/run1.html"}


def test_export_diagnostics_zip_skips_missing_folders(tmp_path):
    dest = tmp_path / "out.zip"
    export_diagnostics_zip(tmp_path, dest)

    with zipfile.ZipFile(dest) as zf:
        assert zf.namelist() == []


def test_install_excepthook_writes_crash_log_and_calls_previous_hook(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(sys, "excepthook", lambda *a: calls.append(a))

    install_excepthook(tmp_path)
    try:
        raise ValueError("boom")
    except ValueError:
        sys.excepthook(*sys.exc_info())

    assert calls, "previous excepthook was not called"
    log_text = crash_log_path(tmp_path).read_text(encoding="utf-8")
    assert "ValueError: boom" in log_text
