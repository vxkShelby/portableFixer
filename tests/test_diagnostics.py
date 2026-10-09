import json
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
    assert "privately" in query["body"][0]


def _write_run(base, run_id, output):
    (base / "Logs").mkdir(exist_ok=True)
    entry = {"timestamp": "2026-01-01T00:00:00+00:00", "module_id": "m01", "action_id": "a", "command": "x",
             "exit_code": 0, "output": output, "dry_run": False, "hostname": "PC", "run_id": run_id}
    (base / "Logs" / f"{run_id}.jsonl").write_text(json.dumps(entry) + "\n", encoding="utf-8")
    (base / "Reports").mkdir(exist_ok=True)
    data = {"run_id": run_id, "hostname": "PC", "language": "en", "generated_at": "2026-01-01T00:00:00+00:00",
            "os": "Windows", "snapshot_before": {}, "snapshot_after": {}, "actions": [dict(entry, label="A", risk="SAFE")],
            "requires_restart": [], "module_summary": [], "job": {}, "events": [], "restore_points": []}
    (base / "Reports" / f"PC_{run_id}.json").write_text(json.dumps(data), encoding="utf-8")
    (base / "Reports" / f"PC_{run_id}.html").write_text(f"<html>{output}</html>", encoding="utf-8")


def test_export_diagnostics_zip_holds_only_the_current_run_redacted(tmp_path, monkeypatch):
    monkeypatch.setattr("portablefix.redaction.local_profile_names", lambda: [])
    _write_run(tmp_path, "run1", "C:\\Users\\jan\\x SSID : Home5G")
    _write_run(tmp_path, "run2", "old C:\\Users\\jan\\x SSID : Home5G")
    (tmp_path / "Logs" / "crash.log").write_text("Traceback C:\\Users\\jan\\app.py", encoding="utf-8")

    dest = tmp_path / "out.zip"
    export_diagnostics_zip(tmp_path, dest, run_id="run1")

    with zipfile.ZipFile(dest) as zf:
        names = set(zf.namelist())
        texts = {n: zf.read(n).decode("utf-8") for n in names}
    assert names == {"README_DIAGNOSTICS.txt", "Logs/crash.log", "Logs/run1.jsonl",
                     "Reports/PC_run1.json", "Reports/PC_run1.html"}
    for name, text in texts.items():
        assert "jan" not in text, name
        assert "Home5G" not in text, name
    assert "Logs/run1.jsonl" in texts["README_DIAGNOSTICS.txt"]
    assert "<user>" in texts["Logs/crash.log"]
    assert "&lt;ssid&gt;" in texts["Reports/PC_run1.html"]


def test_export_diagnostics_zip_defaults_to_the_newest_run(tmp_path, monkeypatch):
    monkeypatch.setattr("portablefix.redaction.local_profile_names", lambda: [])
    _write_run(tmp_path, "run1", "a")
    _write_run(tmp_path, "run2", "b")
    import os
    os.utime(tmp_path / "Logs" / "run1.jsonl", (1, 1))

    dest = tmp_path / "out.zip"
    export_diagnostics_zip(tmp_path, dest)

    with zipfile.ZipFile(dest) as zf:
        assert "Logs/run2.jsonl" in zf.namelist()
        assert "Logs/run1.jsonl" not in zf.namelist()


def test_export_diagnostics_zip_skips_missing_folders(tmp_path):
    dest = tmp_path / "out.zip"
    export_diagnostics_zip(tmp_path, dest)

    with zipfile.ZipFile(dest) as zf:
        assert zf.namelist() == ["README_DIAGNOSTICS.txt"]


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
