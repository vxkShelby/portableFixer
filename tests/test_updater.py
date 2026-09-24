import hashlib
import json
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from portablefix import updater as updater_module
from portablefix.updater import (
    UpdateCheckRunner,
    UpdateDownloadRunner,
    UpdateInfo,
    UpdateVerificationError,
    check_for_update,
    download_update,
    is_newer,
    is_writable,
    needs_elevation_for_update,
    parse_version,
)


def test_parse_version_strips_v_prefix():
    assert parse_version("v1.2.3") == (1, 2, 3)


def test_parse_version_handles_multi_digit_components():
    assert parse_version("1.10.2") == (1, 10, 2)


def test_is_newer_compares_numerically_not_as_strings():
    assert is_newer("1.10.0", "1.9.0") is True
    assert is_newer("1.9.0", "1.10.0") is False


def test_is_newer_false_when_equal():
    assert is_newer("1.0.0", "1.0.0") is False


def test_parse_version_takes_leading_digits_only_on_hyphenated_prerelease_tag():
    assert parse_version("1.2.3-rc10") == (1, 2, 3)


def test_is_newer_treats_prerelease_tag_as_not_newer_than_next_release():
    assert is_newer("1.2.3-rc10", "1.2.4") is False


def _release_json(tag="v1.1.0", with_sha=True, zip_name="PortableFix-Portable.zip"):
    assets = [{
        "name": zip_name,
        "browser_download_url": "https://github.com/vxkShelby/portableFixer/releases/download/v1.1.0/PortableFix-Portable.zip",
    }]
    if with_sha:
        assets.append({
            "name": "PortableFix-Portable.zip.sha256",
            "browser_download_url": "https://github.com/vxkShelby/portableFixer/releases/download/v1.1.0/PortableFix-Portable.zip.sha256",
        })
    return json.dumps({"tag_name": tag, "assets": assets, "body": "release notes"}).encode("utf-8")


def _mock_response(body: bytes):
    mock_resp = MagicMock()
    mock_resp.read.return_value = body
    mock_resp.__enter__.return_value = mock_resp
    return mock_resp


def _mock_download_response(body: bytes):
    # download_update reads in a chunk loop (`while chunk := resp.read(n)`),
    # unlike the single-shot .read() the sha256/API responses use above -
    # this yields the body once, then b"" to end the loop.
    mock_resp = MagicMock()
    mock_resp.read.side_effect = [body, b""]
    mock_resp.__enter__.return_value = mock_resp
    return mock_resp


def test_check_for_update_returns_none_when_remote_not_newer():
    with patch("portablefix.updater.urllib.request.urlopen", return_value=_mock_response(_release_json(tag="v1.0.0"))):
        assert check_for_update("1.0.0") is None


def test_check_for_update_returns_info_when_remote_newer():
    with patch("portablefix.updater.urllib.request.urlopen", return_value=_mock_response(_release_json(tag="v1.1.0"))):
        info = check_for_update("1.0.0")
    assert info == UpdateInfo(
        version="1.1.0",
        package_url="https://github.com/vxkShelby/portableFixer/releases/download/v1.1.0/PortableFix-Portable.zip",
        sha256_url="https://github.com/vxkShelby/portableFixer/releases/download/v1.1.0/PortableFix-Portable.zip.sha256",
        notes="release notes",
    )


def test_check_for_update_returns_none_on_network_error():
    with patch("portablefix.updater.urllib.request.urlopen", side_effect=urllib.error.URLError("offline")):
        assert check_for_update("1.0.0") is None


def test_check_for_update_returns_none_on_malformed_json():
    with patch("portablefix.updater.urllib.request.urlopen", return_value=_mock_response(b"not json")):
        assert check_for_update("1.0.0") is None


def test_check_for_update_returns_none_when_no_zip_asset_present():
    body = _release_json(tag="v1.1.0", zip_name="SomethingElse.zip")
    with patch("portablefix.updater.urllib.request.urlopen", return_value=_mock_response(body)):
        assert check_for_update("1.0.0") is None


def test_check_for_update_returns_none_when_zip_asset_url_is_untrusted_host():
    body = json.dumps({
        "tag_name": "v1.1.0",
        "assets": [{"name": "PortableFix-Portable.zip", "browser_download_url": "https://evil.example.com/PortableFix-Portable.zip"}],
        "body": "release notes",
    }).encode("utf-8")
    with patch("portablefix.updater.urllib.request.urlopen", return_value=_mock_response(body)):
        assert check_for_update("1.0.0") is None


