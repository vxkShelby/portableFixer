import os
import zipfile

import pytest

from portablefix import handoff

HOST = "PC-KLIENT"
RUN = "20260924T100000-abcd1234"
OTHER_RUN = "20260901T080000-ffff0000"


def _write_run(state_dir, run_id=RUN, host=HOST, undo=True):
    reports = state_dir / "Reports"
    reports.mkdir(parents=True, exist_ok=True)
    (reports / f"{host}_{run_id}.html").write_text(f"<html>{run_id}</html>", encoding="utf-8")
    (reports / f"{host}_{run_id}.json").write_text(f'{{"run_id": "{run_id}"}}', encoding="utf-8")
    logs = state_dir / "Logs"
    logs.mkdir(parents=True, exist_ok=True)
    (logs / f"{run_id}.jsonl").write_text(f'{{"run_id": "{run_id}"}}\n', encoding="utf-8")
    if undo:
        backups = state_dir / "Backups" / run_id
        backups.mkdir(parents=True, exist_ok=True)
        (backups / "undo.ps1").write_text("# undo\n", encoding="utf-8-sig")


def _names(zip_path):
    with zipfile.ZipFile(zip_path) as zf:
        return sorted(zf.namelist())


def test_default_package_name():
    assert handoff.default_package_name(HOST, RUN) == f"PortableFix_{HOST}_{RUN}.zip"


def test_zip_contains_run_files_and_readme(tmp_path):
    state = tmp_path / "state"
    _write_run(state)
    dest = state / "Reports" / handoff.default_package_name(HOST, RUN)

    result = handoff.build_handoff_zip(state, HOST, RUN, dest)

    assert result == dest and dest.is_file()
    assert _names(dest) == sorted(
        ["README.txt", "report.html", "report.json", "audit_log.jsonl", "undo.ps1"]
    )
    with zipfile.ZipFile(dest) as zf:
        assert zf.read("report.html").decode("utf-8") == f"<html>{RUN}</html>"
        assert zf.read("audit_log.jsonl").decode("utf-8") == f'{{"run_id": "{RUN}"}}\n'
        readme = zf.read("README.txt").decode("utf-8-sig")
    assert HOST in readme and RUN in readme
    # Bilingual, with proper Slovak diacritics, and the undo safety advice.
    assert "SLOVENSKY" in readme and "ENGLISH" in readme
    assert "Spúšťajte" in readme and "správca" in readme
    assert "Administrator" in readme and "undo.ps1" in readme


def test_missing_optional_files_are_skipped(tmp_path):
    state = tmp_path / "state"
    _write_run(state, undo=False)
    (state / "Reports" / f"{HOST}_{RUN}.json").unlink()

    dest = handoff.build_handoff_zip(state, HOST, RUN, tmp_path / "out.zip")

    assert _names(dest) == ["README.txt", "audit_log.jsonl", "report.html"]


