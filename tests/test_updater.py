import hashlib
import json
import sys
import urllib.error
from pathlib import Path, PureWindowsPath
from unittest.mock import MagicMock, patch

import pytest

from portablefix import updater as updater_module
from portablefix.updater import (
    UpdateCheckRunner,
    UpdateDownloadRunner,
    UpdateInfo,
    UpdateVerificationError,
    apply_update,
    build_swap_script,
    check_for_update,
    download_update,
    is_newer,
    is_writable,
    needs_elevation_for_update,
    parse_version,
)


def _powershell_or_skip() -> str:
    """Windows PowerShell where it exists, else PowerShell 7 (pwsh) - which
    lets the generated swap scripts be parsed and actually run on a
    non-Windows dev/CI box too. Skips when neither is installed."""
    import os
    import shutil

    exe = os.environ.get("PORTABLEFIX_TEST_PWSH") or shutil.which("powershell") or shutil.which("pwsh")
    if not exe:
        pytest.skip("no PowerShell available")
    return exe


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


def _running_process():
    # A swap script that is still running when apply_update stops watching
    # it - i.e. waiting for this process to exit, as designed.
    process = MagicMock()
    process.wait.side_effect = updater_module.subprocess.TimeoutExpired("powershell", 1)
    return process


@pytest.fixture(autouse=True)
def _windows_creationflags(monkeypatch):
    # apply_update ORs Windows-only Popen flags together; give them values
    # on other platforms so its logic is testable there (Popen is mocked).
    for name, value in (("DETACHED_PROCESS", 0x8), ("CREATE_NEW_PROCESS_GROUP", 0x200),
                        ("CREATE_BREAKAWAY_FROM_JOB", 0x1000000)):
        monkeypatch.setattr(updater_module.subprocess, name, getattr(updater_module.subprocess, name, value), raising=False)


def test_apply_update_writes_ps1_script_with_utf8_bom(tmp_path, monkeypatch):
    monkeypatch.setattr(updater_module.tempfile, "gettempdir", lambda: str(tmp_path))
    monkeypatch.setattr(updater_module.subprocess, "Popen", lambda *a, **k: _running_process())
    install_dir = tmp_path / "install"
    install_dir.mkdir()

    apply_update(zip_path=tmp_path / "PortableFix-update.zip", install_dir=install_dir)

    scripts = list(tmp_path.glob("portablefix_update_*.ps1"))
    assert len(scripts) == 1
    assert scripts[0].read_bytes()[:3] == b"\xef\xbb\xbf"


def test_apply_update_breaks_away_from_parent_job_object(tmp_path, monkeypatch):
    # Regression test: without CREATE_BREAKAWAY_FROM_JOB, the swap script
    # dies with the parent when the parent is inside a Job Object with
    # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE (e.g. launched from Windows
    # Terminal) - the update then silently never happens.
    monkeypatch.setattr(updater_module.tempfile, "gettempdir", lambda: str(tmp_path))
    calls = []
    monkeypatch.setattr(
        updater_module.subprocess, "Popen", lambda *a, **k: calls.append(k) or _running_process()
    )
    install_dir = tmp_path / "install"
    install_dir.mkdir()

    apply_update(zip_path=tmp_path / "PortableFix-update.zip", install_dir=install_dir)

    assert len(calls) == 1
    flags = calls[0]["creationflags"]
    assert flags & updater_module.subprocess.CREATE_BREAKAWAY_FROM_JOB


def test_apply_update_redirects_popen_stdout_and_stderr_to_a_launch_log(tmp_path, monkeypatch):
    # DETACHED_PROCESS gives the child no console/inherited std handles - an
    # unredirected startup failure (e.g. an execution-policy refusal) prints
    # to nowhere and is silently lost. The log dir/file must exist BEFORE
    # Popen runs (created here in Python), since the failure this is meant
    # to diagnose can happen before the script's own first line ever runs.
    monkeypatch.setattr(updater_module.tempfile, "gettempdir", lambda: str(tmp_path))
    calls = []
    monkeypatch.setattr(
        updater_module.subprocess, "Popen", lambda *a, **k: calls.append(k) or _running_process()
    )
    install_dir = tmp_path / "install"
    install_dir.mkdir()

    result = apply_update(zip_path=tmp_path / "PortableFix-update.zip", install_dir=install_dir)

    assert result is True
    assert len(calls) == 1
    assert calls[0]["stdout"] is not None
    assert calls[0]["stdout"].closed
    assert calls[0]["stderr"] == updater_module.subprocess.STDOUT
    assert calls[0]["stdin"] == updater_module.subprocess.DEVNULL
    log_files = list((tmp_path / "PortableFixUpdate").glob("popen_launch_*.log"))
    assert len(log_files) == 1


def test_apply_update_returns_true_on_success(tmp_path, monkeypatch):
    monkeypatch.setattr(updater_module.tempfile, "gettempdir", lambda: str(tmp_path))
    monkeypatch.setattr(updater_module.subprocess, "Popen", lambda *a, **k: _running_process())
    install_dir = tmp_path / "install"
    install_dir.mkdir()

    result = apply_update(zip_path=tmp_path / "PortableFix-update.zip", install_dir=install_dir)

    assert result is True


