import hashlib
from pathlib import Path

from portablefix.integrity import check_integrity, compute_sha256, parse_sha256sums


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


def test_check_integrity_unreadable_manifest_degrades_to_empty_instead_of_raising(tmp_path):
    (tmp_path / "Data").mkdir()
    (tmp_path / "Data" / "SHA256SUMS").write_bytes(b"\xff\xfe\x00\xff not valid utf-8")
    assert check_integrity(tmp_path) == []