def test_no_files_for_run_raises_value_error(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    with pytest.raises(ValueError):
        handoff.build_handoff_zip(state, HOST, RUN, tmp_path / "out.zip")
    assert not (tmp_path / "out.zip").exists()


def test_never_includes_settings_or_other_runs(tmp_path):
    state = tmp_path / "state"
    _write_run(state)
    _write_run(state, run_id=OTHER_RUN)
    (state / "Data").mkdir()
    (state / "Data" / "settings.json").write_text('{"technician_name": "x"}', encoding="utf-8")
    (state / "Logs" / "crash.log").write_text("boom", encoding="utf-8")

    dest = handoff.build_handoff_zip(state, HOST, RUN, tmp_path / "out.zip")

    with zipfile.ZipFile(dest) as zf:
        payload = b"".join(zf.read(name) for name in zf.namelist())
        names = zf.namelist()
    assert not any("settings" in n for n in names)
    assert OTHER_RUN.encode() not in payload
    assert b"technician_name" not in payload and b"boom" not in payload


@pytest.mark.parametrize(
    "bad_run_id",
    ["../x", "..", "x/../../y", "..\\x", "a/b", "C:evil", "", " padded", "x\x00y"],
)
def test_path_traversal_run_id_rejected(tmp_path, bad_run_id):
    state = tmp_path / "state"
    _write_run(state)
    # A file a traversal would reach if run_id were used unchecked.
    (tmp_path / "x.jsonl").write_text("outside", encoding="utf-8")
    dest = tmp_path / "out.zip"
    with pytest.raises(ValueError):
        handoff.build_handoff_zip(state, HOST, bad_run_id, dest)
    assert not dest.exists()


@pytest.mark.parametrize("bad_host", ["..", "../PC", "a\\b", ""])
def test_path_traversal_hostname_rejected(tmp_path, bad_host):
    state = tmp_path / "state"
    _write_run(state)
    with pytest.raises(ValueError):
        handoff.build_handoff_zip(state, bad_host, RUN, tmp_path / "out.zip")


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="needs symlinks")
def test_symlink_pointing_outside_state_dir_is_not_followed(tmp_path):
    state = tmp_path / "state"
    _write_run(state)
    secret = tmp_path / "secret.txt"
    secret.write_text("TOP-SECRET", encoding="utf-8")
    html = state / "Reports" / f"{HOST}_{RUN}.html"
    html.unlink()
    try:
        html.symlink_to(secret)
    except OSError:
        pytest.skip("symlinks not permitted here")

    dest = handoff.build_handoff_zip(state, HOST, RUN, tmp_path / "out.zip")

    assert "report.html" not in _names(dest)
    with zipfile.ZipFile(dest) as zf:
        assert all(b"TOP-SECRET" not in zf.read(n) for n in zf.namelist())


def test_package_sources_stay_inside_state_dir(tmp_path):
    state = tmp_path / "state"
    _write_run(state)
    root = state.resolve()
    sources = handoff.package_sources(state, HOST, RUN)
    assert [arc for arc, _ in sources] == ["report.html", "report.json", "audit_log.jsonl", "undo.ps1"]
    assert all(path.is_relative_to(root) for _, path in sources)


def test_write_is_atomic_on_failure(tmp_path, monkeypatch):
    state = tmp_path / "state"
    _write_run(state)
    dest = tmp_path / "out" / "pkg.zip"
    dest.parent.mkdir()
    dest.write_bytes(b"previous package")

    def _boom(self, *args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(handoff.zipfile.ZipFile, "write", _boom)
    with pytest.raises(OSError):
        handoff.build_handoff_zip(state, HOST, RUN, dest)

    # The old file is untouched and no temp file is left behind.
    assert dest.read_bytes() == b"previous package"
    assert sorted(p.name for p in dest.parent.iterdir()) == ["pkg.zip"]


def test_write_goes_through_temp_file_and_os_replace(tmp_path, monkeypatch):
    state = tmp_path / "state"
    _write_run(state)
    dest = tmp_path / "pkg.zip"
    calls = []
    real_replace = os.replace

    def _spy(src, dst):
        calls.append((str(src), str(dst)))
        assert zipfile.is_zipfile(src)
        assert not os.path.exists(dst)
        real_replace(src, dst)

    monkeypatch.setattr(handoff.os, "replace", _spy)
    handoff.build_handoff_zip(state, HOST, RUN, dest)
    assert len(calls) == 1
    src, dst = calls[0]
    assert dst == str(dest) and src != dst
    assert os.path.dirname(src) == str(tmp_path)
    assert not os.path.exists(src)


def test_overwrites_existing_package(tmp_path):
    state = tmp_path / "state"
    _write_run(state)
    dest = tmp_path / "pkg.zip"
    dest.write_bytes(b"old")
    handoff.build_handoff_zip(state, HOST, RUN, dest)
    assert zipfile.is_zipfile(dest)


def test_unicode_hostname_is_stored_with_utf8_names(tmp_path):
    host = "PC-Žilina"
    state = tmp_path / "state"
    _write_run(state, host=host)
    dest = tmp_path / handoff.default_package_name(host, RUN)
    handoff.build_handoff_zip(state, host, RUN, dest)
    with zipfile.ZipFile(dest) as zf:
        assert "report.html" in zf.namelist()
        assert host in zf.read("README.txt").decode("utf-8-sig")