def test_check_for_update_drops_sha256_url_when_untrusted_host_but_keeps_zip():
    body = json.dumps({
        "tag_name": "v1.1.0",
        "assets": [
            {
                "name": "PortableFix-Portable.zip",
                "browser_download_url": "https://github.com/vxkShelby/portableFixer/releases/download/v1.1.0/PortableFix-Portable.zip",
            },
            {
                "name": "PortableFix-Portable.zip.sha256",
                "browser_download_url": "https://evil.example.com/PortableFix-Portable.zip.sha256",
            },
        ],
        "body": "release notes",
    }).encode("utf-8")
    with patch("portablefix.updater.urllib.request.urlopen", return_value=_mock_response(body)):
        info = check_for_update("1.0.0")
    assert info is not None
    assert info.sha256_url is None


def test_download_update_passes_a_timeout_to_the_package_download(tmp_path):
    # urlretrieve (the previous implementation) had NO timeout at all - a
    # stalled connection hung the download thread forever. Every urlopen
    # call here must be bounded.
    from portablefix.updater import DOWNLOAD_TIMEOUT_SEC

    content = b"fake-zip-content"
    expected_hash = hashlib.sha256(content).hexdigest()
    info = UpdateInfo(
        version="1.1.0",
        package_url="https://example.com/PortableFix-Portable.zip",
        sha256_url="https://example.com/PortableFix-Portable.zip.sha256",
        notes="",
    )
    calls = []

    def fake_urlopen(url, timeout=None):
        calls.append((url, timeout))
        if url == info.package_url:
            return _mock_download_response(content)
        return _mock_response(expected_hash.encode("utf-8"))

    with patch("portablefix.updater.urllib.request.urlopen", side_effect=fake_urlopen):
        download_update(info, tmp_path / "dest")

    assert (info.package_url, DOWNLOAD_TIMEOUT_SEC) in calls
    assert all(timeout is not None for _, timeout in calls)


def test_download_update_succeeds_when_hash_matches(tmp_path):
    content = b"fake-zip-content"
    expected_hash = hashlib.sha256(content).hexdigest()
    info = UpdateInfo(
        version="1.1.0",
        package_url="https://example.com/PortableFix-Portable.zip",
        sha256_url="https://example.com/PortableFix-Portable.zip.sha256",
        notes="",
    )

    def fake_urlopen(url, timeout=None):
        if url == info.package_url:
            return _mock_download_response(content)
        return _mock_response(expected_hash.encode("utf-8"))

    with patch("portablefix.updater.urllib.request.urlopen", side_effect=fake_urlopen):
        result_path = download_update(info, tmp_path / "dest")

    assert result_path.read_bytes() == content
    assert result_path.name == "PortableFix-update.zip"


def test_download_update_raises_and_cleans_up_on_hash_mismatch(tmp_path):
    info = UpdateInfo(
        version="1.1.0",
        package_url="https://example.com/PortableFix-Portable.zip",
        sha256_url="https://example.com/PortableFix-Portable.zip.sha256",
        notes="",
    )
    wrong_hash = "0" * 64

    def fake_urlopen(url, timeout=None):
        if url == info.package_url:
            return _mock_download_response(b"fake-zip-content")
        return _mock_response(wrong_hash.encode("utf-8"))

    dest = tmp_path / "dest"
    with patch("portablefix.updater.urllib.request.urlopen", side_effect=fake_urlopen):
        with pytest.raises(UpdateVerificationError):
            download_update(info, dest)

    assert not (dest / "PortableFix-update.zip").exists()


def test_download_update_cleans_up_partial_file_on_read_failure(tmp_path):
    info = UpdateInfo(
        version="1.1.0",
        package_url="https://example.com/PortableFix-Portable.zip",
        sha256_url="https://example.com/PortableFix-Portable.zip.sha256",
        notes="",
    )
    dest = tmp_path / "dest"

    mock_resp = MagicMock()
    mock_resp.read.side_effect = [b"partial", ConnectionError("connection dropped")]
    mock_resp.__enter__.return_value = mock_resp

    with patch("portablefix.updater.urllib.request.urlopen", return_value=mock_resp):
        with pytest.raises(ConnectionError):
            download_update(info, dest)

    assert not (dest / "PortableFix-update.zip").exists()


