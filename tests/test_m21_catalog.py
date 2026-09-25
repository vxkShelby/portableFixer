import base64
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from portablefix.models import ModuleCategory, RiskLevel
from portablefix.module_engine import load_module

CATALOG_PATH = Path(__file__).resolve().parent.parent / "Modules" / "m21_hardware_sensors" / "actions.yaml"


def test_m21_catalog_loads_5_actions_in_repair_category():
    module = load_module(CATALOG_PATH)
    assert module.module_id == "m21_hardware_sensors"
    assert module.category == ModuleCategory.REPAIR
    assert len(module.actions) == 5


def test_m21_catalog_risk_distribution():
    module = load_module(CATALOG_PATH)
    by_risk = {}
    for action in module.actions:
        by_risk.setdefault(action.risk, []).append(action.id)
    assert by_risk[RiskLevel.SAFE] == ["pawnio_status_report", "battery_wear", "mem_test_result"]
    assert by_risk[RiskLevel.MODERATE] == ["pawnio_install"]
    assert by_risk[RiskLevel.REQUIRES_REBOOT] == ["mem_test_schedule"]
    assert RiskLevel.DESTRUCTIVE not in by_risk


def test_m21_catalog_only_install_and_mem_test_schedule_have_undo():
    module = load_module(CATALOG_PATH)
    by_id = {a.id: a for a in module.actions}
    assert by_id["pawnio_install"].undo_command is not None
    assert by_id["mem_test_schedule"].undo_command is not None
    for read_only in ("pawnio_status_report", "battery_wear", "mem_test_result"):
        assert by_id[read_only].undo_command is None, read_only


def test_m21_catalog_install_uses_official_latest_release_url():
    module = load_module(CATALOG_PATH)
    action = next(a for a in module.actions if a.id == "pawnio_install")
    assert "github.com/namazso/PawnIO.Setup/releases/latest/download/PawnIO_setup.exe" in action.command
    assert "-install" in action.command
    assert "-silent" in action.command


def test_m21_catalog_covers_expected_ids():
    module = load_module(CATALOG_PATH)
    ids = {a.id for a in module.actions}
    assert ids == {"pawnio_status_report", "pawnio_install", "battery_wear", "mem_test_schedule", "mem_test_result"}


def test_m21_catalog_every_action_has_both_language_labels_and_descriptions():
    module = load_module(CATALOG_PATH)
    for action in module.actions:
        assert action.label_sk
        assert action.label_en
        assert action.description_sk
        assert action.description_en


# --- G17: battery wear, RAM test schedule and result --------------------------
#
# The commands run below in PowerShell against stubbed tools: every cmdlet
# or executable they touch is shadowed by a function (functions win command
# lookup) and the script exits 97 unless each name really resolves to the
# stub, so a test run never reads the host's battery, boot configuration or
# event log - and never schedules anything. Stub output and exception
# messages are Slovak on purpose: a command keying off them would fail.

STUB_GUARD_EXIT = 97
BATTERY_ID = "battery_wear"
SCHEDULE_ID = "mem_test_schedule"
RESULT_ID = "mem_test_result"
MEMDIAG_GUID = "{b2721d73-1db4-4c62-bf78-c548a880142d}"
BATTERY_NS = "http://schemas.microsoft.com/battery/2012"


def _powershell_or_skip() -> str:
    exe = os.environ.get("PORTABLEFIX_TEST_PWSH") or shutil.which("powershell") or shutil.which("pwsh")
    if not exe:
        pytest.skip("no PowerShell available")
    return exe


def _action(action_id):
    return next(a for a in load_module(CATALOG_PATH).actions if a.id == action_id)


def _ps_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _ps_bool(value: bool) -> str:
    return "$true" if value else "$false"


