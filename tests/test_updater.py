import hashlib
import json
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from portablefix import updater as updater_module
from portablefix.updater import (
    UpdateInfo,
    UpdateVerificationError,
    apply_update,
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


def _release_json(tag="v1.1.0", with_sha=True, zip_name="PortableFix-Portable.zip"):
    assets = [{"name": zip_name, "browser_download_url": "https://example.com/PortableFix-Portable.zip"}]
    if with_sha:
        assets.append({
            "name": "PortableFix-Portable.zip.sha256",
            "browser_download_url": "https://example.com/PortableFix-Portable.zip.sha256",
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
        package_url="https://example.com/PortableFix-Portable.zip",
        sha256_url="https://example.com/PortableFix-Portable.zip.sha256",
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


def test_download_update_succeeds_when_hash_matches(tmp_path):
    content = b"fake-zip-content"
    expected_hash = hashlib.sha256(content).hexdigest()
    info = UpdateInfo(
        version="1.1.0",
        package_url="https://example.com/PortableFix-Portable.zip",
        sha256_url="https://example.com/PortableFix-Portable.zip.sha256",
        notes="",
    )

    def fake_urlretrieve(url, path):
        Path(path).write_bytes(content)

    with patch("portablefix.updater.urllib.request.urlretrieve", side_effect=fake_urlretrieve):
        with patch("portablefix.updater.urllib.request.urlopen", return_value=_mock_response(expected_hash.encode("utf-8"))):
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

    def fake_urlretrieve(url, path):
        Path(path).write_bytes(b"fake-zip-content")

    dest = tmp_path / "dest"
    with patch("portablefix.updater.urllib.request.urlretrieve", side_effect=fake_urlretrieve):
        with patch("portablefix.updater.urllib.request.urlopen", return_value=_mock_response(wrong_hash.encode("utf-8"))):
            with pytest.raises(UpdateVerificationError):
                download_update(info, dest)

    assert not (dest / "PortableFix-update.zip").exists()


def test_download_update_cleans_up_partial_file_on_urlretrieve_failure(tmp_path):
    info = UpdateInfo(
        version="1.1.0", package_url="https://example.com/PortableFix-Portable.zip", sha256_url=None, notes="",
    )
    dest = tmp_path / "dest"

    def fake_urlretrieve(url, path):
        Path(path).write_bytes(b"partial")
        raise ConnectionError("connection dropped")

    with patch("portablefix.updater.urllib.request.urlretrieve", side_effect=fake_urlretrieve):
        with pytest.raises(ConnectionError):
            download_update(info, dest)

    assert not (dest / "PortableFix-update.zip").exists()


def test_download_update_skips_verification_when_no_sha256_asset(tmp_path):
    info = UpdateInfo(
        version="1.1.0", package_url="https://example.com/PortableFix-Portable.zip", sha256_url=None, notes="",
    )

    def fake_urlretrieve(url, path):
        Path(path).write_bytes(b"anything")

    with patch("portablefix.updater.urllib.request.urlretrieve", side_effect=fake_urlretrieve):
        result_path = download_update(info, tmp_path / "dest")

    assert result_path.read_bytes() == b"anything"


def test_apply_update_writes_ps1_script_with_utf8_bom(tmp_path, monkeypatch):
    monkeypatch.setattr(updater_module.tempfile, "gettempdir", lambda: str(tmp_path))
    monkeypatch.setattr(updater_module.subprocess, "Popen", lambda *a, **k: MagicMock())
    install_dir = tmp_path / "install"
    install_dir.mkdir()

    apply_update(zip_path=tmp_path / "PortableFix-update.zip", install_dir=install_dir)

    scripts = list(tmp_path.glob("portablefix_update_*.ps1"))
    assert len(scripts) == 1
    assert scripts[0].read_bytes()[:3] == b"\xef\xbb\xbf"


def test_apply_update_returns_true_on_success(tmp_path, monkeypatch):
    monkeypatch.setattr(updater_module.tempfile, "gettempdir", lambda: str(tmp_path))
    monkeypatch.setattr(updater_module.subprocess, "Popen", lambda *a, **k: MagicMock())
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


def test_build_swap_script_parses_as_valid_powershell():
    import os
    import subprocess

    script = build_swap_script(
        current_pid=12345,
        install_dir=Path(r"C:\Users\test\USB Fixer"),
        zip_path=Path(r"C:\Users\test\AppData\Local\Temp\PortableFix-update.zip"),
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
        install_dir=Path(r"C:\Users\test\USB Fixer"),
        zip_path=Path(r"C:\Temp\PortableFix-update.zip"),
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

    install_dir = Path(r"C:\Users\Jane$Doe\USB Fixer")
    script = build_swap_script(
        current_pid=1,
        install_dir=install_dir,
        zip_path=Path(r"C:\Temp\PortableFix-update.zip"),
    )
    assert "'C:\\Users\\Jane$Doe\\USB Fixer\\App'" in script
    assert '"C:\\Users\\Jane$Doe' not in script

    env = os.environ.copy()
    env["PFCMD"] = script
    result = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command",
         "[scriptblock]::Create($env:PFCMD) | Out-Null; Write-Output OK"],
        env=env, capture_output=True, text=True,
    )
    assert "OK" in result.stdout, result.stderr


def test_build_swap_script_escapes_embedded_single_quote_in_path():
    install_dir = Path(r"C:\Users\O'Brien\USB Fixer")
    script = build_swap_script(
        current_pid=1,
        install_dir=install_dir,
        zip_path=Path(r"C:\Temp\PortableFix-update.zip"),
    )
    assert "O''Brien" in script


def test_build_swap_script_restores_backup_folders_if_swap_fails_to_verify():
    script = build_swap_script(
        current_pid=1,
        install_dir=Path(r"C:\App"),
        zip_path=Path(r"C:\Temp\PortableFix-update.zip"),
    )
    assert "if (Test-Path 'C:\\App\\App\\PortableFix.exe') {" in script
    assert "Move-Item -Path 'C:\\App\\App.old' -Destination 'C:\\App\\App' -Force" in script
    assert "Move-Item -Path 'C:\\App\\Modules.old' -Destination 'C:\\App\\Modules' -Force" in script


def test_build_swap_script_preserves_settings_json_across_the_swap():
    script = build_swap_script(
        current_pid=1,
        install_dir=Path(r"C:\App"),
        zip_path=Path(r"C:\Temp\PortableFix-update.zip"),
    )
    assert "settings.json" in script
    assert "settings.json.bak" in script


def test_build_swap_script_expands_the_downloaded_zip():
    script = build_swap_script(
        current_pid=1,
        install_dir=Path(r"C:\App"),
        zip_path=Path(r"C:\Temp\PortableFix-update.zip"),
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
    install_dir = Path(r"C:\Users\Ondřej Čučko\USB Fixer")
    script = build_swap_script(
        current_pid=1,
        install_dir=install_dir,
        zip_path=Path(r"C:\Temp\PortableFix-update.zip"),
    )
    assert str(install_dir) in script


def test_build_swap_script_contains_pid_wait_loop():
    script = build_swap_script(
        current_pid=54321,
        install_dir=Path(r"C:\App"),
        zip_path=Path(r"C:\Temp\PortableFix-update.zip"),
    )
    assert "54321" in script
    assert "Get-Process" in script


def test_build_swap_script_restarts_via_portablefix_cmd():
    script = build_swap_script(
        current_pid=1,
        install_dir=Path(r"C:\App"),
        zip_path=Path(r"C:\Temp\PortableFix-update.zip"),
    )
    assert "Start-Process -FilePath 'C:\\App\\PortableFix.cmd'" in script


from portablefix.updater import UpdateCheckRunner, UpdateDownloadRunner


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


def test_update_download_runner_emits_error_on_failure(qtbot, tmp_path):
    info = UpdateInfo(version="1.1.0", package_url="https://x", sha256_url=None, notes="")
    with patch("portablefix.updater.download_update", side_effect=UpdateVerificationError("bad hash")):
        runner = UpdateDownloadRunner(info, tmp_path)
        with qtbot.waitSignal(runner.download_finished, timeout=2000) as blocker:
            runner.start()
    assert blocker.args == [None, "bad hash"]
