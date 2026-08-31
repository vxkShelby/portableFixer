from pathlib import Path

from portablefix.integrity import parse_sha256sums
from scripts.generate_sha256sums import build_sums_content, collect_files


def test_collect_files_finds_app_and_modules(tmp_path):
    (tmp_path / "App").mkdir()
    (tmp_path / "App" / "PortableFix.exe").write_bytes(b"x")
    (tmp_path / "Modules" / "m01_diagnostics").mkdir(parents=True)
    (tmp_path / "Modules" / "m01_diagnostics" / "actions.yaml").write_text("x", encoding="utf-8")
    (tmp_path / "Logs").mkdir()
    (tmp_path / "Logs" / "run.jsonl").write_text("x", encoding="utf-8")

    files = collect_files(tmp_path)
    rel_paths = {p.relative_to(tmp_path).as_posix() for p in files}
    assert rel_paths == {"App/PortableFix.exe", "Modules/m01_diagnostics/actions.yaml"}


def test_build_sums_content_round_trips_with_parser(tmp_path):
    (tmp_path / "App").mkdir()
    file_a = tmp_path / "App" / "a.txt"
    file_a.write_bytes(b"content-a")
    files = [file_a]

    content = build_sums_content(tmp_path, files)
    sums_path = tmp_path / "SHA256SUMS"
    sums_path.write_text(content, encoding="utf-8")

    parsed = parse_sha256sums(sums_path)
    assert "App/a.txt" in parsed
    assert len(parsed["App/a.txt"]) == 64
