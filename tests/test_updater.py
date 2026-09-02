import hashlib
import json
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from portablefix.updater import (
    UpdateInfo,
    UpdateVerificationError,
    build_swap_script,
    check_for_update,
    download_update,
    is_newer,
    is_writable,
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


def _release_json(tag="v1.1.0", with_sha=True, exe_name="PortableFix.exe"):
    assets = [{"name": exe_name, "browser_download_url": "https://example.com/PortableFix.exe"}]
    if with_sha:
        assets.append({
            "name": "PortableFix.exe.sha256",
            "browser_download_url": "https://example.com/PortableFix.exe.sha256",
        })
    return json.dumps({"tag_name": tag, "assets": assets, "body": "release notes"}).encode("utf-8")


def _mock_response(body: bytes):
    mock_resp = MagicMock()
    mock_resp.read.return_value = body
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
        download_url="https://example.com/PortableFix.exe",
        sha256_url="https://example.com/PortableFix.exe.sha256",
        notes="release notes",
    )


def test_check_for_update_returns_none_on_network_error():
    with patch("portablefix.updater.urllib.request.urlopen", side_effect=urllib.error.URLError("offline")):
        assert check_for_update("1.0.0") is None


def test_check_for_update_returns_none_on_malformed_json():
    with patch("portablefix.updater.urllib.request.urlopen", return_value=_mock_response(b"not json")):
        assert check_for_update("1.0.0") is None


def test_check_for_update_returns_none_when_no_exe_asset_present():
    body = _release_json(tag="v1.1.0", exe_name="SomethingElse.exe")
    with patch("portablefix.updater.urllib.request.urlopen", return_value=_mock_response(body)):
        assert check_for_update("1.0.0") is None


def test_download_update_succeeds_when_hash_matches(tmp_path):
    content = b"fake-exe-content"
    expected_hash = hashlib.sha256(content).hexdigest()
    info = UpdateInfo(
        version="1.1.0",
        download_url="https://example.com/PortableFix.exe",
        sha256_url="https://example.com/PortableFix.exe.sha256",
        notes="",
    )

    def fake_urlretrieve(url, path):
        Path(path).write_bytes(content)

    with patch("portablefix.updater.urllib.request.urlretrieve", side_effect=fake_urlretrieve):
        with patch("portablefix.updater.urllib.request.urlopen", return_value=_mock_response(expected_hash.encode("utf-8"))):
            result_path = download_update(info, tmp_path / "dest")

    assert result_path.read_bytes() == content


def test_download_update_raises_and_cleans_up_on_hash_mismatch(tmp_path):
    info = UpdateInfo(
        version="1.1.0",
        download_url="https://example.com/PortableFix.exe",
        sha256_url="https://example.com/PortableFix.exe.sha256",
        notes="",
    )
    wrong_hash = "0" * 64

    def fake_urlretrieve(url, path):
        Path(path).write_bytes(b"fake-exe-content")

    dest = tmp_path / "dest"
    with patch("portablefix.updater.urllib.request.urlretrieve", side_effect=fake_urlretrieve):
        with patch("portablefix.updater.urllib.request.urlopen", return_value=_mock_response(wrong_hash.encode("utf-8"))):
            with pytest.raises(UpdateVerificationError):
                download_update(info, dest)

    assert not (dest / "PortableFix.new.exe").exists()


def test_download_update_skips_verification_when_no_sha256_asset(tmp_path):
    info = UpdateInfo(
        version="1.1.0", download_url="https://example.com/PortableFix.exe", sha256_url=None, notes="",
    )

    def fake_urlretrieve(url, path):
        Path(path).write_bytes(b"anything")

    with patch("portablefix.updater.urllib.request.urlretrieve", side_effect=fake_urlretrieve):
        result_path = download_update(info, tmp_path / "dest")

    assert result_path.read_bytes() == b"anything"


def test_is_writable_true_for_writable_directory(tmp_path):
    assert is_writable(tmp_path) is True


def test_is_writable_false_for_missing_directory(tmp_path):
    assert is_writable(tmp_path / "does_not_exist") is False


def test_build_swap_script_parses_as_valid_powershell():
    import os
    import subprocess

    script = build_swap_script(
        current_pid=12345,
        old_exe=Path(r"C:\Users\test\USB Fixer\App\PortableFix.exe"),
        new_exe=Path(r"C:\Users\test\AppData\Local\Temp\PortableFix.new.exe"),
        sums_path=Path(r"C:\Users\test\USB Fixer\Data\SHA256SUMS"),
    )
    env = os.environ.copy()
    env["PFCMD"] = script
    result = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command",
         "[scriptblock]::Create($env:PFCMD) | Out-Null; Write-Output OK"],
        env=env, capture_output=True, text=True,
    )
    assert "OK" in result.stdout, result.stderr


def test_build_swap_script_quotes_paths_with_spaces():
    script = build_swap_script(
        current_pid=1,
        old_exe=Path(r"C:\Users\test\USB Fixer\App\PortableFix.exe"),
        new_exe=Path(r"C:\Temp\PortableFix.new.exe"),
        sums_path=Path(r"C:\Users\test\USB Fixer\Data\SHA256SUMS"),
    )
    assert '"C:\\Users\\test\\USB Fixer\\App\\PortableFix.exe"' in script


def test_build_swap_script_contains_pid_wait_loop():
    script = build_swap_script(
        current_pid=54321,
        old_exe=Path(r"C:\App\PortableFix.exe"),
        new_exe=Path(r"C:\Temp\PortableFix.new.exe"),
        sums_path=Path(r"C:\App\SHA256SUMS"),
    )
    assert "54321" in script
    assert "Get-Process" in script