def test_download_update_reports_progress_via_content_length(tmp_path):
    content = b"x" * 100
    expected_hash = hashlib.sha256(content).hexdigest()
    info = UpdateInfo(
        version="1.1.0",
        package_url="https://example.com/PortableFix-Portable.zip",
        sha256_url="https://example.com/PortableFix-Portable.zip.sha256",
        notes="",
    )

    def fake_urlopen(url, timeout=None):
        if url == info.package_url:
            mock_resp = MagicMock()
            mock_resp.read.side_effect = [content[:60], content[60:], b""]
            mock_resp.getheader.return_value = str(len(content))
            mock_resp.__enter__.return_value = mock_resp
            return mock_resp
        return _mock_response(expected_hash.encode("utf-8"))

    calls = []
    with patch("portablefix.updater.urllib.request.urlopen", side_effect=fake_urlopen):
        download_update(info, tmp_path / "dest", on_progress=lambda d, t: calls.append((d, t)))

    assert calls == [(60, 100), (100, 100)]


def test_download_update_reports_zero_total_when_content_length_missing(tmp_path):
    # A missing/malformed Content-Length header must degrade to "unknown
    # size" (0), not crash the download - the GUI shows an indeterminate
    # progress bar in that case instead of a frozen one.
    content = b"abc"
    expected_hash = hashlib.sha256(content).hexdigest()
    info = UpdateInfo(
        version="1.1.0",
        package_url="https://example.com/PortableFix-Portable.zip",
        sha256_url="https://example.com/PortableFix-Portable.zip.sha256",
        notes="",
    )

    def fake_urlopen(url, timeout=None):
        if url == info.package_url:
            mock_resp = MagicMock()
            mock_resp.read.side_effect = [content, b""]
            mock_resp.getheader.return_value = None
            mock_resp.__enter__.return_value = mock_resp
            return mock_resp
        return _mock_response(expected_hash.encode("utf-8"))

    calls = []
    with patch("portablefix.updater.urllib.request.urlopen", side_effect=fake_urlopen):
        download_update(info, tmp_path / "dest", on_progress=lambda d, t: calls.append((d, t)))

    assert calls == [(3, 0)]


def test_download_update_raises_when_no_sha256_asset(tmp_path):
    # Fail closed: a release with no SHA256 manifest must never be trusted
    # silently - refusing beats installing an unverified zip.
    info = UpdateInfo(
        version="1.1.0", package_url="https://example.com/PortableFix-Portable.zip", sha256_url=None, notes="",
    )

    with pytest.raises(UpdateVerificationError):
        download_update(info, tmp_path / "dest")

    assert not (tmp_path / "dest" / "PortableFix-update.zip").exists()


def test_is_writable_true_for_writable_directory(tmp_path):
    assert is_writable(tmp_path) is True


def test_is_writable_false_for_missing_directory(tmp_path):
    assert is_writable(tmp_path / "does_not_exist") is False


def test_needs_elevation_false_when_already_admin(tmp_path, monkeypatch):
    monkeypatch.setattr(updater_module.elevation, "is_admin", lambda: True)
    monkeypatch.setenv("ProgramFiles", str(tmp_path))
    protected = tmp_path / "PortableFix"
    protected.mkdir()
    assert needs_elevation_for_update(protected) is False


def test_needs_elevation_true_for_program_files_when_not_admin(tmp_path, monkeypatch):
    monkeypatch.setattr(updater_module.elevation, "is_admin", lambda: False)
    monkeypatch.setenv("ProgramFiles", str(tmp_path))
    protected = tmp_path / "PortableFix"
    protected.mkdir()
    assert needs_elevation_for_update(protected) is True


def test_needs_elevation_false_for_ordinary_directory_when_not_admin(tmp_path, monkeypatch):
    monkeypatch.setattr(updater_module.elevation, "is_admin", lambda: False)
    monkeypatch.setenv("ProgramFiles", str(tmp_path / "not_used"))
    ordinary = tmp_path / "USB Fixer"
    ordinary.mkdir()
    assert needs_elevation_for_update(ordinary) is False


def test_is_writable_false_under_protected_path_even_though_probe_write_would_succeed(tmp_path, monkeypatch):
    # This is the UAC-virtualization scenario: a plain write-then-read-back
    # probe would happily succeed (real or virtualized), which is exactly
    # why is_writable() must refuse based on the path+elevation check rather
    # than trusting the probe under a protected root.
    monkeypatch.setattr(updater_module.elevation, "is_admin", lambda: False)
    monkeypatch.setenv("ProgramFiles", str(tmp_path))
    protected = tmp_path / "PortableFix"
    protected.mkdir()
    assert is_writable(protected) is False


