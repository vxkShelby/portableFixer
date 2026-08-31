import hashlib
import json

from portablefix.audit_log import append_entry, audit_log_path, make_entry


def test_make_entry_hashes_output():
    entry = make_entry("m01_diagnostics", "os_info", "Get-CimInstance ...", 0, "some output", False)
    assert entry.output_hash == hashlib.sha256(b"some output").hexdigest()
    assert entry.module_id == "m01_diagnostics"
    assert entry.action_id == "os_info"
    assert entry.exit_code == 0
    assert entry.dry_run is False
    assert entry.timestamp


def test_append_entry_writes_jsonl_line(tmp_path):
    entry = make_entry("m01_diagnostics", "os_info", "cmd", 0, "output", False)
    append_entry(tmp_path, "run123", entry)
    path = audit_log_path(tmp_path, "run123")
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    parsed = json.loads(lines[0])
    assert parsed["action_id"] == "os_info"


def test_append_entry_accumulates_multiple_lines(tmp_path):
    entry1 = make_entry("m01_diagnostics", "os_info", "cmd1", 0, "out1", False)
    entry2 = make_entry("m01_diagnostics", "cpu_info", "cmd2", 0, "out2", False)
    append_entry(tmp_path, "run123", entry1)
    append_entry(tmp_path, "run123", entry2)
    lines = audit_log_path(tmp_path, "run123").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
