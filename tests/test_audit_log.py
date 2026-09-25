import json
import socket

from portablefix.audit_log import append_entry, audit_log_path, make_entry


def test_make_entry_populates_core_fields():
    entry = make_entry("m01_diagnostics", "os_info", "Get-CimInstance ...", 0, "some output", False, "run123")
    assert entry.module_id == "m01_diagnostics"
    assert entry.action_id == "os_info"
    assert entry.exit_code == 0
    assert entry.dry_run is False
    assert entry.timestamp
    assert entry.run_id == "run123"
    assert entry.hostname == socket.gethostname()


def test_make_entry_defaults_risk_warned_elevated():
    entry = make_entry("m01_diagnostics", "os_info", "cmd", 0, "output", False, "run123")
    assert entry.risk == ""
    assert entry.warned is False
    assert entry.elevated is False


def test_make_entry_accepts_risk_warned_elevated():
    entry = make_entry(
        "m08_security", "hard_reset", "cmd", 0, "output", False, "run123",
        risk="DESTRUCTIVE", warned=True, elevated=True,
    )
    assert entry.risk == "DESTRUCTIVE"
    assert entry.warned is True
    assert entry.elevated is True


def test_make_entry_records_warning_text_subject_and_decision(tmp_path):
    # research-reporting.md F2/F3: the log must prove what the technician
    # was warned about and what they answered.
    entry = make_entry(
        "_system", "risk_declined", "", None, "declined", False, "run123",
        risk="DESTRUCTIVE", warned=True, warning_text="WARNING: irreversible",
        subject="m08_security/hard_reset", decision="declined",
    )
    append_entry(tmp_path, "run123", entry)
    parsed = json.loads(audit_log_path(tmp_path, "run123").read_text(encoding="utf-8"))
    assert parsed["warning_text"] == "WARNING: irreversible"
    assert parsed["subject"] == "m08_security/hard_reset"
    assert parsed["decision"] == "declined"


def test_make_entry_new_fields_default_empty_for_backward_compat():
    entry = make_entry("m01_diagnostics", "os_info", "cmd", 0, "output", False, "run123")
    assert (entry.warning_text, entry.subject, entry.decision) == ("", "", "")


def test_make_entry_records_restore_point_sequence(tmp_path):
    # research-reporting.md F1: *which* restore point the run created.
    entry = make_entry(
        "_system", "restore_point", "Checkpoint-Computer", 0, "created", False, "run123",
        restore_point_sequence=123, restore_point_created="20260924101530.123456-000",
    )
    append_entry(tmp_path, "run123", entry)
    parsed = json.loads(audit_log_path(tmp_path, "run123").read_text(encoding="utf-8"))
    assert parsed["restore_point_sequence"] == 123
    assert parsed["restore_point_created"] == "20260924101530.123456-000"


def test_make_entry_restore_point_sequence_defaults_to_not_recorded():
    entry = make_entry("_system", "restore_point", "cmd", 0, "output", False, "run123")
    assert entry.restore_point_sequence is None
    assert entry.restore_point_created == ""


def test_append_entry_writes_jsonl_line(tmp_path):
    entry = make_entry("m01_diagnostics", "os_info", "cmd", 0, "output", False, "run123")
    append_entry(tmp_path, "run123", entry)
    path = audit_log_path(tmp_path, "run123")
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    parsed = json.loads(lines[0])
    assert parsed["action_id"] == "os_info"
    assert parsed["run_id"] == "run123"


def test_append_entry_accumulates_multiple_lines(tmp_path):
    entry1 = make_entry("m01_diagnostics", "os_info", "cmd1", 0, "out1", False, "run123")
    entry2 = make_entry("m01_diagnostics", "cpu_info", "cmd2", 0, "out2", False, "run123")
    append_entry(tmp_path, "run123", entry1)
    append_entry(tmp_path, "run123", entry2)
    lines = audit_log_path(tmp_path, "run123").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2


def test_make_entry_records_every_guarded_subject():
    entry = make_entry("_system", "restore_point", "", 0, "ok", False, "run_x",
                       subject="_uninstaller/A", subjects=["_uninstaller/A", "_uninstaller/B"])
    assert entry.subjects == ["_uninstaller/A", "_uninstaller/B"]
    # Default: no list (the point guarded just `subject`), never a shared one.
    first, second = (make_entry("_system", "x", "", 0, "", False, "r") for _ in range(2))
    assert first.subjects == [] and first.subjects is not second.subjects
