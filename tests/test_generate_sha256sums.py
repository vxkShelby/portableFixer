from pathlib import Path

from portablefix.integrity import parse_sha256sums
from portablefix.sha256sums import MANIFEST_VERSION_PREFIX, parse_manifest_version, parse_sha256sums_text
from portablefix.version import APP_VERSION
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


def test_build_sums_content_names_the_version_in_a_line_older_parsers_skip(tmp_path):
    (tmp_path / "App").mkdir()
    (tmp_path / "App" / "a.txt").write_bytes(b"content-a")

    content = build_sums_content(tmp_path, [tmp_path / "App" / "a.txt"])
    first = content.splitlines()[0]

    assert first == f"{MANIFEST_VERSION_PREFIX}{APP_VERSION}"
    # Clients up to 1.16 keep only lines that split in two; a space in this
    # one would turn it into a "missing file" and make them refuse the update.
    assert first.split() == [first]
    assert list(parse_sha256sums_text(content)) == ["App/a.txt"]
    assert parse_manifest_version(content) == APP_VERSION
    assert build_sums_content(tmp_path, [], "1.17.0").splitlines() == [f"{MANIFEST_VERSION_PREFIX}1.17.0"]


def test_parse_manifest_version_is_none_for_a_manifest_without_one():
    assert parse_manifest_version("abc  App/x\n") is None
    assert parse_manifest_version(f"{MANIFEST_VERSION_PREFIX}\n") is None
