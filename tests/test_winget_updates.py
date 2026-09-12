import subprocess
from unittest.mock import MagicMock, patch

from portablefix.winget_updates import (
    OutdatedPackage,
    WingetUpdateRunner,
    export_package_list,
    import_package_ids,
    parse_winget_upgrade_table,
    update_package,
)

_SAMPLE_TABLE = (
    "Name                    Id                                Version      Available    Source\n"
    "------------------------------------------------------------------------------------------\n"
    "AnyDesk                 AnyDeskSoftwareGmbH.AnyDesk       9.6.11       9.7.15       winget\n"
    "CPUID CPU-Z             CPUID.CPU-Z                       2.20         3.01         winget\n"
    "Docker Desktop          Docker.DockerDesktop              4.88.1       4.90.0       winget\n"
    "4 upgrades available.\n"
)


def test_parse_winget_upgrade_table_extracts_rows():
    packages = parse_winget_upgrade_table(_SAMPLE_TABLE)
    assert len(packages) == 3
    first = packages[0]
    assert first.name == "AnyDesk"
    assert first.id == "AnyDeskSoftwareGmbH.AnyDesk"
    assert first.installed_version == "9.6.11"
    assert first.available_version == "9.7.15"
    assert first.source == "winget"


def test_parse_winget_upgrade_table_stops_at_summary_line():
    packages = parse_winget_upgrade_table(_SAMPLE_TABLE)
    assert all("upgrades available" not in p.name for p in packages)


def test_parse_winget_upgrade_table_handles_names_with_spaces():
    packages = parse_winget_upgrade_table(_SAMPLE_TABLE)
    names = {p.name for p in packages}
    assert "CPUID CPU-Z" in names
    assert "Docker Desktop" in names


def test_parse_winget_upgrade_table_empty_when_no_updates():
    text = "No installed package found matching input criteria.\n"
    assert parse_winget_upgrade_table(text) == []


def test_parse_winget_upgrade_table_empty_when_no_header_found():
    assert parse_winget_upgrade_table("some unrelated error text\n") == []


def test_update_package_includes_unknown_version_packages():
    # winget refuses to upgrade a package whose currently-installed version
    # it can't determine unless --include-unknown is passed - the scan
    # command already used it, the actual upgrade call must too, or every
    # such package fails with "This package's version number cannot be
    # determined" even though the scan found a real update for it.
    package = OutdatedPackage(name="Some App", id="Some.Package", installed_version="1", available_version="2", source="winget")
    with patch("portablefix.winget_updates.subprocess.Popen") as mock_popen:
        mock_popen.return_value.communicate.return_value = ("", None)
        mock_popen.return_value.returncode = 0
        update_package(package)
    args = mock_popen.call_args[0][0]
    assert "--include-unknown" in args


def _fake_process(returncode: int, output: str) -> MagicMock:
    process = MagicMock()
    process.communicate.return_value = (output, None)
    process.returncode = returncode
    return process


def test_update_package_retries_with_location_when_required():
    package = OutdatedPackage(name="Battle.net", id="Blizzard.BattleNet", installed_version="1", available_version="2", source="winget")
    fail_process = _fake_process(1, "Install location is required by the package but it was not provided")
    ok_process = _fake_process(0, "Successfully installed")
    fake_program = type("P", (), {"name": "Battle.net", "install_location": r"C:\Games\Battle.net"})()
    with patch("portablefix.winget_updates.subprocess.Popen", side_effect=[fail_process, ok_process]) as mock_popen, \
         patch("portablefix.uninstaller.list_installed_programs", return_value=[fake_program]):
        ok, output = update_package(package)
    assert ok is True
    assert output == "Successfully installed"
    second_call_args = mock_popen.call_args_list[1][0][0]
    assert "--location" in second_call_args
    assert r"C:\Games\Battle.net" in second_call_args


def test_update_package_does_not_retry_when_no_install_location_found():
    package = OutdatedPackage(name="Mystery App", id="Mystery.App", installed_version="1", available_version="2", source="winget")
    fail_process = _fake_process(1, "Install location is required by the package but it was not provided")
    with patch("portablefix.winget_updates.subprocess.Popen", return_value=fail_process) as mock_popen, \
         patch("portablefix.uninstaller.list_installed_programs", return_value=[]):
        ok, _ = update_package(package)
    assert ok is False
    assert mock_popen.call_count == 1


def test_run_winget_upgrade_kills_process_tree_on_timeout():
    # winget can spawn a child installer/MSI that outlives winget.exe -
    # killing just the immediate process on timeout leaves that child
    # running and possibly holding a file lock. taskkill /T must be used
    # to reap the whole tree instead of relying on subprocess's own
    # single-process kill.
    package = OutdatedPackage(name="Slow App", id="Slow.App", installed_version="1", available_version="2", source="winget")
    process = MagicMock()
    process.pid = 4242
    process.communicate.side_effect = subprocess.TimeoutExpired(cmd="winget", timeout=1)
    with patch("portablefix.winget_updates.subprocess.Popen", return_value=process), \
         patch("portablefix.winget_updates.subprocess.run") as mock_taskkill:
        ok, output = update_package(package, timeout_sec=1)
    assert ok is False
    assert output == "Update timed out."
    taskkill_args = mock_taskkill.call_args[0][0]
    assert taskkill_args == ["taskkill", "/F", "/T", "/PID", "4242"]


def test_winget_update_runner_request_stop_skips_remaining_packages():
    # A scan/update runner that main_window.py can't tell closeEvent about
    # (they used to live on the panel QWidget, not self) left the app
    # process alive indefinitely on quit, which silently broke the in-app
    # "restart to install update" flow (the swap script gives up waiting
    # for this process to exit and just relaunches the old version).
    # request_stop() lets closeEvent bound the wait to at most one more
    # package's worst case instead of the whole remaining batch.
    packages = [
        OutdatedPackage(name="A", id="A.A", installed_version="1", available_version="2", source="winget"),
        OutdatedPackage(name="B", id="B.B", installed_version="1", available_version="2", source="winget"),
    ]
    runner = WingetUpdateRunner(packages)
    runner.request_stop()
    with patch("portablefix.winget_updates.update_package") as mock_update:
        runner.run()
    mock_update.assert_not_called()


def test_parse_winget_upgrade_table_skips_blank_rows_and_dashes():
    text = (
        "Name    Id             Version   Available   Source\n"
        "-----------------------------------------------------\n"
        "\n"
    )
    assert parse_winget_upgrade_table(text) == []


def test_export_then_import_package_list_round_trip(tmp_path):
    packages = [
        OutdatedPackage(name="AnyDesk", id="AnyDeskSoftwareGmbH.AnyDesk", installed_version="1", available_version="2", source="winget"),
        OutdatedPackage(name="CPUID CPU-Z", id="CPUID.CPU-Z", installed_version="1", available_version="2", source="winget"),
    ]
    path = tmp_path / "packages.json"
    export_package_list(packages, path)
    imported_ids = import_package_ids(path)
    assert imported_ids == {"AnyDeskSoftwareGmbH.AnyDesk", "CPUID.CPU-Z"}


def test_import_package_ids_ignores_malformed_entries(tmp_path):
    path = tmp_path / "packages.json"
    path.write_text('[{"id": "Good.Id"}, "not a dict", {"name": "no id"}]', encoding="utf-8")
    assert import_package_ids(path) == {"Good.Id"}
