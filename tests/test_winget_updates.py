import os
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from portablefix import winget_updates
from portablefix.winget_updates import (
    OutdatedPackage,
    WingetScanError,
    WingetScanRunner,
    WingetUpdateRunner,
    export_package_list,
    import_package_ids,
    list_outdated_packages,
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


# --- G16: language-independent parsing ------------------------------------
# The same seven packages as winget prints them on English, German and
# Slovak Windows. Only the labels change - and with them every column
# width. The Slovak "K dispozícii" label has a space in it and is followed
# by a single padding space, the hardest case for a header-driven split.
# Each fixture starts with the "\r"-separated spinner frames winget draws
# while it searches and ends with its localized summary line.
_SPINNER = "   - \r   \\ \r   | \r   / \r"

_FIXTURE_EN = _SPINNER + (
    "Name                     Id                          Version    Available  Source\n"
    "----------------------------------------------------------------------------------\n"
    "AnyDesk                  AnyDeskSoftwareGmbH.AnyDesk 9.6.11     9.7.15     winget\n"
    "CPUID CPU-Z              CPUID.CPU-Z                 2.20       3.01       winget\n"
    "Mozilla Firefox (x64 sk) Mozilla.Firefox.sk          128.0.3    131.0      winget\n"
    "Notepad++ (64-bit x64)   Notepad++.Notepad++         < 8.6.9    8.7        winget\n"
    "PDF Čítač Ďalší          Example.PdfReader           1.0        1.2        winget\n"
    "微信                     Tencent.WeChat              3.9.10     3.9.12     winget\n"
    "Microsoft Teams          XP8BT8DW290MPQ              24193.1805 24243.1309 msstore\n"
    "7 upgrades available.\n"
)

_FIXTURE_DE = _SPINNER + (
    "Name                     ID                          Version    Verfügbar  Quelle\n"
    "----------------------------------------------------------------------------------\n"
    "AnyDesk                  AnyDeskSoftwareGmbH.AnyDesk 9.6.11     9.7.15     winget\n"
    "CPUID CPU-Z              CPUID.CPU-Z                 2.20       3.01       winget\n"
    "Mozilla Firefox (x64 sk) Mozilla.Firefox.sk          128.0.3    131.0      winget\n"
    "Notepad++ (64-bit x64)   Notepad++.Notepad++         < 8.6.9    8.7        winget\n"
    "PDF Čítač Ďalší          Example.PdfReader           1.0        1.2        winget\n"
    "微信                     Tencent.WeChat              3.9.10     3.9.12     winget\n"
    "Microsoft Teams          XP8BT8DW290MPQ              24193.1805 24243.1309 msstore\n"
    "7 Aktualisierungen verfügbar.\n"
)

_FIXTURE_SK = _SPINNER + (
    "Názov                    ID                          Verzia     K dispozícii Zdroj\n"
    "------------------------------------------------------------------------------------\n"
    "AnyDesk                  AnyDeskSoftwareGmbH.AnyDesk 9.6.11     9.7.15       winget\n"
    "CPUID CPU-Z              CPUID.CPU-Z                 2.20       3.01         winget\n"
    "Mozilla Firefox (x64 sk) Mozilla.Firefox.sk          128.0.3    131.0        winget\n"
    "Notepad++ (64-bit x64)   Notepad++.Notepad++         < 8.6.9    8.7          winget\n"
    "PDF Čítač Ďalší          Example.PdfReader           1.0        1.2          winget\n"
    "微信                     Tencent.WeChat              3.9.10     3.9.12       winget\n"
    "Microsoft Teams          XP8BT8DW290MPQ              24193.1805 24243.1309   msstore\n"
    "K dispozícii je 7 inovácií.\n"
)

_EXPECTED = [
    OutdatedPackage("AnyDesk", "AnyDeskSoftwareGmbH.AnyDesk", "9.6.11", "9.7.15", "winget"),
    OutdatedPackage("CPUID CPU-Z", "CPUID.CPU-Z", "2.20", "3.01", "winget"),
    OutdatedPackage("Mozilla Firefox (x64 sk)", "Mozilla.Firefox.sk", "128.0.3", "131.0", "winget"),
    OutdatedPackage("Notepad++ (64-bit x64)", "Notepad++.Notepad++", "< 8.6.9", "8.7", "winget"),
    OutdatedPackage("PDF Čítač Ďalší", "Example.PdfReader", "1.0", "1.2", "winget"),
    OutdatedPackage("微信", "Tencent.WeChat", "3.9.10", "3.9.12", "winget"),
    OutdatedPackage("Microsoft Teams", "XP8BT8DW290MPQ", "24193.1805", "24243.1309", "msstore"),
]


@pytest.mark.parametrize("fixture", [_FIXTURE_EN, _FIXTURE_DE, _FIXTURE_SK], ids=["en", "de", "sk"])
def test_parse_gives_identical_results_in_english_german_and_slovak(fixture):
    # The old parser looked for "Name"/"Id"/"Available" and returned [] on
    # German ("ID", "Verfügbar") and Slovak ("Názov") output - which the
    # panel then showed as "no updates", a false all-clear.
    assert parse_winget_upgrade_table(fixture) == _EXPECTED


def test_parse_does_not_split_a_header_label_that_contains_a_space():
    by_id = {p.id: p for p in parse_winget_upgrade_table(_FIXTURE_SK)}
    assert by_id["AnyDeskSoftwareGmbH.AnyDesk"].available_version == "9.7.15"
    assert by_id["AnyDeskSoftwareGmbH.AnyDesk"].source == "winget"


def test_parse_ignores_ansi_escape_sequences():
    text = "\x1b[2K\x1b[0G" + _FIXTURE_DE.replace("AnyDesk   ", "\x1b[0mAnyDesk   ", 1)
    assert parse_winget_upgrade_table(text) == _EXPECTED


def test_parse_handles_a_table_without_a_source_column():
    text = (
        "Nom        ID            Version Disponible\n"
        "-------------------------------------------\n"
        "Some App   Some.App      1.0     2.0\n"
    )
    assert parse_winget_upgrade_table(text) == [OutdatedPackage("Some App", "Some.App", "1.0", "2.0", "")]


def test_parse_stops_before_the_explicit_targeting_table():
    # winget prints a second table (packages that need explicit targeting)
    # after an explanatory line - its header must never become a package.
    text = _FIXTURE_EN.replace("7 upgrades available.\n", (
        "7 upgrades available.\n"
        "Nasledujúce balíky majú k dispozícii inováciu, ale vyžadujú explicitné zacielenie:\n"
        "Name     Id          Version Available Source\n"
        "---------------------------------------------\n"
        "Pinned   Pinned.App  1.0     2.0       winget\n"
    ))
    assert parse_winget_upgrade_table(text) == _EXPECTED


def test_parse_reports_whether_a_table_was_printed():
    assert winget_updates._parse_upgrade_output("Nenašiel sa žiadny nainštalovaný balík.\n") == (False, [])
    assert winget_updates._parse_upgrade_output(_FIXTURE_SK)[0] is True


# --- G16: scan states ------------------------------------------------------


def _completed(returncode: int, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=["winget"], returncode=returncode, stdout=stdout, stderr=stderr)


@pytest.fixture
def cli_only(monkeypatch):
    """winget found, no Microsoft.WinGet.Client module - the CLI path."""
    monkeypatch.setattr(winget_updates, "find_winget_executable", lambda: r"C:\winget.exe")
    monkeypatch.setattr(winget_updates, "_scan_with_powershell_module", lambda timeout: None)


def _scan_with(monkeypatch, result=None, side_effect=None):
    calls = []

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        if side_effect is not None:
            raise side_effect
        return result

    monkeypatch.setattr(winget_updates.subprocess, "run", fake_run)
    return calls


def test_scan_reports_winget_missing_as_unavailable_not_as_no_updates(monkeypatch):
    monkeypatch.setattr(winget_updates, "find_winget_executable", lambda: None)
    with pytest.raises(WingetScanError) as info:
        list_outdated_packages()
    assert (info.value.kind, info.value.reason) == ("unavailable", "not_found")


def test_scan_reports_a_winget_that_cannot_start_as_unavailable(monkeypatch, cli_only):
    # App Installer's alias present but the package not registered for this
    # account: launching it fails with an OSError.
    _scan_with(monkeypatch, side_effect=OSError(1920, "The file cannot be accessed by the system"))
    with pytest.raises(WingetScanError) as info:
        list_outdated_packages()
    assert (info.value.kind, info.value.reason) == ("unavailable", "cannot_start")


def test_scan_reports_missing_app_installer_dependencies_as_unavailable(monkeypatch, cli_only):
    _scan_with(monkeypatch, _completed(0xC0000135))
    with pytest.raises(WingetScanError) as info:
        list_outdated_packages()
    assert info.value.kind == "unavailable"
    assert info.value.exit_code_hex == "0xC0000135"


@pytest.mark.parametrize("code", [0x8A15000F, 0x8A15000F - 2**32], ids=["unsigned", "signed"])
def test_scan_failure_carries_the_exit_code_in_hex(monkeypatch, cli_only, code):
    _scan_with(monkeypatch, _completed(code, "Zlyhanie pri otváraní zdroja.\n"))
    with pytest.raises(WingetScanError) as info:
        list_outdated_packages()
    assert (info.value.kind, info.value.reason) == ("error", "sources")
    assert info.value.exit_code_hex == "0x8A15000F"
    assert "Zlyhanie pri otváraní zdroja." in info.value.detail


def test_scan_failure_with_an_unknown_code_is_a_plain_failure(monkeypatch, cli_only):
    _scan_with(monkeypatch, _completed(0x8A150001))
    with pytest.raises(WingetScanError) as info:
        list_outdated_packages()
    assert (info.value.kind, info.value.reason, info.value.exit_code_hex) == ("error", "failed", "0x8A150001")


def test_scan_failure_on_an_old_app_installer_says_so(monkeypatch, cli_only):
    _scan_with(monkeypatch, _completed(0x8A150002))
    with pytest.raises(WingetScanError) as info:
        list_outdated_packages()
    assert info.value.reason == "outdated"


def test_scan_failure_keeps_the_rows_winget_still_listed(monkeypatch, cli_only):
    # One source failed (msstore), the other still answered: the rows are
    # real updates and must not disappear behind the error.
    _scan_with(monkeypatch, _completed(0x8A15004B, _FIXTURE_SK))
    with pytest.raises(WingetScanError) as info:
        list_outdated_packages()
    assert info.value.packages == _EXPECTED


def test_scan_timeout_is_a_failure(monkeypatch, cli_only):
    _scan_with(monkeypatch, side_effect=subprocess.TimeoutExpired(cmd="winget", timeout=60))
    with pytest.raises(WingetScanError) as info:
        list_outdated_packages()
    assert (info.value.kind, info.value.reason) == ("error", "timeout")


@pytest.mark.parametrize("code", [0, 0x8A150014, 0x8A15002B])
def test_scan_without_a_table_is_a_real_no_updates(monkeypatch, cli_only, code):
    _scan_with(monkeypatch, _completed(code, "Nenašiel sa žiadny nainštalovaný balík zodpovedajúci kritériám.\n"))
    assert list_outdated_packages() == []


def test_scan_with_a_table_it_cannot_read_is_a_failure_not_no_updates(monkeypatch, cli_only):
    unreadable = (
        "Name Id Version Available Source\n"
        "--------------------------------\n"
        "garbled row without any columns\n"
    )
    _scan_with(monkeypatch, _completed(0, unreadable))
    with pytest.raises(WingetScanError) as info:
        list_outdated_packages()
    assert (info.value.kind, info.value.reason) == ("error", "unparsed")


def test_scan_decodes_winget_output_as_utf8(monkeypatch, cli_only):
    # Decoding as the ANSI code page turned "Názov"/"Čítač" into mojibake
    # of a different length, shifting every column after it.
    calls = _scan_with(monkeypatch, _completed(0, _FIXTURE_SK))
    assert list_outdated_packages() == _EXPECTED
    args, kwargs = calls[0]
    assert args[0] == r"C:\winget.exe"
    assert kwargs["encoding"] == "utf-8"
    assert "--include-unknown" in args and "--accept-source-agreements" in args


def test_scan_prefers_the_winget_client_module_when_it_answers(monkeypatch):
    monkeypatch.setattr(winget_updates, "find_winget_executable", lambda: r"C:\winget.exe")
    monkeypatch.setattr(winget_updates, "_scan_with_powershell_module", lambda timeout: _EXPECTED[:1])
    calls = _scan_with(monkeypatch, _completed(0, _FIXTURE_EN))
    assert list_outdated_packages() == _EXPECTED[:1]
    assert calls == []


def test_scan_budget_stays_inside_the_close_wait(monkeypatch):
    # closeEvent waits 65 s for the scan runner; module + CLI must fit.
    assert winget_updates._MODULE_SCAN_TIMEOUT_SEC < winget_updates._SCAN_TIMEOUT_SEC <= 60
    seen = {}
    clock = {"now": 1000.0}

    def module(timeout):
        # A module that hangs until its own timeout kills it.
        seen["module"] = timeout
        clock["now"] += timeout
        return None

    def cli(exe, timeout):
        seen["cli"] = timeout
        return []

    monkeypatch.setattr(winget_updates, "find_winget_executable", lambda: "winget")
    monkeypatch.setattr(winget_updates, "_scan_with_powershell_module", module)
    monkeypatch.setattr(winget_updates, "_scan_with_cli", cli)
    monkeypatch.setattr(winget_updates.time, "monotonic", lambda: clock["now"])
    list_outdated_packages()
    assert seen["module"] + seen["cli"] <= winget_updates._SCAN_TIMEOUT_SEC


@pytest.mark.parametrize("stdout, expected", [
    ('[{"Name":"Čítač","Id":"Example.PdfReader","InstalledVersion":"1.0","Available":"1.2","Source":"winget"}]',
     [OutdatedPackage("Čítač", "Example.PdfReader", "1.0", "1.2", "winget")]),
    ('{"Name":"A","Id":"A.A","InstalledVersion":"1","Available":"2","Source":"winget"}',
     [OutdatedPackage("A", "A.A", "1", "2", "winget")]),
    ("[]", []),
    ("", []),
])
def test_module_scan_reads_the_json(monkeypatch, stdout, expected):
    _scan_with(monkeypatch, _completed(0, stdout))
    assert winget_updates._scan_with_powershell_module(5) == expected


@pytest.mark.parametrize("result", [_completed(3), _completed(4), _completed(0, "not json")])
def test_module_scan_falls_back_when_the_module_is_missing_or_fails(monkeypatch, result):
    _scan_with(monkeypatch, result)
    assert winget_updates._scan_with_powershell_module(5) is None


def test_find_winget_executable_uses_path_first(monkeypatch):
    monkeypatch.setattr(winget_updates.shutil, "which", lambda name: r"C:\Tools\winget.exe")
    assert winget_updates.find_winget_executable() == r"C:\Tools\winget.exe"


def test_find_winget_executable_finds_the_alias_outside_path(monkeypatch, tmp_path):
    # Elevated sessions often lack WindowsApps on PATH although App
    # Installer is installed - that must not read as "winget missing".
    alias = tmp_path / "Microsoft" / "WindowsApps" / "winget.exe"
    alias.parent.mkdir(parents=True)
    alias.write_bytes(b"")
    monkeypatch.setattr(winget_updates.shutil, "which", lambda name: None)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.delenv("ProgramFiles", raising=False)
    assert winget_updates.find_winget_executable() == str(alias)


def test_find_winget_executable_picks_the_newest_app_installer_package(monkeypatch, tmp_path):
    for version in ("1.9.25200.0", "1.25.340.0", "1.21.3482.0"):
        folder = tmp_path / "WindowsApps" / f"Microsoft.DesktopAppInstaller_{version}_x64__8wekyb3d8bbwe"
        folder.mkdir(parents=True)
        (folder / "winget.exe").write_bytes(b"")
    monkeypatch.setattr(winget_updates.shutil, "which", lambda name: None)
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.setenv("ProgramFiles", str(tmp_path))
    assert "1.25.340.0" in winget_updates.find_winget_executable()


def test_find_winget_executable_none_when_absent(monkeypatch, tmp_path):
    monkeypatch.setattr(winget_updates.shutil, "which", lambda name: None)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("ProgramFiles", str(tmp_path))
    assert winget_updates.find_winget_executable() is None


def test_update_uses_the_winget_the_scan_found(monkeypatch):
    package = OutdatedPackage(name="A", id="A.A", installed_version="1", available_version="2", source="winget")
    exe = r"C:\Users\x\AppData\Local\Microsoft\WindowsApps\winget.exe"
    monkeypatch.setattr(winget_updates, "find_winget_executable", lambda: exe)
    # Linux has no CREATE_NEW_PROCESS_GROUP; the flag value is irrelevant here.
    monkeypatch.setattr(winget_updates.subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200, raising=False)
    with patch("portablefix.winget_updates.subprocess.Popen") as mock_popen:
        mock_popen.return_value.communicate.return_value = ("", None)
        mock_popen.return_value.returncode = 0
        update_package(package)
    assert mock_popen.call_args[0][0][0] == exe


def _collect_runner_signals(runner):
    got = {"finished": [], "failed": []}
    runner.scan_finished.connect(lambda packages: got["finished"].append(packages))
    runner.scan_failed.connect(lambda error: got["failed"].append(error))
    return got


def test_scan_runner_emits_scan_failed_for_an_unavailable_winget(monkeypatch):
    monkeypatch.setattr(winget_updates, "find_winget_executable", lambda: None)
    runner = WingetScanRunner()
    got = _collect_runner_signals(runner)
    runner.run()
    assert got["finished"] == []
    assert [(e.kind, e.reason) for e in got["failed"]] == [("unavailable", "not_found")]


def test_scan_runner_turns_an_unexpected_crash_into_a_failed_scan(monkeypatch):
    def boom():
        raise ValueError("bad")

    monkeypatch.setattr(winget_updates, "list_outdated_packages", boom)
    runner = WingetScanRunner()
    got = _collect_runner_signals(runner)
    runner.run()
    assert [(e.kind, e.reason) for e in got["failed"]] == [("error", "failed")]
    assert "ValueError" in got["failed"][0].detail


def test_scan_runner_emits_the_packages_on_success(monkeypatch):
    monkeypatch.setattr(winget_updates, "list_outdated_packages", lambda: _EXPECTED)
    runner = WingetScanRunner()
    got = _collect_runner_signals(runner)
    runner.run()
    assert got == {"finished": [_EXPECTED], "failed": []}


# --- G16: the Microsoft.WinGet.Client script, run in real PowerShell ------


def _powershell_or_skip() -> str:
    import shutil

    exe = os.environ.get("PORTABLEFIX_TEST_PWSH") or shutil.which("powershell") or shutil.which("pwsh")
    if not exe:
        pytest.skip("no PowerShell available")
    return exe


def test_module_scan_script_parses_without_errors():
    exe = _powershell_or_skip()
    check = (
        "$e = $null; [System.Management.Automation.Language.Parser]::ParseInput($env:PF_SCRIPT, [ref]$null, [ref]$e) | Out-Null; "
        "if ($e.Count) { $e | ForEach-Object { $_.Message }; exit 1 }"
    )
    result = subprocess.run(
        [exe, "-NoProfile", "-NonInteractive", "-Command", check],
        capture_output=True, text=True, timeout=60, env={**os.environ, "PF_SCRIPT": winget_updates._MODULE_SCAN_SCRIPT},
    )
    assert result.returncode == 0, result.stdout + result.stderr


# Functions win over cmdlets in command lookup; the guard refuses to run
# the script (exit 97) unless each name really resolves to the stub.
_MODULE_STUBS = (
    "function Get-Module { param([switch] $ListAvailable, [string] $Name) [pscustomobject]@{ Name = $Name } }; "
    "function Import-Module { param([string] $Name) }; "
    "function Get-WinGetPackage { "
    "[pscustomobject]@{ Name = 'PDF Čítač'; Id = 'Example.PdfReader'; InstalledVersion = '1.0'; "
    "AvailableVersions = @('1.2', '1.1'); IsUpdateAvailable = $true; Source = 'winget' }; "
    "[pscustomobject]@{ Name = 'Current'; Id = 'Current.App'; InstalledVersion = '5.0'; "
    "AvailableVersions = @(); IsUpdateAvailable = $false; Source = 'winget' } }; "
    "foreach ($n in 'Get-Module', 'Import-Module', 'Get-WinGetPackage') { "
    "if ((Get-Command $n).CommandType -ne 'Function') { exit 97 } }; "
)


def test_module_scan_script_lists_only_packages_with_an_update(monkeypatch):
    exe = _powershell_or_skip()
    monkeypatch.setattr("portablefix.paths.powershell_executable", lambda: exe)
    monkeypatch.setattr(winget_updates, "_MODULE_SCAN_SCRIPT", _MODULE_STUBS + winget_updates._MODULE_SCAN_SCRIPT)
    assert winget_updates._scan_with_powershell_module(60) == [
        OutdatedPackage("PDF Čítač", "Example.PdfReader", "1.0", "1.2", "winget"),
    ]


def test_module_scan_script_exits_3_without_the_module():
    exe = _powershell_or_skip()
    stub = "function Get-Module { param([switch] $ListAvailable, [string] $Name) }; "
    result = subprocess.run(
        [exe, "-NoProfile", "-NonInteractive", "-Command", stub + winget_updates._MODULE_SCAN_SCRIPT],
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 3, result.stdout + result.stderr


def test_parse_treats_prose_above_a_dashed_line_as_an_unreadable_table():
    # Any text with a dashed line under it is "a table" - one whose header
    # is a sentence must end as unreadable (a failed scan), not as "no
    # updates", and without trying every split of a long sentence.
    prose = " ".join(f"slovo{i}" for i in range(40))
    text = prose + "\n" + "-" * 40 + "\nAnyDesk  AnyDesk.AnyDesk  1.0  2.0  winget\n"
    assert winget_updates._parse_upgrade_output(text) == (True, [])


def _winget_table(header: list[str], rows: list[list[str]], summary: str) -> str:
    # The way winget lays a table out: each column padded to its widest
    # value, one space between columns, a dashed line as wide as the table.
    widths = [max(len(r[i]) for r in [header] + rows) for i in range(len(header))]

    def line(cells):
        return " ".join(c.ljust(w) for c, w in zip(cells, widths)).rstrip()

    lines = [line(header), "-" * len(line(header))] + [line(r) for r in rows] + [summary]
    return "\n".join(lines) + "\n"


_HEADERS = {
    "en": ["Name", "Id", "Version", "Available", "Source"],
    "de": ["Name", "ID", "Version", "Verfügbar", "Quelle"],
    "sk": ["Názov", "ID", "Verzia", "K dispozícii", "Zdroj"],
}
_UNKNOWN_ROWS = [
    ["Zoom", "Zoom.Zoom", "Unknown", "6.0.2", "winget"],
    ["Opera", "Opera.Opera", "Unknown", "113.0", "winget"],
]


@pytest.mark.parametrize("language", ["en", "de", "sk"])
@pytest.mark.parametrize("row_count", [1, 2])
def test_parse_reads_a_table_where_every_installed_version_is_unknown(language, row_count):
    # With --include-unknown the installed version is often "Unknown"; when
    # no row had a numeric one, the split one column to the right won and
    # the update would have run `winget upgrade --id Unknown`.
    rows = _UNKNOWN_ROWS[:row_count]
    text = _winget_table(_HEADERS[language], rows, "2 upgrades available.")
    assert parse_winget_upgrade_table(text) == [OutdatedPackage(*row) for row in rows]


def test_parse_counts_a_package_row_it_could_not_read():
    text = _winget_table(
        _HEADERS["en"],
        [["Foo", "Foo.Foo", "1.0 beta", "2.0", "winget"], ["Bar", "Bar.Bar", "1.0", "2.0", "winget"]],
        "2 upgrades available.",
    )
    table_found, packages, unreadable = winget_updates._parse_upgrade_table(text)
    assert (table_found, unreadable) == (True, 1)
    assert [p.id for p in packages] == ["Bar.Bar"]


@pytest.mark.parametrize("fixture", [_FIXTURE_EN, _FIXTURE_DE, _FIXTURE_SK], ids=["en", "de", "sk"])
def test_parse_does_not_count_the_summary_line_as_an_unreadable_row(fixture):
    assert winget_updates._parse_upgrade_table(fixture)[2] == 0


def test_scan_with_an_unreadable_row_keeps_the_rest_and_says_the_list_is_incomplete(monkeypatch, cli_only):
    # A package whose version has a space in it used to vanish silently -
    # for that package a false "up to date".
    text = _winget_table(
        _HEADERS["sk"],
        [["Foo", "Foo.Foo", "1.0 beta", "2.0", "winget"], ["Bar", "Bar.Bar", "1.0", "2.0", "winget"]],
        "K dispozícii sú 2 inovácie.",
    )
    _scan_with(monkeypatch, _completed(0, text))
    with pytest.raises(WingetScanError) as info:
        list_outdated_packages()
    assert (info.value.kind, info.value.reason) == ("error", "unparsed")
    assert [p.id for p in info.value.packages] == ["Bar.Bar"]


def test_module_scan_accepts_json_with_a_utf8_bom(monkeypatch):
    # Windows PowerShell 5.1 may write the UTF-8 preamble to redirected
    # stdout; json.loads rejects it, which silently disabled the module path.
    _scan_with(monkeypatch, _completed(0, '﻿[{"Name":"A","Id":"A.A","InstalledVersion":"1","Available":"2","Source":"winget"}]'))
    assert winget_updates._scan_with_powershell_module(5) == [OutdatedPackage("A", "A.A", "1", "2", "winget")]