def test_update_check_runner_emits_none_when_no_update(qtbot):
    with patch("portablefix.updater.check_for_update", return_value=None):
        runner = UpdateCheckRunner("1.0.0")
        with qtbot.waitSignal(runner.check_finished, timeout=2000) as blocker:
            runner.start()
    assert blocker.args == [None]


def test_update_check_runner_emits_update_info(qtbot):
    info = UpdateInfo(version="1.1.0", package_url="https://x", sha256_url=None, notes="")
    with patch("portablefix.updater.check_for_update", return_value=info):
        runner = UpdateCheckRunner("1.0.0")
        with qtbot.waitSignal(runner.check_finished, timeout=2000) as blocker:
            runner.start()
    assert blocker.args == [info]


def test_update_download_runner_emits_path_on_success(qtbot, tmp_path):
    info = UpdateInfo(version="1.1.0", package_url="https://x", sha256_url=None, notes="")
    fake_path = tmp_path / "PortableFix-update.zip"
    fake_path.write_bytes(b"x")
    with patch("portablefix.updater.download_update", return_value=fake_path):
        runner = UpdateDownloadRunner(info, tmp_path)
        with qtbot.waitSignal(runner.download_finished, timeout=2000) as blocker:
            runner.start()
    assert blocker.args == [fake_path, ""]


def test_update_download_runner_forwards_progress_signal(qtbot, tmp_path):
    info = UpdateInfo(version="1.1.0", package_url="https://x", sha256_url=None, notes="")

    def fake_download_update(info, dest_dir, on_progress=None, should_stop=None):
        on_progress(50, 100)
        on_progress(100, 100)
        return tmp_path / "PortableFix-update.zip"

    with patch("portablefix.updater.download_update", side_effect=fake_download_update):
        runner = UpdateDownloadRunner(info, tmp_path)
        progress_calls = []
        runner.progress.connect(lambda d, t: progress_calls.append((d, t)))
        with qtbot.waitSignal(runner.download_finished, timeout=2000):
            runner.start()
    assert progress_calls == [(50, 100), (100, 100)]


def test_download_update_stops_between_chunks_and_deletes_the_partial_file(tmp_path):
    # Closing the app mid-download used to wait out the whole download (or
    # destroy its QThread); now it stops at the next chunk.
    info = UpdateInfo(
        version="1.1.0",
        package_url="https://example.com/PortableFix-Portable.zip",
        sha256_url="https://example.com/PortableFix-Portable.zip.sha256",
        notes="",
    )
    mock_resp = MagicMock()
    mock_resp.read.side_effect = [b"a" * 10, b"b" * 10, b""]
    mock_resp.getheader.return_value = "20"
    mock_resp.__enter__.return_value = mock_resp
    stop_after = iter([False, True])

    with patch("portablefix.updater.urllib.request.urlopen", return_value=mock_resp) as urlopen:
        with pytest.raises(updater_module.UpdateDownloadCancelled):
            download_update(info, tmp_path / "dest", should_stop=lambda: next(stop_after))

    assert mock_resp.read.call_count == 1
    assert not (tmp_path / "dest" / "PortableFix-update.zip").exists()
    # The .sha256 manifest is never fetched for a cancelled download.
    assert urlopen.call_count == 1


def test_copy_local_update_copies_and_verifies_without_touching_the_source(tmp_path):
    source = tmp_path / "PortableFix-Portable.zip"
    source.write_bytes(b"release" * 1000)
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    progress = []

    copied = updater_module.copy_local_update(
        source, digest.upper(), tmp_path / "dest", on_progress=lambda d, t: progress.append((d, t)),
    )

    assert copied == tmp_path / "dest" / "PortableFix-update.zip"
    assert copied.read_bytes() == source.read_bytes()
    assert source.exists()
    assert progress[-1] == (7000, 7000)


@pytest.mark.parametrize("sha256", ["", "abc", "0" * 64])
def test_copy_local_update_refuses_a_missing_or_wrong_hash(tmp_path, sha256):
    source = tmp_path / "p.zip"
    source.write_bytes(b"zip")

    with pytest.raises(UpdateVerificationError):
        updater_module.copy_local_update(source, sha256, tmp_path / "dest")

    assert not (tmp_path / "dest" / "PortableFix-update.zip").exists()


