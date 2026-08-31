from pathlib import Path

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