def _run_ps(stubs: list, stubbed_names: list, command: str, extra_env=None):
    guard = (
        "foreach ($n in " + ", ".join(_ps_quote(n) for n in stubbed_names) + ") { "
        "if ((Get-Command $n -EA SilentlyContinue | Select-Object -First 1).CommandType -ne 'Function') "
        f"{{ exit {STUB_GUARD_EXIT} }} }}"
    )
    # Redirected variables are set inside the script, not in the child's
    # environment: Windows PowerShell cannot start with a fake SystemRoot.
    env_lines = [f"$env:{name} = {_ps_quote(value)}" for name, value in (extra_env or {}).items()]
    script = "; ".join(
        ["[Console]::OutputEncoding=[Text.Encoding]::UTF8"] + env_lines + stubs + [guard, command]
    )
    result = subprocess.run(
        [_powershell_or_skip(), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script],
        env=dict(os.environ), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert result.returncode != STUB_GUARD_EXIT, "a system tool was not stubbed - refusing to run the real one"
    return result


def _verdict(stdout: str) -> str:
    lines = [line for line in stdout.splitlines() if line.startswith("VERDICT: ")]
    assert len(lines) == 1, stdout
    return lines[0]


def _calls(log: Path) -> list:
    return log.read_text(encoding="utf-8-sig").splitlines() if log.exists() else []


# --- battery_wear ---------------------------------------------------------------

def _battery(design="60000", full="54000", cycles="312", battery_id="DELL 1VX1H", maker="SMP", relative="0"):
    """One <Battery> element as powercfg writes it; None leaves a field out."""
    fields = [("Id", battery_id), ("Manufacturer", maker), ("SerialNumber", "1234"), ("Chemistry", "LION"),
              ("LongTerm", "1"), ("RelativeCapacity", relative), ("DesignCapacity", design),
              ("FullChargeCapacity", full), ("CycleCount", cycles)]
    return "<Battery>" + "".join(f"<{k}>{v}</{k}>" for k, v in fields if v is not None) + "</Battery>"


def _report(*batteries: str, namespace=BATTERY_NS) -> str:
    ns = f' xmlns="{namespace}"' if namespace else ""
    return (
        f'<?xml version="1.0" encoding="utf-8"?><BatteryReport{ns}>'
        "<ReportInformation><ReportVersion>1</ReportVersion></ReportInformation>"
        f"<Batteries>{''.join(batteries)}</Batteries><RuntimeEstimates/></BatteryReport>"
    )


def _run_battery(tmp_path, report=None, cim_count=1, cim_throws=False, powercfg_exit=0, culture=None):
    """report None = powercfg writes no file."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    temp = tmp_path / "temp"
    temp.mkdir()
    log = tmp_path / "calls.log"
    lg = _ps_quote(str(log))
    xml_b64 = base64.b64encode((report or "").encode("utf-8")).decode()
    if cim_throws:
        cim_body = "throw [System.InvalidOperationException]::new('Neplatná trieda')"
    elif cim_count:
        cim_body = f"1..{cim_count} | ForEach-Object {{ [pscustomobject]@{{ Name = 'bat' }} }}"
    else:
        cim_body = "@()"
    stubs = [
        "function Get-CimInstance { [CmdletBinding()] param([string] $ClassName) "
        f"Add-Content -Path {lg} -Value ('cim ' + $ClassName); {cim_body} }}",
        # Writes the report (when given) where /OUTPUT points, like powercfg.
        "function powercfg.exe { "
        f"Add-Content -Path {lg} -Value ('powercfg ' + ($args -join ' ')); "
        f"if ({_ps_bool(report is not None)}) {{ "
        f"[IO.File]::WriteAllBytes($args[3], [Convert]::FromBase64String('{xml_b64}')) }}; "
        f"$global:LASTEXITCODE = {powercfg_exit} }}",
    ]
    if culture:
        stubs.insert(0, f"[Threading.Thread]::CurrentThread.CurrentCulture = [Globalization.CultureInfo]::new('{culture}')")
    result = _run_ps(stubs, ["Get-CimInstance", "powercfg.exe"], _action(BATTERY_ID).command,
                     extra_env={"TEMP": str(temp)})
    return result, _calls(log), temp


def test_battery_desktop_without_battery_skips_powercfg(tmp_path):
    result, calls, _ = _run_battery(tmp_path, report=_report(), cim_count=0)
    assert result.returncode == 0, result.stdout + result.stderr
    assert _verdict(result.stdout).startswith("VERDICT: NO BATTERY")
    assert calls == ["cim Win32_Battery"]


def test_battery_good_lists_capacities_cycles_and_cleans_the_temp_file(tmp_path):
    result, calls, temp = _run_battery(tmp_path, report=_report(_battery()))
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Battery 1 (SMP DELL 1VX1H): design capacity 60000 mWh, full charge capacity 54000 mWh" in result.stdout
    assert "health 90 % (wear 10 %), cycles 312 -> GOOD" in result.stdout
    assert _verdict(result.stdout).startswith("VERDICT: GOOD")
    powercfg = [c for c in calls if c.startswith("powercfg ")]
    assert len(powercfg) == 1
    assert powercfg[0].startswith("powercfg /batteryreport /XML /OUTPUT " + str(temp))
    assert powercfg[0].endswith(".xml")
    assert list(temp.iterdir()) == []


@pytest.mark.parametrize("full, verdict", [
    ("48001", "GOOD"),     # just above 80 %
    ("48000", "CAUTION"),  # exactly 80 %
    ("36000", "CAUTION"),  # exactly 60 %
    ("35999", "REPLACE"),  # just below 60 %
    ("12000", "REPLACE"),
])
def test_battery_verdict_thresholds(tmp_path, full, verdict):
    result, _, _ = _run_battery(tmp_path, report=_report(_battery(full=full)))
    assert result.returncode == 0, result.stdout + result.stderr
    assert _verdict(result.stdout).startswith("VERDICT: " + verdict)
    assert result.stdout.count("-> " + verdict) == 1


@pytest.mark.parametrize("design, full", [("0", "0"), ("60000", "0"), ("0", "40000"), (None, None), ("", "")])
def test_battery_without_usable_capacities_is_unknown(tmp_path, design, full):
    result, _, _ = _run_battery(tmp_path, report=_report(_battery(design=design, full=full, cycles="0")))
    assert result.returncode == 0, result.stdout + result.stderr
    assert _verdict(result.stdout).startswith("VERDICT: UNKNOWN")
    assert "cycles unknown" in result.stdout


@pytest.mark.parametrize("design, full, hint", [("60000", "0", True), ("0", "0", False), ("0", "40000", False)])
def test_battery_zero_full_charge_hints_at_a_dead_battery(tmp_path, design, full, hint):
    # The verdict stays UNKNOWN, but a zero full charge next to a valid
    # design capacity is usually a dead battery the technician must not miss.
    result, _, _ = _run_battery(tmp_path, report=_report(_battery(design=design, full=full)))
    assert _verdict(result.stdout).startswith("VERDICT: UNKNOWN")
    assert ("often means a dead battery" in result.stdout) is hint


def test_battery_multi_battery_laptop_takes_the_worst(tmp_path):
    report = _report(_battery(full="57000", battery_id="INT"), _battery(full="30000", battery_id="EXT", cycles=None))
    result, _, temp = _run_battery(tmp_path, report=report)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Battery 1 (SMP INT)" in result.stdout and "-> GOOD" in result.stdout
    assert "Battery 2 (SMP EXT)" in result.stdout and "cycles unknown -> REPLACE" in result.stdout
    assert _verdict(result.stdout).startswith("VERDICT: REPLACE")
    assert list(temp.iterdir()) == []


def test_battery_unknown_ranks_between_good_and_caution(tmp_path):
    result, _, _ = _run_battery(tmp_path / "a", report=_report(_battery(full="40000"), _battery(design="0")))
    assert _verdict(result.stdout).startswith("VERDICT: CAUTION")
    result, _, _ = _run_battery(tmp_path / "b", report=_report(_battery(), _battery(design="0")))
    assert _verdict(result.stdout).startswith("VERDICT: UNKNOWN")


def test_battery_battery_without_names_gets_a_plain_label(tmp_path):
    result, _, _ = _run_battery(tmp_path, report=_report(_battery(battery_id=None, maker="")))
    assert "Battery 1: design capacity 60000 mWh" in result.stdout


def test_battery_report_without_namespace_or_without_batteries(tmp_path):
    result, _, _ = _run_battery(tmp_path / "a", report=_report(_battery(), namespace=None))
    assert _verdict(result.stdout).startswith("VERDICT: GOOD")
    result, _, _ = _run_battery(tmp_path / "b", report=_report())
    assert result.returncode == 0, result.stdout + result.stderr
    assert _verdict(result.stdout).startswith("VERDICT: NO BATTERY")


def test_battery_relative_capacities_are_not_labelled_mwh(tmp_path):
    result, _, _ = _run_battery(tmp_path, report=_report(_battery(design="100", full="70", relative="1")))
    assert "design capacity 100 (relative units)" in result.stdout
    assert "mWh" not in result.stdout
    assert _verdict(result.stdout).startswith("VERDICT: CAUTION")


def test_battery_cim_failure_still_reads_the_report(tmp_path):
    result, calls, _ = _run_battery(tmp_path, report=_report(_battery()), cim_throws=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert any(c.startswith("powercfg ") for c in calls)
    assert _verdict(result.stdout).startswith("VERDICT: GOOD")


@pytest.mark.parametrize("report, exit_code", [(None, 0), (_report(_battery()), 1), ("<BatteryReport><Batt", 0)])
def test_battery_powercfg_or_xml_failure_exits_non_zero_and_cleans_up(tmp_path, report, exit_code):
    result, _, temp = _run_battery(tmp_path, report=report, powercfg_exit=exit_code)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "VERDICT" not in result.stdout
    assert list(temp.iterdir()) == []


def test_battery_percentages_ignore_the_display_culture(tmp_path):
    result, _, _ = _run_battery(tmp_path, report=_report(_battery(full="45123")), culture="sk-SK")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "health 75.2 % (wear 24.8 %)" in result.stdout


# --- mem_test_schedule (and its preview and undo) -----------------------------

OTHER_GUID = "{466f5a88-0af2-4f76-9038-095b170dc21c}"


def _run_bcd(tmp_path, command, memdiag_exit=0, set_exit=0, set_takes_effect=True, hive_readable=True,
             pending=False, delete_exit=0, element_denied=False, other_sequence=False, subkey_denied=False):
    """bcdedit and the BCD registry hive: pending = {memdiag} already in the
    one-time boot sequence (other_sequence = another entry is);
    set_takes_effect = a successful /bootsequence really lands in the hive;
    element_denied = the bootsequence element exists but cannot be read;
    subkey_denied = Test-Path cannot see the 24000002 subkey because reading
    it is denied (Get-Item then fails with an access error, not "not found")."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    log = tmp_path / "calls.log"
    lg = _ps_quote(str(log))
    guid = _ps_quote(MEMDIAG_GUID)
    initial = f"@({guid})" if pending else f"@({_ps_quote(OTHER_GUID)})" if other_sequence else "$null"
    stubs = [
        f"$global:pfSeq = {initial}",
        "function bcdedit.exe { "
        f"Add-Content -Path {lg} -Value ('bcdedit ' + ($args -join ' ')); "
        "switch ($args[0]) { "
        f"'/enum' {{ 'Identifikátor {{memdiag}}'; $global:LASTEXITCODE = {memdiag_exit} }} "
        f"'/bootsequence' {{ if ({set_exit} -eq 0 -and {_ps_bool(set_takes_effect)}) {{ $global:pfSeq = @({guid}) }}; "
        f"'Operácia bola úspešná.'; $global:LASTEXITCODE = {set_exit} }} "
        f"'/deletevalue' {{ if ({delete_exit} -eq 0) {{ $global:pfSeq = $null }}; $global:LASTEXITCODE = {delete_exit} }} "
        "default { $global:LASTEXITCODE = 87 } } }",
        # The Elements key answers "is the hive readable", its 24000002
        # subkey "is there a one-time boot sequence at all".
        "function Test-Path { [CmdletBinding()] param([string] $LiteralPath) "
        f"Add-Content -Path {lg} -Value ('test ' + $LiteralPath); "
        f"if ($LiteralPath.EndsWith('\\24000002')) {{ {_ps_bool(hive_readable)} -and [bool]$global:pfSeq }} "
        f"else {{ {_ps_bool(hive_readable)} }} }}",
        "function Get-ItemProperty { [CmdletBinding()] param([string] $LiteralPath, [string] $Name) "
        f"Add-Content -Path {lg} -Value ('get ' + $LiteralPath + ' ' + $Name); "
        f"if ({_ps_bool(element_denied)}) {{ throw [System.Security.SecurityException]::new('Prístup odmietnutý.') }}; "
        "if ($global:pfSeq) { [pscustomobject]@{ Element = $global:pfSeq } } }",
        # Only asked when Test-Path said the subkey is not there.
        "function Get-Item { [CmdletBinding()] param([string] $LiteralPath) "
        f"Add-Content -Path {lg} -Value ('item ' + $LiteralPath); "
        f"if ({_ps_bool(subkey_denied)}) {{ throw [System.Security.SecurityException]::new('Prístup odmietnutý.') }}; "
        "throw [System.Management.Automation.ItemNotFoundException]::new('Cesta neexistuje.') }",
        "function Restart-Computer { exit 96 }",
        "function shutdown.exe { exit 96 }",
    ]
    names = ["bcdedit.exe", "Test-Path", "Get-ItemProperty", "Get-Item", "Restart-Computer", "shutdown.exe"]
    result = _run_ps(stubs, names, command)
    assert result.returncode != 96, "the action tried to restart the PC"
    return result, _calls(log)


def _bcdedit_calls(calls):
    return [c for c in calls if c.startswith("bcdedit ")]


def test_mem_test_schedule_declares_reboot_tier_without_restarting():
    action = _action(SCHEDULE_ID)
    assert action.risk == RiskLevel.REQUIRES_REBOOT
    assert action.exclude_from_select_all is True
    # Nothing restarts now, and the rest of the batch does not wait for it.
    assert action.restarts_pc is False and action.restart_before_next is False
    assert action.changes_system is False
    assert action.preview_command and action.undo_command
    for text in (action.command, action.preview_command, action.undo_command):
        for forbidden in ("Restart-Computer", "shutdown", "mdsched", "Stop-Computer"):
            assert forbidden not in text, forbidden


def test_mem_test_schedule_sets_the_one_time_boot_sequence_and_verifies_it(tmp_path):
    result, calls = _run_bcd(tmp_path, _action(SCHEDULE_ID).command)
    assert result.returncode == 0, result.stdout + result.stderr
    assert _bcdedit_calls(calls) == ["bcdedit /enum {memdiag}", "bcdedit /bootsequence {memdiag}"]
    assert "get HKLM:\\BCD00000000\\Objects\\{9dea862c-5cdd-4e70-acc1-f32b344d4795}\\Elements\\24000002 Element" in calls
    assert "scheduled for the next restart only (one-time boot sequence, verified)" in result.stdout
    assert "NOT restarted now" in result.stdout
    assert "Operácia" not in result.stdout


def test_mem_test_schedule_without_memdiag_entry_schedules_nothing(tmp_path):
    result, calls = _run_bcd(tmp_path, _action(SCHEDULE_ID).command, memdiag_exit=1)
    assert result.returncode == 1
    assert _bcdedit_calls(calls) == ["bcdedit /enum {memdiag}"]
    assert "exit code 1" in result.stdout
    # bcdedit fails the same way without elevation - the message must not
    # blame only a missing entry.
    assert "not readable (needs administrator" in result.stdout


def test_mem_test_schedule_bcdedit_failure_exits_non_zero(tmp_path):
    result, _ = _run_bcd(tmp_path, _action(SCHEDULE_ID).command, set_exit=5)
    assert result.returncode == 1
    assert "exit code 5" in result.stdout


def test_mem_test_schedule_success_that_did_not_land_is_a_failure(tmp_path):
    result, _ = _run_bcd(tmp_path, _action(SCHEDULE_ID).command, set_takes_effect=False)
    assert result.returncode == 1
    assert "nothing will run at the next restart" in result.stdout


def test_mem_test_schedule_trusts_the_exit_code_when_the_hive_is_unreadable(tmp_path):
    result, _ = _run_bcd(tmp_path, _action(SCHEDULE_ID).command, set_takes_effect=False, hive_readable=False)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "not verifiable" in result.stdout


@pytest.mark.parametrize("pending, readable, expected", [
    (False, True, "next restart: no"), (True, True, "next restart: yes"), (False, False, "next restart: unknown"),
])
def test_mem_test_schedule_preview_changes_nothing(tmp_path, pending, readable, expected):
    result, calls = _run_bcd(tmp_path, _action(SCHEDULE_ID).preview_command, pending=pending, hive_readable=readable)
    assert result.returncode == 0, result.stdout + result.stderr
    assert _bcdedit_calls(calls) == ["bcdedit /enum {memdiag}"]
    assert expected in result.stdout
    assert "Would run: bcdedit /bootsequence {memdiag}" in result.stdout


def test_mem_test_schedule_preview_reports_missing_memdiag(tmp_path):
    result, calls = _run_bcd(tmp_path, _action(SCHEDULE_ID).preview_command, memdiag_exit=1)
    assert result.returncode == 0
    assert "would fail and schedule nothing" in result.stdout
    assert "not readable (needs administrator" in result.stdout
    assert _bcdedit_calls(calls) == ["bcdedit /enum {memdiag}"]


def test_mem_test_schedule_undo_cancels_a_pending_run(tmp_path):
    result, calls = _run_bcd(tmp_path, _action(SCHEDULE_ID).undo_command, pending=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert _bcdedit_calls(calls) == ["bcdedit /deletevalue {bootmgr} bootsequence"]
    assert "cancelled" in result.stdout


def test_mem_test_schedule_undo_with_nothing_pending_touches_nothing(tmp_path):
    result, calls = _run_bcd(tmp_path, _action(SCHEDULE_ID).undo_command, pending=False)
    assert result.returncode == 0
    assert _bcdedit_calls(calls) == []
    assert "nothing to cancel" in result.stdout


def test_mem_test_schedule_undo_failure_exits_non_zero(tmp_path):
    result, _ = _run_bcd(tmp_path, _action(SCHEDULE_ID).undo_command, pending=True, delete_exit=5)
    assert result.returncode == 1
    assert "exit code 5" in result.stdout


@pytest.mark.parametrize("hive_readable, element_denied", [(False, False), (True, True)])
def test_mem_test_schedule_undo_that_cannot_read_the_sequence_still_cancels(tmp_path, hive_readable, element_denied):
    result, calls = _run_bcd(tmp_path, _action(SCHEDULE_ID).undo_command, pending=True,
                             hive_readable=hive_readable, element_denied=element_denied)
    assert result.returncode == 0, result.stdout + result.stderr
    assert _bcdedit_calls(calls) == ["bcdedit /deletevalue {bootmgr} bootsequence"]


def test_mem_test_schedule_undo_leaves_another_tools_boot_sequence_alone(tmp_path):
    result, calls = _run_bcd(tmp_path, _action(SCHEDULE_ID).undo_command, other_sequence=True)
    assert result.returncode == 0
    assert _bcdedit_calls(calls) == []
    assert "nothing to cancel" in result.stdout


def test_mem_test_schedule_unreadable_sequence_element_is_not_a_failure(tmp_path):
    result, _ = _run_bcd(tmp_path, _action(SCHEDULE_ID).command, element_denied=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "not verifiable" in result.stdout
    preview, _ = _run_bcd(tmp_path / "p", _action(SCHEDULE_ID).preview_command, pending=True, element_denied=True)
    assert "next restart: unknown" in preview.stdout


def test_mem_test_schedule_denied_sequence_subkey_is_not_a_false_failure(tmp_path):
    # Test-Path answers $false for a subkey it may not read; only a real
    # "not found" from Get-Item means the sequence did not land.
    result, calls = _run_bcd(tmp_path, _action(SCHEDULE_ID).command, set_takes_effect=False, subkey_denied=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "not verifiable" in result.stdout
    assert any(c.startswith("item ") and c.endswith("\\24000002") for c in calls)
    preview, _ = _run_bcd(tmp_path / "p", _action(SCHEDULE_ID).preview_command, subkey_denied=True)
    assert "next restart: unknown" in preview.stdout
    undo, calls = _run_bcd(tmp_path / "u", _action(SCHEDULE_ID).undo_command, subkey_denied=True)
    assert undo.returncode == 0, undo.stdout + undo.stderr
    assert _bcdedit_calls(calls) == ["bcdedit /deletevalue {bootmgr} bootsequence"]


def test_mem_test_schedule_fails_when_another_boot_sequence_stays(tmp_path):
    # bcdedit /bootsequence replaces the one-time list; what counts is that
    # {memdiag} is in it afterwards.
    result, _ = _run_bcd(tmp_path, _action(SCHEDULE_ID).command, other_sequence=True, set_takes_effect=False)
    assert result.returncode == 1
    assert "nothing will run at the next restart" in result.stdout


# --- mem_test_result ------------------------------------------------------------

def _run_result(tmp_path, event_id=None, bad_pages=None, error=None, pending=False, hive_readable=True):
    """event_id None = no matching events; error = FullyQualifiedErrorId of
    another Get-WinEvent failure."""
    log = tmp_path / "calls.log"
    lg = _ps_quote(str(log))
    if error:
        body = ("$PSCmdlet.ThrowTerminatingError([System.Management.Automation.ErrorRecord]::new("
                f"[System.UnauthorizedAccessException]::new('Prístup odmietnutý.'), '{error}', "
                "[System.Management.Automation.ErrorCategory]::PermissionDenied, $null))")
    elif event_id is None:
        body = ("$PSCmdlet.ThrowTerminatingError([System.Management.Automation.ErrorRecord]::new("
                "[System.Exception]::new('Nenašli sa žiadne udalosti.'), "
                "'NoMatchingEventsFound,Microsoft.PowerShell.Commands.GetWinEventCommand', "
                "[System.Management.Automation.ErrorCategory]::ObjectNotFound, $null))")
    else:
        props = "@()"
        if bad_pages is not None:
            # 1101/1102 payload: LaunchType, CompletionType, MemorySize, TestType,
            # TestDuration, TestCount, NumPagesTested, NumPagesUnTested, NumBadPages, ...
            values = ["'Manual'", "'Success'", "16384", "1", "900", "2", "4000000", "0", str(bad_pages), "0"]
            props = "@(" + ", ".join(f"[pscustomobject]@{{ Value = {v} }}" for v in values) + ")"
        body = (f"[pscustomobject]@{{ Id = {event_id}; TimeCreated = [datetime]::new(2026, 9, 20, 14, 5, 0); "
                f"Properties = {props}; Message = 'Lokalizovaný text' }}")
    stubs = [
        "function Get-WinEvent { [CmdletBinding()] param([hashtable] $FilterHashtable, [int] $MaxEvents) "
        f"Add-Content -Path {lg} -Value ('winevent ' + $FilterHashtable.LogName + ' ' + $FilterHashtable.ProviderName "
        "+ ' ' + ($FilterHashtable.Id -join ',') + ' max ' + $MaxEvents); "
        f"{body} }}",
        f"function Test-Path {{ [CmdletBinding()] param([string] $LiteralPath) {_ps_bool(hive_readable)} }}",
        "function Get-ItemProperty { [CmdletBinding()] param([string] $LiteralPath, [string] $Name) "
        f"if ({_ps_bool(pending)}) {{ [pscustomobject]@{{ Element = @({_ps_quote(MEMDIAG_GUID)}) }} }} }}",
    ]
    result = _run_ps(stubs, ["Get-WinEvent", "Test-Path", "Get-ItemProperty"], _action(RESULT_ID).command)
    return result, _calls(log)


def test_mem_test_result_filters_by_provider_and_event_ids(tmp_path):
    _, calls = _run_result(tmp_path, event_id=1201)
    assert calls == ["winevent System Microsoft-Windows-MemoryDiagnostics-Results 1101,1102,1103,1104,1201,1202 max 1"]


@pytest.mark.parametrize("event_id", [1101, 1201])
def test_mem_test_result_pass(tmp_path, event_id):
    result, _ = _run_result(tmp_path, event_id=event_id, bad_pages=0 if event_id == 1101 else None)
    assert result.returncode == 0, result.stdout + result.stderr
    assert _verdict(result.stdout) == "VERDICT: PASS - the memory test found no errors (2026-09-20 14:05)."
    assert f"Last result: event {event_id} from 2026-09-20 14:05" in result.stdout
    assert ("bad pages: 0" in result.stdout) == (event_id == 1101)


@pytest.mark.parametrize("event_id", [1102, 1202])
def test_mem_test_result_fail(tmp_path, event_id):
    result, _ = _run_result(tmp_path, event_id=event_id, bad_pages=12 if event_id == 1102 else None)
    assert result.returncode == 0, result.stdout + result.stderr
    assert _verdict(result.stdout).startswith("VERDICT: FAIL - the memory test found hardware errors")
    assert "(2026-09-20 14:05)" in result.stdout
    assert ("bad pages: 12" in result.stdout) == (event_id == 1102)
    assert "Lokalizovaný" not in result.stdout


@pytest.mark.parametrize("event_id, reason", [(1103, "cancelled"), (1104, "could not complete")])
def test_mem_test_result_incomplete(tmp_path, event_id, reason):
    result, _ = _run_result(tmp_path, event_id=event_id)
    assert result.returncode == 0, result.stdout + result.stderr
    verdict = _verdict(result.stdout)
    assert verdict.startswith("VERDICT: INCOMPLETE") and reason in verdict


def test_mem_test_result_never_run(tmp_path):
    result, _ = _run_result(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert _verdict(result.stdout).startswith("VERDICT: NEVER RUN")
    assert "scheduled" not in result.stdout


def test_mem_test_result_never_run_but_scheduled(tmp_path):
    result, _ = _run_result(tmp_path, pending=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "A memory test is scheduled for the next restart and has not run yet." in result.stdout
    assert _verdict(result.stdout).endswith("The scheduled test runs at the next restart.")


def test_mem_test_result_unreadable_hive_is_not_an_error(tmp_path):
    result, _ = _run_result(tmp_path, event_id=1201, pending=True, hive_readable=False)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "scheduled" not in result.stdout


def test_mem_test_result_log_read_failure_exits_non_zero(tmp_path):
    result, _ = _run_result(
        tmp_path, error="System.UnauthorizedAccessException,Microsoft.PowerShell.Commands.GetWinEventCommand",
    )
    assert result.returncode == 1
    assert "VERDICT" not in result.stdout
    assert "System.UnauthorizedAccessException" in result.stdout
    assert "Prístup" not in result.stdout


# --- all G17 actions: Windows PowerShell 5.1 syntax, no localized text -------

PARSE_CHECKER = (
    "$s = Get-Content -Raw -Encoding UTF8 -LiteralPath $env:PFSCRIPTS_FILE | ConvertFrom-Json; "
    "foreach ($p in $s.PSObject.Properties) { $t = $null; $e = $null; "
    "$ast = [System.Management.Automation.Language.Parser]::ParseInput($p.Value, [ref]$t, [ref]$e); "
    "foreach ($x in @($e)) { if ($x) { Write-Output ('ERR ' + $p.Name + ': ' + $x.Message) } }; "
    "$ps7 = @($t | Where-Object { [string]$_.Kind -in @('QuestionQuestion','QuestionQuestionEquals','QuestionDot','QuestionLBracket','AndAnd','OrOr') }); "
    "$ps7 += @($ast.FindAll({ param($n) $n.GetType().Name -in @('TernaryExpressionAst','PipelineChainAst') }, $true)); "
    "if ($ps7.Count) { Write-Output ('ERR ' + $p.Name + ': PowerShell 7-only syntax') } }; "
    "Write-Output PARSE_DONE"
)


def test_m21_catalog_scripts_parse_without_powershell_7_only_syntax(tmp_path):
    scripts = {
        f"{a.id}.{field}": getattr(a, field)
        for a in load_module(CATALOG_PATH).actions
        for field in ("command", "undo_command", "preview_command")
        if getattr(a, field)
    }
    scripts_file = tmp_path / "scripts.json"
    scripts_file.write_text(json.dumps(scripts), encoding="utf-8")
    result = subprocess.run(
        [_powershell_or_skip(), "-NoProfile", "-NonInteractive", "-Command", PARSE_CHECKER],
        env=dict(os.environ, PFSCRIPTS_FILE=str(scripts_file)), capture_output=True, text=True, timeout=120,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert "PARSE_DONE" in result.stdout, result.stdout + result.stderr
    assert [line for line in result.stdout.splitlines() if line.startswith("ERR")] == []


@pytest.mark.parametrize("action_id", [BATTERY_ID, SCHEDULE_ID, RESULT_ID])
def test_g17_actions_never_read_localized_text(action_id):
    action = _action(action_id)
    for text in filter(None, (action.command, action.preview_command, action.undo_command)):
        # Decisions come from exit codes, XML element names, GUIDs, event IDs
        # and FullyQualifiedErrorId - never from translated tool output or
        # exception/event messages.
        for forbidden in ("Select-String", "-match", "-imatch", "-cmatch", "-replace", "-split",
                          ".Message", "[regex]", "IndexOf", "Out-String"):
            assert forbidden not in text, f"{action_id}: {forbidden}"