def test_apply_update_returns_false_without_spawning_when_not_writable(tmp_path, monkeypatch):
    popen_calls = []
    monkeypatch.setattr(updater_module.subprocess, "Popen", lambda *a, **k: popen_calls.append(1))

    result = apply_update(zip_path=tmp_path / "PortableFix-update.zip", install_dir=tmp_path / "missing_install_dir")

    assert result is False
    assert popen_calls == []


def test_apply_update_returns_false_when_popen_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(updater_module.tempfile, "gettempdir", lambda: str(tmp_path))
    install_dir = tmp_path / "install"
    install_dir.mkdir()

    def raise_oserror(*a, **k):
        raise OSError("no such file")

    monkeypatch.setattr(updater_module.subprocess, "Popen", raise_oserror)

    result = apply_update(zip_path=tmp_path / "PortableFix-update.zip", install_dir=install_dir)

    assert result is False


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


def test_build_swap_script_parses_as_valid_powershell():
    import os
    import subprocess

    script = build_swap_script(
        current_pid=12345,
        install_dir=PureWindowsPath(r"C:\Users\test\USB Fixer"),
        zip_path=PureWindowsPath(r"C:\Users\test\AppData\Local\Temp\PortableFix-update.zip"),
    )
    env = os.environ.copy()
    env["PFCMD"] = script
    result = subprocess.run(
        [_powershell_or_skip(), "-NoProfile", "-NonInteractive", "-Command",
         "[scriptblock]::Create($env:PFCMD) | Out-Null; Write-Output OK"],
        env=env, capture_output=True, text=True,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert "OK" in result.stdout, result.stderr


def test_build_swap_script_quotes_paths_with_spaces():
    script = build_swap_script(
        current_pid=1,
        install_dir=PureWindowsPath(r"C:\Users\test\USB Fixer"),
        zip_path=PureWindowsPath(r"C:\Temp\PortableFix-update.zip"),
    )
    assert "'C:\\Users\\test\\USB Fixer\\App'" in script


def test_build_swap_script_single_quotes_do_not_interpolate_dollar_sign():
    # PowerShell interpolates $variables inside double-quoted strings but
    # never inside single-quoted ones - a literal '$' is a legal NTFS path
    # character (e.g. a username) that would otherwise silently truncate
    # the path. Verified two ways: the raw script text uses single quotes
    # around the $-containing path, and the script actually parses.
    import os
    import subprocess

    install_dir = PureWindowsPath(r"C:\Users\Jane$Doe\USB Fixer")
    script = build_swap_script(
        current_pid=1,
        install_dir=install_dir,
        zip_path=PureWindowsPath(r"C:\Temp\PortableFix-update.zip"),
    )
    assert "'C:\\Users\\Jane$Doe\\USB Fixer\\App'" in script
    assert '"C:\\Users\\Jane$Doe' not in script

    env = os.environ.copy()
    env["PFCMD"] = script
    result = subprocess.run(
        [_powershell_or_skip(), "-NoProfile", "-NonInteractive", "-Command",
         "[scriptblock]::Create($env:PFCMD) | Out-Null; Write-Output OK"],
        env=env, capture_output=True, text=True,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert "OK" in result.stdout, result.stderr


def test_build_swap_script_escapes_embedded_single_quote_in_path():
    install_dir = PureWindowsPath(r"C:\Users\O'Brien\USB Fixer")
    script = build_swap_script(
        current_pid=1,
        install_dir=install_dir,
        zip_path=PureWindowsPath(r"C:\Temp\PortableFix-update.zip"),
    )
    assert "O''Brien" in script


def test_build_swap_script_restores_backup_folders_if_swap_fails_to_verify():
    script = build_swap_script(
        current_pid=1,
        install_dir=PureWindowsPath(r"C:\App"),
        zip_path=PureWindowsPath(r"C:\Temp\PortableFix-update.zip"),
    )
    assert (
        "if ((Test-Path -LiteralPath 'C:\\App\\App\\PortableFix.exe') -and (Test-Path -LiteralPath 'C:\\App\\Modules') "
        "-and (Get-ChildItem -LiteralPath 'C:\\App\\Modules' -EA SilentlyContinue) "
        "-and (Test-Path -LiteralPath 'C:\\App\\Vendor') -and (Get-ChildItem -LiteralPath 'C:\\App\\Vendor' -EA SilentlyContinue)) {"
    ) in script
    assert "Move-Item -LiteralPath 'C:\\App\\App.old' -Destination 'C:\\App\\App' -Force" in script
    assert "Move-Item -LiteralPath 'C:\\App\\Modules.old' -Destination 'C:\\App\\Modules' -Force" in script
    assert "Move-Item -LiteralPath 'C:\\App\\Vendor.old' -Destination 'C:\\App\\Vendor' -Force" in script


def test_build_swap_script_swaps_vendor_folder_with_backup(tmp_path):
    script = build_swap_script(
        current_pid=1,
        install_dir=PureWindowsPath(r"C:\App"),
        zip_path=PureWindowsPath(r"C:\Temp\PortableFix-update.zip"),
    )
    assert "if (Test-Path -LiteralPath 'C:\\App\\Vendor') { Move-Item -LiteralPath 'C:\\App\\Vendor' -Destination 'C:\\App\\Vendor.old' -Force }" in script
    assert 'if (Test-Path -LiteralPath "$stagedRoot\\Vendor") { Move-Item -LiteralPath "$stagedRoot\\Vendor" -Destination \'C:\\App\\Vendor\' -Force }' in script


def test_build_swap_script_rejects_zip_entries_that_escape_the_stage_directory():
    script = build_swap_script(
        current_pid=1,
        install_dir=PureWindowsPath(r"C:\App"),
        zip_path=PureWindowsPath(r"C:\Temp\PortableFix-update.zip"),
    )
    # Checked on the zip's own entries before extraction (the old check
    # listed the stage folder afterwards and could never trip), and a hit
    # fails $stageOk so the old version is still relaunched - no exit.
    assert "[IO.Compression.ZipFile]::OpenRead(" in script
    assert "$target.StartsWith($stageRoot, [StringComparison]::OrdinalIgnoreCase)" in script
    assert script.index("$zip.Entries") < script.index("Expand-Archive")
    assert "exit 1" not in script


def test_build_swap_script_preserves_settings_json_across_the_swap():
    script = build_swap_script(
        current_pid=1,
        install_dir=PureWindowsPath(r"C:\App"),
        zip_path=PureWindowsPath(r"C:\Temp\PortableFix-update.zip"),
    )
    assert "settings.json" in script
    assert "settings.json.bak" in script


def test_build_swap_script_retries_deleting_the_zip():
    # Expand-Archive can hold the zip handle open a moment after returning;
    # a single Remove-Item can silently no-op on that transient lock.
    script = build_swap_script(
        current_pid=1,
        install_dir=PureWindowsPath(r"C:\App"),
        zip_path=PureWindowsPath(r"C:\Temp\PortableFix-update.zip"),
    )
    assert script.count("Remove-Item -LiteralPath 'C:\\Temp\\PortableFix-update.zip'") == 1
    assert "for ($i = 0; $i -lt 30; $i++)" in script


def test_build_swap_script_relaunches_before_cleaning_up_temp_files():
    # A freshly-downloaded zip can sit under AV scanning for many seconds;
    # the relaunch must never wait on that cleanup finishing first.
    script = build_swap_script(
        current_pid=1,
        install_dir=PureWindowsPath(r"C:\App"),
        zip_path=PureWindowsPath(r"C:\Temp\PortableFix-update.zip"),
    )
    relaunch_pos = script.index("Start-Process -FilePath 'C:\\App\\App\\PortableFix.exe'")
    zip_cleanup_pos = script.index("Remove-Item -LiteralPath 'C:\\Temp\\PortableFix-update.zip'")
    assert relaunch_pos < zip_cleanup_pos


def test_build_swap_script_expands_the_downloaded_zip():
    script = build_swap_script(
        current_pid=1,
        install_dir=PureWindowsPath(r"C:\App"),
        zip_path=PureWindowsPath(r"C:\Temp\PortableFix-update.zip"),
    )
    assert "Expand-Archive" in script
    assert "'C:\\Temp\\PortableFix-update.zip'" in script


def test_build_swap_script_handles_non_ascii_path_component():
    # Proves the non-ASCII path embeds correctly into the generated script
    # text (string-level round trip). This does NOT prove PowerShell's own
    # ANSI/UTF-8 decoding of the .ps1 file on disk - that depends on the
    # system codepage and isn't testable from here; the BOM added in
    # apply_update (utf-8-sig) is what makes powershell.exe -File decode it
    # as UTF-8 regardless of codepage.
    install_dir = PureWindowsPath(r"C:\Users\Ondřej Čučko\USB Fixer")
    script = build_swap_script(
        current_pid=1,
        install_dir=install_dir,
        zip_path=PureWindowsPath(r"C:\Temp\PortableFix-update.zip"),
    )
    assert str(install_dir) in script


def test_build_swap_script_contains_pid_wait_loop():
    script = build_swap_script(
        current_pid=54321,
        install_dir=PureWindowsPath(r"C:\App"),
        zip_path=PureWindowsPath(r"C:\Temp\PortableFix-update.zip"),
    )
    assert "54321" in script
    assert "Get-Process" in script


def test_build_swap_script_logs_to_a_temp_file_under_pid(tmp_path, monkeypatch):
    monkeypatch.setattr(updater_module.tempfile, "gettempdir", lambda: str(tmp_path))
    script = build_swap_script(
        current_pid=999,
        install_dir=PureWindowsPath(r"C:\App"),
        zip_path=PureWindowsPath(r"C:\Temp\PortableFix-update.zip"),
    )
    assert "update_log_999.txt" in script
    assert "PortableFixUpdate" in script
    assert "function Log(" in script


def test_build_swap_script_aborts_without_swapping_if_process_still_running_after_wait():
    # If the old exe hasn't exited by the time the wait loop gives up, its
    # files are still locked - Move-Item would fail silently. The script
    # must set the abort flag BEFORE touching App/Modules/Vendor, rather
    # than proceeding into a doomed swap.
    script = build_swap_script(
        current_pid=42,
        install_dir=PureWindowsPath(r"C:\App"),
        zip_path=PureWindowsPath(r"C:\Temp\PortableFix-update.zip"),
    )
    abort_pos = script.index("ABORT: pid 42 did not exit")
    first_move_pos = script.index("Move-Item -LiteralPath 'C:\\App\\App' -Destination 'C:\\App\\App.old'")
    assert abort_pos < first_move_pos
    swap_aborted_pos = script.index("$swapAborted = $true", abort_pos)
    assert swap_aborted_pos < first_move_pos


def test_build_swap_script_skips_relaunch_on_pid_wait_abort():
    # $swapAborted means the OLD process is still alive right now (that's
    # the abort condition) - relaunching would spawn a second instance that
    # immediately loses to the single-instance mutex in main.py and exits
    # silently, which is exactly "clicked restart, nothing happened, still
    # old version". An earlier version of this test asserted the opposite
    # (relaunch must always fire) - that was true only before main.py
    # gained a single-instance mutex; unconditional relaunch is what broke
    # the update flow once that mutex existed.
    script = build_swap_script(
        current_pid=42,
        install_dir=PureWindowsPath(r"C:\App"),
        zip_path=PureWindowsPath(r"C:\Temp\PortableFix-update.zip"),
    )
    abort_pos = script.index("ABORT: pid 42 did not exit")
    guard_pos = script.index("if (-not $swapAborted) {\n    Log \"relaunching via")
    assert abort_pos < guard_pos
    # the abort branch must not exit the script early
    relaunch_pos = script.index("Start-Process -FilePath 'C:\\App\\App\\PortableFix.exe'")
    assert "\n    exit 1\n" not in script[abort_pos:relaunch_pos]
    assert "skipping relaunch - old process is still running" in script


def test_build_swap_script_verifies_relaunch_and_falls_back(tmp_path):
    script = build_swap_script(
        current_pid=1,
        install_dir=PureWindowsPath(r"C:\App"),
        zip_path=PureWindowsPath(r"C:\Temp\PortableFix-update.zip"),
    )
    assert "$relaunchOk = [bool](Get-Process -EA SilentlyContinue | Where-Object { $_.Path -eq 'C:\\App\\App\\PortableFix.exe' })" in script
    assert "if (-not $relaunchOk) {" in script
    assert "retrying once" in script


def _make_release_zip(tmp_path: Path, exe_marker: bytes = b"new-exe") -> Path:
    # Mirrors the real release contract: one top-level folder containing
    # App/, Data/, Modules/, Vendor/, PortableFix.cmd.
    import shutil

    src = tmp_path / "zip_src" / "PortableFix"
    (src / "App").mkdir(parents=True)
    (src / "App" / "PortableFix.exe").write_bytes(exe_marker)
    (src / "Modules").mkdir()
    (src / "Modules" / "mod.dll").write_bytes(b"m")
    (src / "Vendor").mkdir()
    (src / "Vendor" / "vendor.dll").write_bytes(b"v")
    (src / "Data").mkdir()
    (src / "Data" / "settings.json").write_text("{}")
    (src / "PortableFix.cmd").write_text("@echo off\n")
    zip_path = tmp_path / "PortableFix-update.zip"
    shutil.make_archive(str(zip_path.with_suffix("")), "zip", root_dir=src.parent)
    return zip_path


def _make_old_install(install_dir: Path) -> None:
    (install_dir / "App").mkdir(parents=True)
    (install_dir / "App" / "PortableFix.exe").write_bytes(b"old-exe")
    (install_dir / "Modules").mkdir()
    (install_dir / "Modules" / "mod.dll").write_bytes(b"old-m")
    (install_dir / "Vendor").mkdir()
    (install_dir / "Vendor" / "vendor.dll").write_bytes(b"old-v")
    (install_dir / "Data").mkdir()
    (install_dir / "Data" / "settings.json").write_text('{"k": "v"}')
    (install_dir / "PortableFix.cmd").write_text("@echo off\n")


def _run_script(script_text: str, tmp_path: Path, stubs: str = "") -> None:
    import subprocess

    # Start-Process is stubbed to only record the relaunch: the fake
    # "exe" is a few bytes of text, and actually launching it is neither
    # possible nor what these tests are about. Start-Sleep is stubbed out
    # so the wait/retry loops don't make every run take seconds.
    relaunch_log = tmp_path / "relaunched.txt"
    default_stubs = (
        "function Start-Process { [CmdletBinding()] param($FilePath, $WindowStyle) "
        f"Add-Content -LiteralPath '{relaunch_log}' -Value $FilePath }}\n"
        "function Start-Sleep { [CmdletBinding()] param($Milliseconds, $Seconds) }\n"
    )
    script_path = tmp_path / "swap.ps1"
    script_path.write_text(default_stubs + stubs + script_text, encoding="utf-8-sig")
    subprocess.run(
        [_powershell_or_skip(), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(script_path)],
        capture_output=True, text=True, timeout=120,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )


def test_swap_script_actually_replaces_old_files_end_to_end(tmp_path):
    # None of the other build_swap_script tests ever RUN the script - they
    # only assert substrings in the generated text or that it parses. This
    # actually executes it against a real fake install, proving the swap
    # logic itself (not just its syntax) works.
    install_dir = tmp_path / "install"
    _make_old_install(install_dir)
    zip_path = _make_release_zip(tmp_path)
    # current_pid must already be a dead pid so the wait loop exits immediately.
    dead_pid = 999_999
    script = build_swap_script(current_pid=dead_pid, install_dir=install_dir, zip_path=zip_path)

    _run_script(script, tmp_path)

    assert (install_dir / "App" / "PortableFix.exe").read_bytes() == b"new-exe"
    assert (install_dir / "Modules" / "mod.dll").exists()
    assert (install_dir / "Vendor" / "vendor.dll").exists()
    assert not (install_dir / "App.old").exists()


@pytest.mark.skipif(sys.platform != "win32", reason="relies on Windows mandatory file locking")
def test_swap_script_aborts_without_false_positive_when_old_app_dir_is_locked(tmp_path):
    # Reproduces the actual bug: if moving the old App folder out of the way
    # fails (here simulated by holding a file open inside it, standing in
    # for AV scanning / a lingering handle from the just-exited process),
    # Move-Item into the still-occupied destination NESTS the new files
    # instead of replacing them - so the old exe must be left in place
    # rather than the script reporting a false "verified OK".
    install_dir = tmp_path / "install"
    _make_old_install(install_dir)
    zip_path = _make_release_zip(tmp_path)
    dead_pid = 999_998
    script = build_swap_script(current_pid=dead_pid, install_dir=install_dir, zip_path=zip_path)

    locked_file = install_dir / "App" / "PortableFix.exe"
    with open(locked_file, "r+b"):
        _run_script(script, tmp_path)

    # Old exe must still be exactly the old one - not silently "verified"
    # against new content nested one level deeper.
    assert locked_file.read_bytes() == b"old-exe"


def test_build_swap_script_restarts_via_portablefix_exe_directly():
    # Relaunch must target the GUI exe directly, not PortableFix.cmd - a
    # Start-Process on the .cmd goes through cmd.exe, whose console window
    # isn't reliably hidden on Windows 11 with Windows Terminal as the
    # default terminal app (see build_swap_script's relaunch comment).
    script = build_swap_script(
        current_pid=1,
        install_dir=PureWindowsPath(r"C:\App"),
        zip_path=PureWindowsPath(r"C:\Temp\PortableFix-update.zip"),
    )
    assert "Start-Process -FilePath 'C:\\App\\App\\PortableFix.exe'" in script
    assert "Start-Process -FilePath 'C:\\App\\PortableFix.cmd'" not in script



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

    def fake_download_update(info, dest_dir, on_progress=None):
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


def _parse_errors(script: str) -> list[str]:
    import os
    import subprocess

    env = os.environ.copy()
    env["PFCMD"] = script
    result = subprocess.run(
        [_powershell_or_skip(), "-NoProfile", "-NonInteractive", "-Command",
         "$e = $null; [System.Management.Automation.Language.Parser]::ParseInput($env:PFCMD, [ref]$null, [ref]$e) | Out-Null; "
         "if ($e.Count) { $e | ForEach-Object { Write-Output ('ERR ' + $_.Message) } } else { Write-Output PARSE_OK }"],
        env=env, capture_output=True, text=True, timeout=60,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert "PARSE_OK" in result.stdout or "ERR" in result.stdout, result.stderr
    return [line for line in result.stdout.splitlines() if line.startswith("ERR")]


@pytest.mark.parametrize(
    "install_dir",
    [r"C:\USB Fixer", r"C:\Users\Jane$Doe\Tools [2024]\O'Brien", r"D:\Ondřej Čučko\PortableFix"],
)
def test_build_swap_script_parses_cleanly_with_the_powershell_parser(install_dir):
    script = build_swap_script(
        current_pid=4242,
        install_dir=PureWindowsPath(install_dir),
        zip_path=PureWindowsPath(r"C:\Temp\PortableFixUpdate_x\PortableFix-update.zip"),
    )
    assert _parse_errors(script) == []


def test_build_swap_script_stages_the_update_on_the_install_volume():
    # Staging under %TEMP% made the new-App move a slow cross-volume copy
    # onto the USB stick while no App\ existed at all - the window in which
    # a pulled stick left no PortableFix.exe behind.
    script = build_swap_script(
        current_pid=1,
        install_dir=PureWindowsPath(r"E:\PortableFix"),
        zip_path=PureWindowsPath(r"C:\Temp\PortableFixUpdate_x\PortableFix-update.zip"),
    )
    assert "-DestinationPath 'E:\\PortableFix\\_update_stage'" in script
    assert "C:\\Temp\\PortableFixUpdate_x\\PortableFixUpdateStage" not in script


def test_build_swap_script_uses_literal_paths_only():
    # -Path treats [ and ] as wildcards: Test-Path 'X:\Tools [2024]\App'
    # reports False for a folder that exists, and the swap then nested the
    # new App inside the old one.
    script = build_swap_script(
        current_pid=1,
        install_dir=PureWindowsPath(r"E:\Tools [2024]"),
        zip_path=PureWindowsPath(r"C:\Temp\PortableFix-update.zip"),
    )
    for cmdlet in ("Test-Path", "Move-Item", "Remove-Item", "Copy-Item", "Get-ChildItem", "Expand-Archive", "Resolve-Path"):
        assert f"{cmdlet} -Path " not in script, cmdlet
        assert f"{cmdlet} '" not in script, cmdlet


def _make_swap_zip(
    tmp_path: Path, *, with_exe: bool = True, with_vendor: bool = True, empty_vendor: bool = False, sums: bytes = b"new-sums"
) -> Path:
    import shutil

    src = tmp_path / "zip_src2" / "PortableFix"
    (src / "App").mkdir(parents=True)
    if with_exe:
        (src / "App" / "PortableFix.exe").write_bytes(b"new-exe")
    (src / "Modules").mkdir()
    (src / "Modules" / "mod.yaml").write_bytes(b"new-m")
    if with_vendor:
        (src / "Vendor").mkdir()
        if not empty_vendor:
            (src / "Vendor" / "vendor.dll").write_bytes(b"new-v")
    (src / "Data").mkdir()
    (src / "Data" / "settings.json").write_text("{}")
    (src / "Data" / "SHA256SUMS").write_bytes(sums)
    (src / "PortableFix.cmd").write_text("@echo off\n")
    zip_path = tmp_path / "dl" / "PortableFix-update.zip"
    zip_path.parent.mkdir()
    shutil.make_archive(str(zip_path.with_suffix("")), "zip", root_dir=src.parent)
    return zip_path


def _make_swap_install(install_dir: Path) -> None:
    _make_old_install(install_dir)
    (install_dir / "Data" / "SHA256SUMS").write_bytes(b"old-sums")


def _status(install_dir: Path) -> str:
    status = (install_dir / "Data" / "update_status.txt").read_text(encoding="utf-8-sig").strip()
    if status != updater_module.UPDATE_STATUS_OK:
        # Printed so a failing assert shows the swap script's own log in the
        # captured output - the script swallows errors by design, so this log
        # is the only evidence of *why* (e.g. on a CI runner's PowerShell 5.1).
        for parent in install_dir.parents:
            for log in sorted((parent / "PortableFixUpdate").glob("update_log_*.txt")):
                print(f"--- {log}\n{log.read_text(encoding='utf-8', errors='replace')}")
    return status


@pytest.fixture
def _swap_temp(tmp_path, monkeypatch):
    # Keep the script's log under tmp_path instead of the real %TEMP%.
    monkeypatch.setattr(updater_module.tempfile, "gettempdir", lambda: str(tmp_path / "temp"))
    return tmp_path


def test_swap_script_end_to_end_on_bracketed_path_installs_manifest_and_reports_ok(_swap_temp):
    tmp_path = _swap_temp
    install_dir = tmp_path / "Tools [2024]" / "PF"
    _make_swap_install(install_dir)
    zip_path = _make_swap_zip(tmp_path)

    _run_script(build_swap_script(current_pid=999_997, install_dir=install_dir, zip_path=zip_path), tmp_path)

    assert (install_dir / "App" / "PortableFix.exe").read_bytes() == b"new-exe"
    assert (install_dir / "Modules" / "mod.yaml").read_bytes() == b"new-m"
    assert (install_dir / "Data" / "SHA256SUMS").read_bytes() == b"new-sums"
    assert (install_dir / "Data" / "settings.json").read_text() == '{"k": "v"}'
    assert _status(install_dir) == updater_module.UPDATE_STATUS_OK
    for leftover in ("App.old", "Modules.old", "Vendor.old", "_update_stage"):
        assert not (install_dir / leftover).exists(), leftover
    assert (tmp_path / "relaunched.txt").exists()


def test_swap_script_leaves_install_untouched_when_extracted_update_is_incomplete(_swap_temp):
    # An empty $stagedRoot used to turn "$stagedRoot\App" into "\App" after
    # the live App\ had already been moved away.
    tmp_path = _swap_temp
    install_dir = tmp_path / "install"
    _make_swap_install(install_dir)
    zip_path = _make_swap_zip(tmp_path, with_exe=False)

    _run_script(build_swap_script(current_pid=999_996, install_dir=install_dir, zip_path=zip_path), tmp_path)

    assert (install_dir / "App" / "PortableFix.exe").read_bytes() == b"old-exe"
    assert (install_dir / "Modules" / "mod.dll").read_bytes() == b"old-m"
    assert not (install_dir / "App.old").exists()
    assert _status(install_dir) == updater_module.UPDATE_STATUS_ABORTED


def test_swap_script_rollback_keeps_the_old_integrity_manifest(_swap_temp):
    # Data\ used to be copied before verification, so a rollback put the
    # OLD exe next to the NEW SHA256SUMS - a permanent false tamper warning.
    tmp_path = _swap_temp
    install_dir = tmp_path / "install"
    _make_swap_install(install_dir)
    # An empty Vendor\ passes the pre-swap package check but fails the
    # post-swap verification, forcing the rollback path.
    zip_path = _make_swap_zip(tmp_path, empty_vendor=True)

    _run_script(build_swap_script(current_pid=999_995, install_dir=install_dir, zip_path=zip_path), tmp_path)

    assert (install_dir / "App" / "PortableFix.exe").read_bytes() == b"old-exe"
    assert (install_dir / "Vendor" / "vendor.dll").read_bytes() == b"old-v"
    assert (install_dir / "Data" / "SHA256SUMS").read_bytes() == b"old-sums"
    assert _status(install_dir) == updater_module.UPDATE_STATUS_ROLLED_BACK


def test_swap_script_puts_back_partially_backed_up_folders_when_app_is_locked(_swap_temp):
    # App\ can't be moved (locked exe), but Modules\ and Vendor\ could -
    # they used to stay stranded as *.old and the relaunched old app came
    # up with no modules at all.
    tmp_path = _swap_temp
    install_dir = tmp_path / "install"
    _make_swap_install(install_dir)
    zip_path = _make_swap_zip(tmp_path)
    locked = install_dir / "App"
    stub = (
        "function Move-Item { [CmdletBinding()] param($LiteralPath, $Destination, [switch]$Force) "
        f"if ($LiteralPath -eq '{locked}') {{ return }} "
        "Microsoft.PowerShell.Management\\Move-Item -LiteralPath $LiteralPath -Destination $Destination -Force }\n"
    )

    _run_script(build_swap_script(current_pid=999_994, install_dir=install_dir, zip_path=zip_path), tmp_path, stubs=stub)

    assert (install_dir / "App" / "PortableFix.exe").read_bytes() == b"old-exe"
    assert (install_dir / "Modules" / "mod.dll").read_bytes() == b"old-m"
    assert (install_dir / "Vendor" / "vendor.dll").read_bytes() == b"old-v"
    assert not (install_dir / "Modules.old").exists()
    assert _status(install_dir) == updater_module.UPDATE_STATUS_ABORTED


def test_swap_script_recovers_leftovers_of_an_interrupted_swap_before_swapping(_swap_temp):
    # State after a USB stick was pulled mid-swap: App\ only exists as
    # App.old, plus a stale Modules.old next to a live Modules\ (which
    # would make the next Move-Item nest into it).
    tmp_path = _swap_temp
    install_dir = tmp_path / "install"
    _make_swap_install(install_dir)
    (install_dir / "App").rename(install_dir / "App.old")
    (install_dir / "Modules.old").mkdir()
    (install_dir / "Modules.old" / "stale.yaml").write_bytes(b"stale")
    zip_path = _make_swap_zip(tmp_path)

    _run_script(build_swap_script(current_pid=999_993, install_dir=install_dir, zip_path=zip_path), tmp_path)

    assert (install_dir / "App" / "PortableFix.exe").read_bytes() == b"new-exe"
    assert (install_dir / "Modules" / "mod.yaml").read_bytes() == b"new-m"
    assert not (install_dir / "App.old").exists()
    assert not (install_dir / "Modules.old").exists()
    assert _status(install_dir) == updater_module.UPDATE_STATUS_OK


def test_swap_script_reports_stale_manifest_when_sums_copy_cannot_be_verified(_swap_temp):
    tmp_path = _swap_temp
    install_dir = tmp_path / "install"
    _make_swap_install(install_dir)
    zip_path = _make_swap_zip(tmp_path)
    # A directory where the manifest file should be can never be read back
    # as the expected bytes - the same outcome as a copy that AV keeps
    # locking, without stubbing the hash function the script defines itself.
    installed_sums = install_dir / "Data" / "SHA256SUMS"
    installed_sums.unlink()
    installed_sums.mkdir()

    _run_script(build_swap_script(current_pid=999_992, install_dir=install_dir, zip_path=zip_path), tmp_path)

    assert (install_dir / "App" / "PortableFix.exe").read_bytes() == b"new-exe"
    assert _status(install_dir) == updater_module.UPDATE_STATUS_OK_SUMS_STALE


def test_swap_script_records_abort_when_old_process_never_exits(_swap_temp):
    import os

    tmp_path = _swap_temp
    install_dir = tmp_path / "install"
    _make_swap_install(install_dir)
    zip_path = _make_swap_zip(tmp_path)

    # This test process is alive for the whole run, so the pid wait gives up.
    _run_script(build_swap_script(current_pid=os.getpid(), install_dir=install_dir, zip_path=zip_path), tmp_path)

    assert (install_dir / "App" / "PortableFix.exe").read_bytes() == b"old-exe"
    assert _status(install_dir) == updater_module.UPDATE_STATUS_ABORTED
    assert not (tmp_path / "relaunched.txt").exists()


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
    ],
)
def test_update_status_message_key(status, restored, expected):
    assert updater_module.update_status_message_key(status, restored) == expected


def test_apply_update_returns_false_when_swap_script_exits_immediately(tmp_path, monkeypatch):
    # A GPO-enforced execution policy / AppLocker makes powershell.exe start
    # and exit at once - Popen succeeded, so the app used to quit into an
    # update that never ran.
    monkeypatch.setattr(updater_module.tempfile, "gettempdir", lambda: str(tmp_path))
    process = MagicMock()
    process.wait.return_value = 1
    monkeypatch.setattr(updater_module.subprocess, "Popen", lambda *a, **k: process)
    install_dir = tmp_path / "install"
    install_dir.mkdir()

    assert apply_update(zip_path=tmp_path / "PortableFix-update.zip", install_dir=install_dir) is False


def test_apply_update_launches_powershell_by_resolved_path_non_interactively(tmp_path, monkeypatch):
    monkeypatch.setattr(updater_module.tempfile, "gettempdir", lambda: str(tmp_path))
    ps_path = r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
    monkeypatch.setattr(updater_module, "powershell_executable", lambda: ps_path)
    calls = []
    monkeypatch.setattr(updater_module.subprocess, "Popen", lambda argv, **k: calls.append(argv) or _running_process())
    install_dir = tmp_path / "install"
    install_dir.mkdir()

    assert apply_update(zip_path=tmp_path / "PortableFix-update.zip", install_dir=install_dir) is True
    assert calls[0][0] == ps_path
    assert "-NonInteractive" in calls[0]


def test_apply_update_returns_false_when_no_temp_directory_is_usable(tmp_path, monkeypatch):
    def no_temp(*a, **k):
        raise FileNotFoundError("No usable temporary directory found")

    monkeypatch.setattr(updater_module.tempfile, "gettempdir", no_temp)
    monkeypatch.setattr(updater_module.tempfile, "mkstemp", no_temp)
    popen_calls = []
    monkeypatch.setattr(updater_module.subprocess, "Popen", lambda *a, **k: popen_calls.append(1))
    install_dir = tmp_path / "install"
    install_dir.mkdir()

    assert apply_update(zip_path=tmp_path / "PortableFix-update.zip", install_dir=install_dir) is False
    assert popen_calls == []


def test_launcher_cmd_restores_app_folder_stranded_as_app_old():
    # Without App\PortableFix.exe there is no Python code left to run any
    # recovery - the launcher is the only thing that can put App.old back.
    cmd = (Path(__file__).resolve().parent.parent / "PortableFix.cmd").read_text(encoding="utf-8")
    restore = 'if not exist "%~dp0App\\" if exist "%~dp0App.old\\PortableFix.exe" move "%~dp0App.old" "%~dp0App"'
    assert restore in cmd
    assert cmd.index(restore) < cmd.rindex('"%~dp0App\\PortableFix.exe"')


def test_swap_script_rejects_zip_slip_entry_and_still_relaunches(_swap_temp):
    import zipfile

    tmp_path = _swap_temp
    install_dir = tmp_path / "install"
    _make_swap_install(install_dir)
    zip_path = _make_swap_zip(tmp_path)
    # A crafted release whose entry climbs out of the staging folder.
    with zipfile.ZipFile(zip_path, "a") as zf:
        zf.writestr("../../escaped.txt", b"pwned")

    _run_script(build_swap_script(current_pid=999_980, install_dir=install_dir, zip_path=zip_path), tmp_path)

    assert not (install_dir.parent / "escaped.txt").exists()
    assert not (install_dir / "escaped.txt").exists()
    assert (install_dir / "App" / "PortableFix.exe").read_bytes() == b"old-exe"
    assert _status(install_dir) == updater_module.UPDATE_STATUS_ABORTED
    # Previously the guard exited without relaunching - the user was left
    # with no app running at all.
    assert (tmp_path / "relaunched.txt").exists()


def test_swap_script_aborts_cleanly_when_package_has_no_vendor(_swap_temp):
    tmp_path = _swap_temp
    install_dir = tmp_path / "install"
    _make_swap_install(install_dir)
    zip_path = _make_swap_zip(tmp_path, with_vendor=False)

    _run_script(build_swap_script(current_pid=999_979, install_dir=install_dir, zip_path=zip_path), tmp_path)

    assert (install_dir / "App" / "PortableFix.exe").read_bytes() == b"old-exe"
    assert not (install_dir / "App.old").exists()
    assert _status(install_dir) == updater_module.UPDATE_STATUS_ABORTED
