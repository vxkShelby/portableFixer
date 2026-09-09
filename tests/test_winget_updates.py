from unittest.mock import patch

from portablefix.winget_updates import (
    OutdatedPackage,
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
    with patch("portablefix.winget_updates.subprocess.run") as mock_run:
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = ""
        mock_run.return_value.stderr = ""
        update_package(package)
    args = mock_run.call_args[0][0]
    assert "--include-unknown" in args


def test_update_package_retries_with_location_when_required():
    package = OutdatedPackage(name="Battle.net", id="Blizzard.BattleNet", installed_version="1", available_version="2", source="winget")
    fail_result = type("R", (), {"returncode": 1, "stdout": "Install location is required by the package but it was not provided", "stderr": ""})()
    ok_result = type("R", (), {"returncode": 0, "stdout": "Successfully installed", "stderr": ""})()
    fake_program = type("P", (), {"name": "Battle.net", "install_location": r"C:\Games\Battle.net"})()
    with patch("portablefix.winget_updates.subprocess.run", side_effect=[fail_result, ok_result]) as mock_run, \
         patch("portablefix.uninstaller.list_installed_programs", return_value=[fake_program]):
        ok, output = update_package(package)
    assert ok is True
    assert output == "Successfully installed"
    second_call_args = mock_run.call_args_list[1][0][0]
    assert "--location" in second_call_args
    assert r"C:\Games\Battle.net" in second_call_args


def test_update_package_does_not_retry_when_no_install_location_found():
    package = OutdatedPackage(name="Mystery App", id="Mystery.App", installed_version="1", available_version="2", source="winget")
    fail_result = type("R", (), {"returncode": 1, "stdout": "Install location is required by the package but it was not provided", "stderr": ""})()
    with patch("portablefix.winget_updates.subprocess.run", return_value=fail_result) as mock_run, \
         patch("portablefix.uninstaller.list_installed_programs", return_value=[]):
        ok, _ = update_package(package)
    assert ok is False
    assert mock_run.call_count == 1


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
