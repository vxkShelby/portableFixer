import sys
import zipfile
from urllib.parse import parse_qs, urlparse

from portablefix.diagnostics import (
    build_bug_report_url,
    crash_log_path,
    export_diagnostics_zip,
    install_excepthook,
)


def test_build_bug_report_url_points_at_github_issues_with_title_and_body():
    url = build_bug_report_url("1.10.0")

    parsed = urlparse(url)
    assert parsed.netloc == "github.com"
    assert parsed.path == "/vxkShelby/portableFixer/issues/new"

    query = parse_qs(parsed.query)
    assert "1.10.0" in query["title"][0]
    assert "diagnostics" in query["body"][0].lower()


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


def test_write_crash_log_records_a_caught_exception(tmp_path):
    # main() catches startup exceptions itself (they never reach
    # sys.excepthook) - this is what keeps a trace of them on disk.
    from portablefix.diagnostics import write_crash_log

    try:
        raise RuntimeError("startup exploded")
    except RuntimeError as exc:
        write_crash_log(tmp_path, exc)

    log_text = crash_log_path(tmp_path).read_text(encoding="utf-8")
    assert "RuntimeError: startup exploded" in log_text
    assert "Traceback" in log_text