def test_update_download_runner_copies_a_local_zip_for_the_dev_switch(qtbot, tmp_path):
    source = tmp_path / "p.zip"
    source.write_bytes(b"zip")
    info = UpdateInfo(version="p.zip (dev)", package_url=str(source), sha256_url=None, notes="")
    runner = UpdateDownloadRunner(
        info, tmp_path / "dest", local_zip=source, local_sha256=hashlib.sha256(b"zip").hexdigest(),
    )
    with qtbot.waitSignal(runner.download_finished, timeout=5000) as blocker:
        runner.start()
    assert blocker.args == [tmp_path / "dest" / "PortableFix-update.zip", ""]


def test_update_download_runner_emits_error_on_failure(qtbot, tmp_path):
    info = UpdateInfo(version="1.1.0", package_url="https://x", sha256_url=None, notes="")
    with patch("portablefix.updater.download_update", side_effect=UpdateVerificationError("bad hash")):
        runner = UpdateDownloadRunner(info, tmp_path)
        with qtbot.waitSignal(runner.download_finished, timeout=2000) as blocker:
            runner.start()
    assert blocker.args == [None, "bad hash"]


@pytest.mark.parametrize("manifest", [b"", b"   \n", b"not-a-hash  PortableFix-Portable.zip"])
def test_download_update_rejects_empty_or_malformed_manifest(tmp_path, manifest):
    # Previously an empty manifest raised IndexError instead of a
    # verification error, and a non-hash token was compared as-is.
    info = UpdateInfo(
        version="1.1.0",
        package_url="https://example.com/PortableFix-Portable.zip",
        sha256_url="https://example.com/PortableFix-Portable.zip.sha256",
        notes="",
    )

    def fake_urlopen(url, timeout=None):
        if url == info.package_url:
            return _mock_download_response(b"fake-zip-content")
        return _mock_response(manifest)

    dest = tmp_path / "dest"
    with patch("portablefix.updater.urllib.request.urlopen", side_effect=fake_urlopen):
        with pytest.raises(UpdateVerificationError):
            download_update(info, dest)
    assert not (dest / "PortableFix-update.zip").exists()


def test_download_update_accepts_uppercase_manifest_hash_with_filename(tmp_path):
    content = b"fake-zip-content"
    info = UpdateInfo(
        version="1.1.0",
        package_url="https://example.com/PortableFix-Portable.zip",
        sha256_url="https://example.com/PortableFix-Portable.zip.sha256",
        notes="",
    )
    manifest = f"{hashlib.sha256(content).hexdigest().upper()}  PortableFix-Portable.zip\n".encode()

    def fake_urlopen(url, timeout=None):
        if url == info.package_url:
            return _mock_download_response(content)
        return _mock_response(manifest)

    with patch("portablefix.updater.urllib.request.urlopen", side_effect=fake_urlopen):
        result_path = download_update(info, tmp_path / "dest")
    assert result_path.read_bytes() == content


# --- Resilience of the update swap (docs/research/research-resilience.md 5.2-5.4, 4.3, 1.2) ---


def test_recover_interrupted_swap_restores_only_missing_live_folders(tmp_path):
    (tmp_path / "Modules.old").mkdir()
    (tmp_path / "Modules.old" / "m.yaml").write_text("x")
    (tmp_path / "Vendor").mkdir()
    (tmp_path / "Vendor.old").mkdir()

    restored = updater_module.recover_interrupted_swap(tmp_path)

    assert restored == ["Modules"]
    assert (tmp_path / "Modules" / "m.yaml").exists()
    assert (tmp_path / "Vendor.old").exists()  # live Vendor present - left alone


def test_consume_update_status_reads_once_then_deletes(tmp_path):
    (tmp_path / "Data").mkdir()
    updater_module.update_status_path(tmp_path).write_text("rolled_back\r\n", encoding="ascii")

    assert updater_module.consume_update_status(tmp_path) == "rolled_back"
    assert updater_module.consume_update_status(tmp_path) is None


@pytest.mark.parametrize(
    "status, restored, expected",
    [
        (None, [], None),
        ("ok", [], None),
        ("in_progress", [], "update_status_interrupted"),
        (None, ["Modules"], "update_status_interrupted"),
        ("aborted", [], "update_status_failed"),
        ("rolled_back", [], "update_status_failed"),
        ("ok_sums_stale", [], "update_status_sums_stale"),
        # The app handed off, but the updater died before it wrote anything.
        ("handed_off", [], "update_status_incomplete"),
    ],
)
def test_update_status_message_key(status, restored, expected):
    assert updater_module.update_status_message_key(status, restored) == expected


