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
    ).read_text(encoding="utf-8-sig")
    # Every non-empty line must still be a comment - no executable line
    # smuggled in through a label containing a line break.
    assert all(line.startswith("#") for line in content.splitlines() if line.strip())


def test_undo_script_has_exactly_one_utf8_bom_even_after_rewrites(tmp_path):
    # Windows PowerShell 5.1 reads a BOM-less .ps1 as ANSI - the Slovak
    # labels in the NOT-reversible comments came out garbled.
    label = "[DESTRUCTIVE] Vyčistenie súčastí (x)"
    create_undo_script(tmp_path, "run7", irreversible=[label])
    path = create_undo_script(tmp_path, "run7", steps=["Write-Output 'ľšč'"], irreversible=[label])
    raw = path.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf# PortableFix undo script")
    assert raw.count(b"\xef\xbb\xbf") == 1
    content = raw.decode("utf-8-sig")
    assert label in content and "Write-Output 'ľšč'" in content


def test_hive_backup_is_named_in_undo_ps1_as_a_manual_hint_only(tmp_path):
    # research G24: never auto-restored - every line about it is a comment.
    folder = tmp_path / "Backups" / "run5" / "hives-20260924-100000"
    content = create_undo_script(tmp_path, "run5", hive_backups=[folder]).read_text(encoding="utf-8-sig")
    assert str(folder) in content
    assert "manual restore only" in content
    hint = [line for line in content.splitlines() if "hive" in line.lower() or ".hiv" in line]
    assert hint and all(line.startswith("#") for line in hint)
    assert "reg restore" not in content.lower()
    assert "Copy-Item" not in content


def test_hive_backup_path_with_a_line_break_cannot_escape_the_comment(tmp_path):
    from pathlib import Path

    content = create_undo_script(
        tmp_path, "run6", hive_backups=[Path("C:/x\nRemove-Item C:/ -Recurse")],
    ).read_text(encoding="utf-8-sig")
    assert all(line.startswith("#") or not line.strip() for line in content.splitlines())


def test_undo_script_with_hive_hint_parses_as_powershell(tmp_path):
    import os
    import shutil
    import subprocess

    import pytest

    exe = os.environ.get("PORTABLEFIX_TEST_PWSH") or shutil.which("powershell") or shutil.which("pwsh")
    if not exe:
        pytest.skip("no PowerShell available")
    path = create_undo_script(
        tmp_path, "run7", steps=["Write-Output 'undo-step'"],
        irreversible=["[DESTRUCTIVE] X"], hive_backups=[tmp_path / "Backups" / "run7" / "hives-1"],
    )
    # Running it must do only the real undo step - the hive hint is inert.
    result = subprocess.run([exe, "-NoProfile", "-File", str(path)], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "undo-step"
