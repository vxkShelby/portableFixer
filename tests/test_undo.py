from portablefix.undo import create_undo_script


def test_create_undo_script_with_no_steps_writes_header_only(tmp_path):
    path = create_undo_script(tmp_path, "run1")
    assert path == tmp_path / "Backups" / "run1" / "undo.ps1"
    content = path.read_text(encoding="utf-8")
    assert "run1" in content
    assert "No reversible changes" in content


def test_create_undo_script_with_steps_includes_them(tmp_path):
    path = create_undo_script(tmp_path, "run2", steps=["Set-ItemProperty -Path X -Name Y -Value Z"])
    content = path.read_text(encoding="utf-8")
    assert "Set-ItemProperty -Path X -Name Y -Value Z" in content


def test_create_undo_script_creates_backups_dir(tmp_path):
    create_undo_script(tmp_path, "run3")
    assert (tmp_path / "Backups" / "run3").is_dir()


def test_undo_script_points_back_to_report_and_audit_log(tmp_path):
    # research-reporting.md F9: an undo.ps1 found on its own must say where
    # the full record of the run lives.
    import socket

    content = create_undo_script(tmp_path, "run4").read_text(encoding="utf-8")
    report = tmp_path / "Reports" / f"{socket.gethostname()}_run4.html"
    assert f"# full report: {report}" in content
    assert str(tmp_path / "Logs" / "run4.jsonl") in content
    assert f"# computer: {socket.gethostname()}" in content


def test_undo_script_lists_irreversible_actions_as_comments(tmp_path):
    content = create_undo_script(
        tmp_path, "run5", steps=[], irreversible=["[DESTRUCTIVE] Reset component store (component_store_resetbase)"],
    ).read_text(encoding="utf-8")
    assert "# NOT reversible" in content
    assert "#   - [DESTRUCTIVE] Reset component store (component_store_resetbase)" in content
    # Still honest that nothing here can be rolled back.
    assert "No reversible changes" in content


def test_undo_script_irreversible_label_cannot_inject_code(tmp_path):
    content = create_undo_script(
        tmp_path, "run6", irreversible=["evil\nRemove-Item C:\\ -Recurse"],
    ).read_text(encoding="utf-8")
    # Every non-empty line must still be a comment - no executable line
    # smuggled in through a label containing a line break.
    assert all(line.startswith("#") for line in content.splitlines() if line.strip())
