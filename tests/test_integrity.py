import hashlib
import os
import subprocess

import pytest

from portablefix.integrity import IntegrityCheckRunner, check_integrity, compute_sha256, parse_sha256sums


def test_compute_sha256_matches_hashlib(tmp_path):
    file_path = tmp_path / "sample.txt"
    file_path.write_bytes(b"hello world")
    assert compute_sha256(file_path) == hashlib.sha256(b"hello world").hexdigest()


def test_parse_sha256sums(tmp_path):
    sums_path = tmp_path / "SHA256SUMS"
    sums_path.write_text(
        "aaaa  App/PortableFix.exe\nbbbb  Modules/m01_diagnostics/actions.yaml\n",
        encoding="utf-8",
    )
    result = parse_sha256sums(sums_path)
    assert result == {
        "App/PortableFix.exe": "aaaa",
        "Modules/m01_diagnostics/actions.yaml": "bbbb",
    }


def test_check_integrity_no_sums_file_returns_empty(tmp_path):
    assert check_integrity(tmp_path) == []


def test_check_integrity_all_match(tmp_path):
    (tmp_path / "App").mkdir()
    target = tmp_path / "App" / "file.txt"
    target.write_bytes(b"content")
    digest = compute_sha256(target)
    (tmp_path / "Data").mkdir()
    (tmp_path / "Data" / "SHA256SUMS").write_text(f"{digest}  App/file.txt\n", encoding="utf-8")
    assert check_integrity(tmp_path) == []


def test_check_integrity_detects_tampered_file(tmp_path):
    (tmp_path / "App").mkdir()
    target = tmp_path / "App" / "file.txt"
    target.write_bytes(b"content")
    digest = compute_sha256(target)
    target.write_bytes(b"tampered")
    (tmp_path / "Data").mkdir()
    (tmp_path / "Data" / "SHA256SUMS").write_text(f"{digest}  App/file.txt\n", encoding="utf-8")
    assert check_integrity(tmp_path) == ["App/file.txt"]


def test_check_integrity_detects_missing_file(tmp_path):
    (tmp_path / "Data").mkdir()
    (tmp_path / "Data" / "SHA256SUMS").write_text("aaaa  App/missing.txt\n", encoding="utf-8")
    assert check_integrity(tmp_path) == ["App/missing.txt"]


def test_check_integrity_detects_file_added_outside_the_manifest(tmp_path):
    (tmp_path / "App").mkdir()
    known = tmp_path / "App" / "known.txt"
    known.write_bytes(b"content")
    digest = compute_sha256(known)
    (tmp_path / "Modules").mkdir()
    (tmp_path / "Modules" / "planted.dll").write_bytes(b"not in the manifest")
    (tmp_path / "Data").mkdir()
    (tmp_path / "Data" / "SHA256SUMS").write_text(f"{digest}  App/known.txt\n", encoding="utf-8")

    assert check_integrity(tmp_path) == ["Modules/planted.dll"]


def test_check_integrity_skips_symlinks_instead_of_following_them(tmp_path):
    (tmp_path / "App").mkdir()
    real_dir = tmp_path / "real_target"
    real_dir.mkdir()
    (real_dir / "secret.txt").write_bytes(b"outside base_dir")
    link_path = tmp_path / "App" / "linked"
    try:
        os.symlink(real_dir, link_path, target_is_directory=True)
    except OSError:
        pytest.skip("creating symlinks needs Developer Mode or admin on this machine")

    (tmp_path / "Data").mkdir()
    (tmp_path / "Data" / "SHA256SUMS").write_text("", encoding="utf-8")

    # A symlinked directory under App/ must not be walked into (it isn't in
    # the manifest either way, but if rglob followed it and it looped back
    # to an ancestor, this call would hang instead of returning).
    assert check_integrity(tmp_path) == []


def test_check_integrity_skips_junctions_instead_of_following_them(tmp_path):
    # An NTFS junction (mklink /J) is a reparse point but pathlib's
    # is_symlink() returns False for it - only is_junction() (3.12+) catches
    # it. No admin/Developer Mode needed to create one, unlike a real symlink.
    (tmp_path / "App").mkdir()
    real_dir = tmp_path / "real_target"
    real_dir.mkdir()
    (real_dir / "secret.txt").write_bytes(b"outside base_dir")
    link_path = tmp_path / "App" / "linked_junction"
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link_path), str(real_dir)],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        pytest.skip("mklink /J not available in this environment")

    (tmp_path / "Data").mkdir()
    (tmp_path / "Data" / "SHA256SUMS").write_text("", encoding="utf-8")

    assert check_integrity(tmp_path) == []


def test_check_integrity_skips_a_junction_that_cycles_back_to_an_ancestor(tmp_path):
    # This is the scenario that actually hangs a plain rglob() walk: a
    # junction inside App/ pointing back at App/ itself recurses forever
    # since rglob has already descended into it before any per-entry check
    # runs. The fix must refuse to descend in the first place.
    (tmp_path / "App").mkdir()
    (tmp_path / "App" / "sub").mkdir()
    link_path = tmp_path / "App" / "sub" / "cycle_back"
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link_path), str(tmp_path / "App")],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        pytest.skip("mklink /J not available in this environment")

    (tmp_path / "Data").mkdir()
    (tmp_path / "Data" / "SHA256SUMS").write_text("", encoding="utf-8")

    assert check_integrity(tmp_path) == []


def test_check_integrity_unreadable_manifest_degrades_to_empty_instead_of_raising(tmp_path):
    (tmp_path / "Data").mkdir()
    (tmp_path / "Data" / "SHA256SUMS").write_bytes(b"\xff\xfe\x00\xff not valid utf-8")
    assert check_integrity(tmp_path) == []


def test_integrity_check_runner_emits_mismatches_off_the_gui_thread(qtbot, tmp_path):
    (tmp_path / "Data").mkdir()
    (tmp_path / "Data" / "SHA256SUMS").write_text("aaaa  App/missing.txt\n", encoding="utf-8")

    runner = IntegrityCheckRunner(tmp_path)
    with qtbot.waitSignal(runner.check_finished, timeout=5000) as blocker:
        runner.start()
    assert blocker.args == [["App/missing.txt"]]