def test_launcher_cmd_restores_app_folder_stranded_as_app_old():
    # Without App\PortableFix.exe there is no Python code left to run any
    # recovery - the launcher is the only thing that can put App.old back.
    cmd = (Path(__file__).resolve().parent.parent / "PortableFix.cmd").read_text(encoding="utf-8")
    restore = 'if not exist "%~dp0App\\" if exist "%~dp0App.old\\PortableFix.exe" move "%~dp0App.old" "%~dp0App"'
    assert restore in cmd
    assert cmd.index(restore) < cmd.rindex('"%~dp0App\\PortableFix.exe"')


def test_swap_backups_use_the_names_startup_recovery_restores(tmp_path):
    # The swap script, recover_interrupted_swap and PortableFix.cmd must agree
    # on X.old - a stick pulled mid-swap is only recoverable if they do.
    from portablefix.update_swap import StagedUpdate, write_swap_job

    staged = StagedUpdate(stage_dir=tmp_path / "_update_stage", stage_root=tmp_path / "_update_stage" / "PortableFix",
                          file_count=0, byte_count=0)
    job = write_swap_job(staged, tmp_path, [1], tmp_path / "logs")
    for folder in json.loads(job.job_path.read_text(encoding="ascii"))["Folders"]:
        assert Path(folder["Backup"]) == tmp_path / f"{folder['Name']}.old"
        Path(folder["Backup"]).mkdir()
    assert updater_module.recover_interrupted_swap(tmp_path) == ["App", "Modules", "Vendor"]


def test_update_stage_runner_emits_the_staged_update(qtbot, tmp_path):
    staged = updater_module.StagedUpdate(stage_dir=tmp_path, stage_root=tmp_path, file_count=1, byte_count=1)
    calls = []

    def fake_stage(zip_path, install_dir, should_stop=None, progress=None, version=None):
        calls.append(version)
        progress(50, 100)
        return staged

    with patch("portablefix.updater.stage_update", side_effect=fake_stage):
        runner = updater_module.UpdateStageRunner(tmp_path / "u.zip", tmp_path, version="1.12.0")
        progress = []
        runner.progress.connect(lambda d, t: progress.append((d, t)))
        with qtbot.waitSignal(runner.stage_finished, timeout=5000) as blocker:
            runner.start()
    assert blocker.args == [staged, ""]
    assert calls == ["1.12.0"]
    assert progress == [(50, 100)]


def test_update_stage_runner_reports_progress_of_huge_packages_in_kib(qtbot, tmp_path):
    def fake_stage(zip_path, install_dir, should_stop=None, progress=None, version=None):
        progress(3 * 2**30, 4 * 2**30)
        return None

    with patch("portablefix.updater.stage_update", side_effect=fake_stage):
        runner = updater_module.UpdateStageRunner(tmp_path / "u.zip", tmp_path)
        progress = []
        runner.progress.connect(lambda d, t: progress.append((d, t)))
        with qtbot.waitSignal(runner.stage_finished, timeout=5000):
            runner.start()
    assert progress == [(3 * 2**20, 4 * 2**20)]


def test_update_stage_runner_emits_the_error_text(qtbot, tmp_path):
    with patch("portablefix.updater.stage_update", side_effect=updater_module.UpdateStageError("no Vendor")):
        runner = updater_module.UpdateStageRunner(tmp_path / "u.zip", tmp_path)
        with qtbot.waitSignal(runner.stage_finished, timeout=5000) as blocker:
            runner.start()
    assert blocker.args == [None, "no Vendor"]


def test_update_launch_runner_emits_the_launch_result(qtbot, tmp_path):
    result = updater_module.LaunchResult(ok=True, route="direct")
    with patch("portablefix.updater.launch_swap", return_value=result) as launch:
        runner = updater_module.UpdateLaunchRunner(object(), tmp_path)
        with qtbot.waitSignal(runner.launch_finished, timeout=5000) as blocker:
            runner.start()
    assert blocker.args == [result]
    assert launch.call_args.kwargs["should_stop"] is not None


def test_update_launch_runner_turns_an_unexpected_error_into_a_failed_result(qtbot, tmp_path):
    with patch("portablefix.updater.launch_swap", side_effect=ValueError("boom")):
        runner = updater_module.UpdateLaunchRunner(object(), tmp_path)
        with qtbot.waitSignal(runner.launch_finished, timeout=5000) as blocker:
            runner.start()
    (result,) = blocker.args
    assert result.ok is False
    assert result.reason == updater_module.REASON_SPAWN_ERROR
    assert "boom" in result.detail
