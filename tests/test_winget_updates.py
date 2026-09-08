from portablefix.winget_updates import parse_winget_upgrade_table

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


def test_parse_winget_upgrade_table_skips_blank_rows_and_dashes():
    text = (
        "Name    Id             Version   Available   Source\n"
        "-----------------------------------------------------\n"
        "\n"
    )
    assert parse_winget_upgrade_table(text) == []
