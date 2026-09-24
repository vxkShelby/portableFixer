# tests/test_crash_triage.py
"""G06 crash triage (m01): crash_bugcheck_triage, whea_hardware_errors,
reliability_history and crash_dump_evidence.

The real catalog commands run in PowerShell against stubbed Get-WinEvent,
Get-CimInstance and Get-ItemProperty that hand back structured events and
CIM records. Every stubbed event also carries a Slovak Message that names a
DIFFERENT stop code / product on purpose: a command that read the localized
message instead of the event XML would print the wrong thing and fail.

The stubs are PowerShell functions (functions win over cmdlets in command
lookup) and the script exits 97 unless each name really resolves to the
stub, so a test run never reads the host's event log, WMI or registry.
SystemRoot is redirected with $env: inside the script, never in the child
environment - Windows PowerShell cannot start without the real one.
"""
import datetime
import json
import os
import re
import subprocess
from pathlib import Path

import pytest

from portablefix.models import RiskLevel
from portablefix.module_engine import load_module

MODULES_DIR = Path(__file__).resolve().parent.parent / "Modules"
M01 = MODULES_DIR / "m01_diagnostics" / "actions.yaml"
TRIAGE_IDS = ("crash_bugcheck_triage", "whea_hardware_errors", "reliability_history", "crash_dump_evidence")
STUB_GUARD_EXIT = 97

# What the stubbed events' localized messages claim - never the truth.
SK_MESSAGE_BUGCHECK = (
    "Počítač bol reštartovaný po chybe kontroly. Kontrola chýb: 0x000000d1 (0x1, 0x2, 0x3, 0x4). "
    "Výpis bol uložený v: D:\\nepravda.dmp."
)
SK_MESSAGE_KP41 = "Systém sa reštartoval bez predchádzajúceho čistého vypnutia. Kód kontroly chýb: 0x124."
SK_MESSAGE_6008 = "Predchádzajúce vypnutie systému o 3:14:15 dňa 1. 1. 2026 bolo neočakávané."
SK_MESSAGE_WHEA = "Vyskytla sa opravená hardvérová chyba. Súčasť: Pamäť. Zdroj: Kontrola počítača."


def _powershell_or_skip() -> str:
    import shutil

    exe = os.environ.get("PORTABLEFIX_TEST_PWSH") or shutil.which("powershell") or shutil.which("pwsh")
    if not exe:
        pytest.skip("no PowerShell available")
    return exe


def _action(action_id: str):
    return next(a for a in load_module(M01).actions if a.id == action_id)


def _ps_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


# Stub bodies. Test data comes from JSON files named by $env:PF_* variables so
# the stubs themselves stay constant.
STUBS = r"""
function Get-WinEvent {
  [CmdletBinding()] param([hashtable] $FilterHashtable, [long] $MaxEvents)
  Add-Content -LiteralPath $env:PF_CALLS -Value ('Get-WinEvent ' + (($FilterHashtable.Keys | Sort-Object) -join ','))
  $cfg = Get-Content -Raw -Encoding UTF8 -LiteralPath $env:PF_EVENTS | ConvertFrom-Json
  if ($cfg.fail) {
    $PSCmdlet.ThrowTerminatingError((New-Object System.Management.Automation.ErrorRecord ((New-Object System.UnauthorizedAccessException 'Prístup bol odmietnutý.'), 'LogInfoUnavailable', 'PermissionDenied', $null)))
  }
  $out = New-Object System.Collections.Generic.List[object]
  foreach ($r in @($cfg.events)) {
    if ($FilterHashtable.ContainsKey('LogName') -and (@($FilterHashtable.LogName) -notcontains $r.LogName)) { continue }
    if ($FilterHashtable.ContainsKey('Id') -and (@($FilterHashtable.Id) -notcontains [int]$r.Id)) { continue }
    if ($FilterHashtable.ContainsKey('ProviderName') -and (@($FilterHashtable.ProviderName) -notcontains $r.ProviderName)) { continue }
    $time = (Get-Date).AddHours(-[double]$r.HoursAgo)
    if ($FilterHashtable.ContainsKey('StartTime') -and $time -lt $FilterHashtable.StartTime) { continue }
    $data = foreach ($p in @($r.Data)) {
      $v = [Security.SecurityElement]::Escape([string]$p[1])
      if ($p[0]) { '<Data Name="' + $p[0] + '">' + $v + '</Data>' } else { '<Data>' + $v + '</Data>' }
    }
    $xml = '<Event xmlns="http://schemas.microsoft.com/win/2004/08/events/event"><System><Provider Name="' + $r.ProviderName + '"/><EventID>' + $r.Id + '</EventID></System><EventData>' + ($data -join '') + '</EventData></Event>'
    $o = [pscustomobject]@{ Id = [int]$r.Id; ProviderName = [string]$r.ProviderName; LogName = [string]$r.LogName; Level = [byte]$r.Level; TimeCreated = $time; Message = [string]$r.Message; LevelDisplayName = 'Chyba'; PfXml = $xml; Properties = @(foreach ($p in @($r.Data)) { [pscustomobject]@{ Value = $p[1] } }) }
    $o | Add-Member -MemberType ScriptMethod -Name ToXml -Value { $this.PfXml }
    $out.Add($o)
  }
  if ($out.Count -eq 0) {
    $PSCmdlet.ThrowTerminatingError((New-Object System.Management.Automation.ErrorRecord ((New-Object System.Exception 'Nenašli sa žiadne udalosti zodpovedajúce zadaným kritériám výberu.'), 'NoMatchingEventsFound', 'ObjectNotFound', $null)))
  }
  $out | Sort-Object TimeCreated -Descending | Select-Object -First $MaxEvents
}
function Get-CimInstance {
  [CmdletBinding()] param([Parameter(Position = 0)] [string] $ClassName, [string] $Filter, [string[]] $Property, [string] $Namespace)
  Add-Content -LiteralPath $env:PF_CALLS -Value ('Get-CimInstance ' + $ClassName)
  if ($Filter) { Add-Content -LiteralPath $env:PF_CALLS -Value ('  Filter ' + $Filter) }
  if ($Property) { Add-Content -LiteralPath $env:PF_CALLS -Value ('  Property ' + ($Property -join ',')) }
  $cfg = Get-Content -Raw -Encoding UTF8 -LiteralPath $env:PF_CIM | ConvertFrom-Json
  if (@($cfg.fail) -contains $ClassName) { throw 'Neplatná trieda' }
  # Like WMI: a WQL date filter drops older instances, and -Property leaves
  # every other property empty.
  $after = $null
  if ($Filter) {
    if ($Filter -notmatch "^TimeGenerated >= '(\d{14}\.\d{6})\+000'$") { throw 'Neplatný dotaz' }
    $after = [datetime]::ParseExact($Matches[1], 'yyyyMMddHHmmss.ffffff', [Globalization.CultureInfo]::InvariantCulture, [Globalization.DateTimeStyles]'AssumeUniversal,AdjustToUniversal')
  }
  foreach ($r in @($cfg.$ClassName)) {
    if ($null -eq $r) { continue }
    $o = [ordered]@{}
    foreach ($p in $r.PSObject.Properties) { if ($p.Name -ne 'HoursAgo') { $o[$p.Name] = $(if (-not $Property -or $Property -contains $p.Name) { $p.Value }) } }
    $o['TimeGenerated'] = (Get-Date).AddHours(-[double]$r.HoursAgo)
    if ($after -and $o['TimeGenerated'].ToUniversalTime() -lt $after) { continue }
    [pscustomobject]$o
  }
}
function Get-ItemProperty {
  [CmdletBinding()] param([Parameter(Position = 0)] [string] $Path, [string] $Name)
  Add-Content -LiteralPath $env:PF_CALLS -Value ('Get-ItemProperty ' + $Path)
  $cfg = Get-Content -Raw -Encoding UTF8 -LiteralPath $env:PF_CIM | ConvertFrom-Json
  if ($Path -like '*CrashControl' -and $cfg.CrashControl) { return $cfg.CrashControl }
  return $null
}
foreach ($n in 'Get-WinEvent', 'Get-CimInstance', 'Get-ItemProperty') {
  if ((Get-Command $n -EA SilentlyContinue | Select-Object -First 1).CommandType -ne 'Function') { exit 97 }
}
"""


def _run(tmp_path: Path, action_id: str, events=(), cim=None, event_fail=False):
    calls = tmp_path / "calls.log"
    calls.write_text("", encoding="utf-8")
    (tmp_path / "events.json").write_text(json.dumps({"events": list(events), "fail": event_fail}), encoding="utf-8")
    (tmp_path / "cim.json").write_text(json.dumps(cim or {}), encoding="utf-8")
    stubs = tmp_path / "stubs.ps1"
    # BOM: Windows PowerShell 5.1 reads a BOM-less script as ANSI.
    stubs.write_text(STUBS, encoding="utf-8-sig")
    system_root = tmp_path / "Windows"
    system_root.mkdir(exist_ok=True)
    script = (
        "[Console]::OutputEncoding=[Text.Encoding]::UTF8; "
        f"$env:PF_CALLS = {_ps_quote(str(calls))}; $env:PF_EVENTS = {_ps_quote(str(tmp_path / 'events.json'))}; "
        f"$env:PF_CIM = {_ps_quote(str(tmp_path / 'cim.json'))}; $env:SystemRoot = {_ps_quote(str(system_root))}; "
        f". {_ps_quote(str(stubs))}; "
        + _action(action_id).command
    )
    result = subprocess.run(
        [_powershell_or_skip(), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert result.returncode != STUB_GUARD_EXIT, "a system cmdlet was not stubbed - refusing to read the real one"
    return result, calls.read_text(encoding="utf-8-sig").splitlines()


def _event(event_id, provider, hours_ago, data, message, level=2, log="System"):
    return {"Id": event_id, "ProviderName": provider, "LogName": log, "Level": level,
            "HoursAgo": hours_ago, "Data": data, "Message": message}


WER = "Microsoft-Windows-WER-SystemErrorReporting"
KP = "Microsoft-Windows-Kernel-Power"
WHEA = "Microsoft-Windows-WHEA-Logger"


def _bugcheck(hours_ago, param1, dump="C:\\Windows\\MEMORY.DMP", named=True, provider=WER):
    data = [["param1", param1], ["param2", dump], ["param3", "7f3c9e2a-0000-0000-0000-000000000000"]]
    if not named:
        data = [["", v] for _, v in data]
    return _event(1001, provider, hours_ago, data, SK_MESSAGE_BUGCHECK)


def _kp41(hours_ago, code, params=("0x0", "0x0", "0x0", "0x0"), provider=KP):
    data = [["BugcheckCode", str(code)]] + [[f"BugcheckParameter{i}", p] for i, p in enumerate(params, 1)]
    data += [["SleepInProgress", "0"], ["PowerButtonTimestamp", "0"]]
    return _event(41, provider, hours_ago, data, SK_MESSAGE_KP41, level=1)


def _blocks(stdout: str) -> list[str]:
    """Format-List blocks of the triage output, one per event."""
    return [b for b in re.split(r"\r?\n\s*\r?\n", stdout) if "Source" in b and "Cause" in b]


# --- crash_bugcheck_triage ----------------------------------------------------


def test_triage_decodes_the_stop_code_from_the_event_xml_not_the_slovak_message(tmp_path):
    ev = _bugcheck(5, "0x00000133 (0x0000000000000001, 0x0000000000001e00, 0xfffff8057a2fb320, 0x0000000000000000)")
    result, calls = _run(tmp_path, "crash_bugcheck_triage", events=[ev])
    assert result.returncode == 0, result.stdout + result.stderr
    out = result.stdout
    assert "0x00000133" in out
    assert "DPC_WATCHDOG_VIOLATION" in out
    assert "0x0000000000000001, 0x0000000000001e00, 0xfffff8057a2fb320, 0x0000000000000000" in out
    assert "C:\\Windows\\MEMORY.DMP" in out
    # The localized message says 0xD1 and a different dump path - both must be ignored.
    assert "DRIVER_IRQL_NOT_LESS_OR_EQUAL" not in out
    assert "nepravda" not in out and "Počítač" not in out
    assert calls == ["Get-WinEvent Id,LogName,StartTime"]


def test_triage_kernel_power_41_without_a_code_means_power_loss_or_hang(tmp_path):
    result, _ = _run(tmp_path, "crash_bugcheck_triage", events=[_kp41(3, 0)])
    assert result.returncode == 0, result.stdout + result.stderr
    (block,) = _blocks(result.stdout)
    assert "Kernel-Power 41" in block
    assert "NO_BUGCHECK" in block
    assert "power loss" in block
    # The message claims 0x124 - that must not leak in.
    assert "WHEA_UNCORRECTABLE_ERROR" not in result.stdout


def test_triage_kernel_power_41_with_a_decimal_bugcheck_code(tmp_path):
    # Kernel-Power stores BugcheckCode as a decimal number: 159 = 0x9F.
    ev = _kp41(3, 159, params=("0x3", "0xffffc10f9d6e4060", "0xfffff80567c6f750", "0xffffc10fa1b3e010"))
    result, _ = _run(tmp_path, "crash_bugcheck_triage", events=[ev])
    assert result.returncode == 0, result.stdout + result.stderr
    (block,) = _blocks(result.stdout)
    assert "0x0000009F" in block
    assert "DRIVER_POWER_STATE_FAILURE" in block
    assert "0x3, 0xffffc10f9d6e4060, 0xfffff80567c6f750, 0xffffc10fa1b3e010" in block


def test_triage_eventlog_6008_is_an_unexpected_shutdown_without_code(tmp_path):
    ev = _event(6008, "EventLog", 2, [["", "3:14:15"], ["", "1. 1. 2026"], ["", ""]], SK_MESSAGE_6008, level=2)
    result, _ = _run(tmp_path, "crash_bugcheck_triage", events=[ev])
    assert result.returncode == 0, result.stdout + result.stderr
    (block,) = _blocks(result.stdout)
    assert "EventLog 6008" in block
    assert "UNEXPECTED_SHUTDOWN" in block
    assert "3:14:15" not in result.stdout


@pytest.mark.parametrize("param1, name", [
    ("0x1000007e (0xffffffffc0000005, 0xfffff80712345678, 0xffff8a0e1c2d3e48, 0xffff8a0e1c2d3690)",
     "SYSTEM_THREAD_EXCEPTION_NOT_HANDLED"),
    ("0xc000021a (0x0, 0x0, 0x0, 0x0)", "STATUS_SYSTEM_PROCESS_TERMINATED"),
    ("0x00000124 (0x0000000000000000, 0xffffb00b8e3c8028, 0x00000000b2000000, 0x0000000000030005)",
     "WHEA_UNCORRECTABLE_ERROR"),
    ("0x00000999 (0x0, 0x0, 0x0, 0x0)", "UNKNOWN"),
])
def test_triage_code_variants(tmp_path, param1, name):
    # 0x1000007E is the "_M" variant of 0x7E and decodes the same way.
    result, _ = _run(tmp_path, "crash_bugcheck_triage", events=[_bugcheck(1, param1)])
    assert result.returncode == 0, result.stdout + result.stderr
    (block,) = _blocks(result.stdout)
    assert name in block


def test_triage_reads_unnamed_data_of_the_legacy_bugcheck_source(tmp_path):
    ev = _bugcheck(1, "0x0000003b (0x00000000c0000005, 0xfffff8012a3b4c5d, 0xffffd0012345e920, 0x0)",
                   dump="C:\\Windows\\Minidump\\092426-1234-01.dmp", named=False, provider="BugCheck")
    result, _ = _run(tmp_path, "crash_bugcheck_triage", events=[ev])
    assert result.returncode == 0, result.stdout + result.stderr
    (block,) = _blocks(result.stdout)
    assert "SYSTEM_SERVICE_EXCEPTION" in block
    assert "092426-1234-01.dmp" in block


def test_triage_ignores_other_providers_and_events_older_than_90_days(tmp_path):
    events = [
        _kp41(2, 0, provider="Some-Other-Provider"),
        _bugcheck(24 * 120, "0x0000000a (0x0, 0x2, 0x0, 0xfffff80000000000)"),
        _bugcheck(10, "0x00000050 (0xffff9f0000000000, 0x0, 0xfffff80000000000, 0x2)"),
    ]
    result, _ = _run(tmp_path, "crash_bugcheck_triage", events=events)
    assert result.returncode == 0, result.stdout + result.stderr
    blocks = _blocks(result.stdout)
    assert len(blocks) == 1
    assert "PAGE_FAULT_IN_NONPAGED_AREA" in blocks[0]
    assert "IRQL_NOT_LESS_OR_EQUAL" not in result.stdout


def test_triage_lists_newest_first_and_summarizes_per_code(tmp_path):
    events = [
        _bugcheck(50, "0x00000116 (0x1, 0x2, 0x3, 0x4)"),
        _bugcheck(5, "0x00000116 (0x1, 0x2, 0x3, 0x4)"),
        _kp41(5, 278, params=("0x1", "0x2", "0x3", "0x4")),
        _bugcheck(30, "0x0000001a (0x41790, 0x0, 0x0, 0x0)"),
    ]
    result, _ = _run(tmp_path, "crash_bugcheck_triage", events=events)
    assert result.returncode == 0, result.stdout + result.stderr
    blocks = _blocks(result.stdout)
    assert len(blocks) == 4
    times = [re.search(r"Time\s*:\s*(\S+ \S+)", b).group(1) for b in blocks]
    assert times == sorted(times, reverse=True)
    summary = result.stdout.split("=== Summary by stop code ===", 1)[1]
    # Two 0x116 crashes: the Kernel-Power 41 logged with the second one is
    # the same crash and is not counted again.
    assert re.search(r"^\s*2\s+0x00000116\s+VIDEO_TDR_FAILURE", summary, re.M), summary
    assert re.search(r"^\s*1\s+0x0000001A\s+MEMORY_MANAGEMENT", summary, re.M), summary


def test_triage_summary_folds_only_a_kernel_power_41_next_to_a_matching_bugcheck(tmp_path):
    events = [
        _bugcheck(5, "0x00000116 (0x1, 0x2, 0x3, 0x4)"),
        _kp41(5, 278, params=("0x1", "0x2", "0x3", "0x4")),  # same crash as the 1001 above
        _kp41(40, 278, params=("0x1", "0x2", "0x3", "0x4")),  # its 1001 was not logged - still a crash
        _kp41(6, 0),  # power loss - never folded
        _kp41(5, 0),
        _bugcheck(20, "0x00000133 (0x1, 0x2, 0x3, 0x4)"),
        _kp41(20, 278, params=("0x1", "0x2", "0x3", "0x4")),  # different code - not the same crash
    ]
    result, _ = _run(tmp_path, "crash_bugcheck_triage", events=events)
    assert result.returncode == 0, result.stdout + result.stderr
    assert len(_blocks(result.stdout)) == 7  # the per-event list keeps every event
    summary = result.stdout.split("=== Summary by stop code ===", 1)[1]
    assert re.search(r"^\s*3\s+0x00000116\s+VIDEO_TDR_FAILURE", summary, re.M), summary
    assert re.search(r"^\s*2\s+0x00000000\s+NO_BUGCHECK", summary, re.M), summary
    assert re.search(r"^\s*1\s+0x00000133\s+DPC_WATCHDOG_VIOLATION", summary, re.M), summary
    assert "counts such a pair (logged within 10 minutes) once" in result.stdout


def test_triage_without_events_reports_clean_and_exits_zero(tmp_path):
    result, _ = _run(tmp_path, "crash_bugcheck_triage", events=[])
    assert result.returncode == 0, result.stdout + result.stderr
    assert "No unexpected shutdowns or bugchecks in the System log in the last 90 days." in result.stdout


def test_triage_fails_when_the_log_cannot_be_read(tmp_path):
    result, _ = _run(tmp_path, "crash_bugcheck_triage", event_fail=True)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "Could not read the System event log" in result.stdout


# Every stop code named in the G06 task, plus a few more common ones.
REQUIRED_CODES = {
    "0x0000000A": "IRQL_NOT_LESS_OR_EQUAL", "0x0000007E": "SYSTEM_THREAD_EXCEPTION_NOT_HANDLED",
    "0x00000050": "PAGE_FAULT_IN_NONPAGED_AREA", "0x0000003B": "SYSTEM_SERVICE_EXCEPTION",
    "0x000000D1": "DRIVER_IRQL_NOT_LESS_OR_EQUAL", "0x00000133": "DPC_WATCHDOG_VIOLATION",
    "0x0000009F": "DRIVER_POWER_STATE_FAILURE", "0x00000124": "WHEA_UNCORRECTABLE_ERROR",
    "0x000000EF": "CRITICAL_PROCESS_DIED", "0x0000001E": "KMODE_EXCEPTION_NOT_HANDLED",
    "0x000000C2": "BAD_POOL_CALLER", "0x0000001A": "MEMORY_MANAGEMENT",
    "0x00000139": "KERNEL_SECURITY_CHECK_FAILURE", "0x00000116": "VIDEO_TDR_FAILURE",
    "0x00000119": "VIDEO_SCHEDULER_INTERNAL_ERROR", "0x0000007F": "UNEXPECTED_KERNEL_MODE_TRAP",
    "0x000000F4": "CRITICAL_OBJECT_TERMINATION", "0x00000154": "UNEXPECTED_STORE_EXCEPTION",
    "0x000000BE": "ATTEMPTED_WRITE_TO_READONLY_MEMORY", "0x000000C5": "DRIVER_CORRUPTED_EXPOOL",
}


def test_triage_decoding_table_covers_the_common_stop_codes():
    entries = re.findall(r"'(0x[0-9A-F]{8})' = '([A-Z0-9_]+)\|([^']+)'", _action("crash_bugcheck_triage").command)
    table = {code: name for code, name, _ in entries}
    assert len(table) == len(entries) >= 30
    for code, name in REQUIRED_CODES.items():
        assert table.get(code) == name, code
    # Every entry carries a one-line English cause.
    for _, _, cause in entries:
        assert len(cause) > 20 and cause.isascii(), cause


# --- whea_hardware_errors -----------------------------------------------------


def _whea(event_id, hours_ago, data, level=3):
    return _event(event_id, WHEA, hours_ago, data, SK_MESSAGE_WHEA, level=level)


PCIE_DATA = [["ErrorSource", "4"], ["PrimaryDeviceName", "PCI\\VEN_8086&DEV_A110&SUBSYS_00000000&REV_F0"],
             ["VendorID", "0x8086"], ["DeviceID", "0xa110"], ["Bus", "0x0"], ["Device", "0x1c"], ["Function", "0x0"]]
MCE_DATA = [["ErrorSource", "1"], ["ApicId", "2"], ["MCABank", "5"], ["MciStat", "0x9c00004000010005"]]


def test_whea_summarizes_by_component_and_flags_fatal_errors(tmp_path):
    events = [_whea(17, h, PCIE_DATA) for h in (1, 20, 100)]
    events += [_whea(19, h, MCE_DATA) for h in (3, 4)]
    events += [_whea(47, 6, [["ErrorSource", "1"]])]
    events += [_whea(18, 7, [["ErrorSource", "0"], ["ApicId", "4"], ["MCABank", "0"]], level=2)]
    result, calls = _run(tmp_path, "whea_hardware_errors", events=events)
    assert result.returncode == 0, result.stdout + result.stderr
    out = result.stdout
    assert "7 event(s)" in out
    assert re.search(r"^\s*3\s+corrected\s+PCI Express\s+PCI\\VEN_8086&DEV_A110", out, re.M), out
    assert re.search(r"^\s*2\s+corrected\s+Processor \(machine check\)\s+APIC 2, bank 5", out, re.M), out
    assert re.search(r"^\s*1\s+corrected\s+Memory", out, re.M), out
    assert re.search(r"^\s*1\s+FATAL\s+Processor \(machine check\)\s+APIC 4, bank 0", out, re.M), out
    assert "VERDICT: 1 uncorrectable hardware error(s)" in out
    # The Slovak message says "Pamäť" for every event - never echoed.
    assert "Pamäť" not in out and "Súčasť" not in out
    assert calls == ["Get-WinEvent LogName,ProviderName,StartTime"]


def test_whea_only_corrected_errors_get_the_corrected_verdict(tmp_path):
    result, _ = _run(tmp_path, "whea_hardware_errors", events=[_whea(17, 1, PCIE_DATA)])
    assert result.returncode == 0, result.stdout + result.stderr
    assert "VERDICT: only corrected errors" in result.stdout
    # A single row must still count as one (the rows are wrapped in @() for
    # Windows PowerShell 5.1, where a lone PSCustomObject has no .Count).
    assert "1 event(s)" in result.stdout


def test_whea_unknown_event_id_falls_back_to_the_error_source_number(tmp_path):
    result, _ = _run(tmp_path, "whea_hardware_errors", events=[_whea(20, 1, [["ErrorSource", "4"]], level=2)])
    assert result.returncode == 0, result.stdout + result.stderr
    assert re.search(r"FATAL\s+PCI Express \(event 20\)", result.stdout), result.stdout


def test_whea_without_events_and_older_than_30_days(tmp_path):
    result, _ = _run(tmp_path, "whea_hardware_errors", events=[_whea(17, 24 * 40, PCIE_DATA)])
    assert result.returncode == 0, result.stdout + result.stderr
    assert "No WHEA hardware errors in the System log in the last 30 days." in result.stdout


def test_whea_fails_when_the_log_cannot_be_read(tmp_path):
    result, _ = _run(tmp_path, "whea_hardware_errors", event_fail=True)
    assert result.returncode == 1, result.stdout + result.stderr


# --- reliability_history ------------------------------------------------------


def _metrics(values_by_day_ago):
    return [{"SystemStabilityIndex": v, "HoursAgo": days * 24 + 1} for days, v in values_by_day_ago]


def _record(source, event_id, product, hours_ago):
    return {"SourceName": source, "EventIdentifier": event_id, "ProductName": product, "HoursAgo": hours_ago,
            "Message": "Aplikácia word.exe prestala pracovať a bola zatvorená.", "LogFile": "Application"}


def test_reliability_reports_index_trend_and_top_failing_sources(tmp_path):
    cim = {
        "Win32_ReliabilityStabilityMetrics": _metrics(
            [(40, 1.0), (20, 10.0), (18, 9.5), (10, 9.0), (6, 5.0), (3, 3.25), (1, 2.5)]
        ),
        "Win32_ReliabilityRecords": [
            _record("Application Error", 1000, "explorer.exe", 5),
            _record("Application Error", 1000, "explorer.exe", 30),
            _record("Application Error", 1000, "explorer.exe", 50),
            _record("Application Hang", 1002, "outlook.exe", 8),
            _record("Microsoft-Windows-WER-SystemErrorReporting", 1001, "Windows", 12),
            # Qualifier bits in the high word must not hide the event ID.
            _record("EventLog", 0x80000000 + 6008, "Windows", 13),
            _record("MsiInstaller", 11707, "Adobe Reader", 9),  # a successful install - not a failure
            _record("Application Error", 1000, "old.exe", 24 * 45),  # outside the 30 days
        ],
    }
    result, calls = _run(tmp_path, "reliability_history", cim=cim)
    assert result.returncode == 0, result.stdout + result.stderr
    out = result.stdout
    assert "Latest: 2.5" in out
    assert "minimum: 2.5" in out
    assert "Trend: getting WORSE" in out
    # The 40-day-old 1.0 is outside the window.
    assert "1.0" not in out.split("Top failing", 1)[0].replace("10.0", "")
    top = out.split("=== Top failing sources", 1)[1]
    assert "6 failure record(s)" in top
    assert re.search(r"^\s*3\s+Application crash\s+explorer\.exe", top, re.M), top
    assert re.search(r"^\s*1\s+Application hang\s+outlook\.exe", top, re.M), top
    assert re.search(r"^\s*1\s+Windows crash \(bugcheck\)\s+Windows", top, re.M), top
    assert re.search(r"^\s*1\s+Unexpected shutdown\s+Windows", top, re.M), top
    assert "Adobe Reader" not in top and "old.exe" not in top
    assert "word.exe" not in out
    # The 30-day window is pushed into WQL and only the used properties are
    # fetched, so a year of records with their full Message never crosses over.
    assert [c for c in calls if c.startswith("Get-CimInstance")] == [
        "Get-CimInstance Win32_ReliabilityStabilityMetrics", "Get-CimInstance Win32_ReliabilityRecords"]
    filters = [c for c in calls if c.startswith("  Filter ")]
    assert len(filters) == 2 and filters[0] == filters[1]
    stamp = re.fullmatch(r"  Filter TimeGenerated >= '(\d{14})\.\d{6}\+000'", filters[0]).group(1)
    since = datetime.datetime.strptime(stamp, "%Y%m%d%H%M%S").replace(tzinfo=datetime.timezone.utc)
    age = datetime.datetime.now(datetime.timezone.utc) - since
    assert abs(age - datetime.timedelta(days=30)) < datetime.timedelta(minutes=5), age
    assert [c for c in calls if c.startswith("  Property ")] == [
        "  Property SystemStabilityIndex,TimeGenerated",
        "  Property SourceName,EventIdentifier,ProductName,TimeGenerated",
    ]


def test_reliability_without_data_says_so_and_exits_zero(tmp_path):
    result, _ = _run(tmp_path, "reliability_history", cim={})
    assert result.returncode == 0, result.stdout + result.stderr
    assert "No stability data" in result.stdout
    assert "0 failure record(s)" in result.stdout


def test_reliability_fails_when_cim_is_unavailable(tmp_path):
    result, _ = _run(tmp_path, "reliability_history", cim={"fail": ["Win32_ReliabilityRecords"]})
    assert result.returncode == 1, result.stdout + result.stderr
    assert "Could not read Win32_ReliabilityRecords" in result.stdout


# --- crash_dump_evidence ------------------------------------------------------


def _tree(root: Path) -> dict:
    return {str(p.relative_to(root)): (p.stat().st_size, p.stat().st_mtime_ns) for p in sorted(root.rglob("*"))}


def test_dump_evidence_lists_dumps_read_only(tmp_path):
    win = tmp_path / "Windows"
    (win / "Minidump").mkdir(parents=True)
    (win / "Minidump" / "092426-1234-01.dmp").write_bytes(b"\0" * 2048)
    (win / "Minidump" / "notes.txt").write_text("x")
    (win / "MEMORY.DMP").write_bytes(b"\0" * 4096)
    (win / "LiveKernelReports" / "WATCHDOG").mkdir(parents=True)
    (win / "LiveKernelReports" / "WATCHDOG" / "WD-20260920.dmp").write_bytes(b"\0" * 1024)
    before = _tree(win)
    result, calls = _run(tmp_path, "crash_dump_evidence")
    assert result.returncode == 0, result.stdout + result.stderr
    out = result.stdout
    for name in ("092426-1234-01.dmp", "MEMORY.DMP", "WD-20260920.dmp"):
        assert name in out
    assert "notes.txt" not in out
    assert "Total: 3 file(s)" in out
    assert "crash_dumps" in out
    assert "custom location" not in out
    assert _tree(win) == before
    assert calls == ["Get-ItemProperty HKLM:\\SYSTEM\\CurrentControlSet\\Control\\CrashControl"]


def test_dump_evidence_warns_when_dumps_are_turned_off(tmp_path):
    result, _ = _run(tmp_path, "crash_dump_evidence", cim={"CrashControl": {"CrashDumpEnabled": 0}})
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Dump setting: none" in result.stdout
    assert "WARNING: crash dumps are turned off" in result.stdout
    assert "No crash dump files found." in result.stdout


def test_dump_evidence_follows_a_custom_minidump_folder(tmp_path):
    custom = tmp_path / "Dumps"
    custom.mkdir()
    (custom / "mini.dmp").write_bytes(b"\0" * 10)
    cim = {"CrashControl": {"CrashDumpEnabled": 7, "MinidumpDir": str(custom)}}
    result, _ = _run(tmp_path, "crash_dump_evidence", cim=cim)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Dump setting: automatic memory dump" in result.stdout
    assert "mini.dmp" in result.stdout
    # crash_dumps deletes only the fixed %SystemRoot% paths - say so.
    assert f"Note: dumps are configured in a custom location ({custom});" in result.stdout
    assert "MEMORY.DMP);" not in result.stdout


def test_dump_evidence_treats_the_expanded_default_paths_as_default(tmp_path):
    # Get-ItemProperty expands the REG_EXPAND_SZ "%SystemRoot%\\Minidump" default.
    win = tmp_path / "Windows"
    cim = {"CrashControl": {"CrashDumpEnabled": 7, "MinidumpDir": str(win / "Minidump") + "\\",
                            "DumpFile": str(win / "MEMORY.DMP")}}
    result, _ = _run(tmp_path, "crash_dump_evidence", cim=cim)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "custom location" not in result.stdout


# --- static checks ------------------------------------------------------------


def test_triage_actions_are_safe_read_only_diagnostics():
    for action_id in TRIAGE_IDS:
        action = _action(action_id)
        assert action.risk == RiskLevel.SAFE, action_id
        assert action.undo_command is None and action.preview_command is None, action_id
        assert action.label_sk and action.label_en and action.description_sk and action.description_en
        for verb in ("Remove-Item", "Set-", "New-Item", "Copy-Item", "Move-Item", "Clear-EventLog", "wevtutil",
                     "Invoke-CimMethod", "Rename-Item", "Out-File", "Add-Content", ".Delete("):
            assert verb not in action.command, f"{action_id}: {verb}"


# The only text the commands may pattern-match: the structured 1001 param1
# value ("0x... (p1, p2, p3, p4)") and a hex/decimal number.
ALLOWED_MATCH_PATTERNS = {
    r"^\s*(0[xX][0-9A-Fa-f]{1,8})\s*\(([^)]*)\)",
    r"^0[xX]([0-9A-Fa-f]{1,8})$",
}


@pytest.mark.parametrize("action_id", TRIAGE_IDS)
def test_triage_never_matches_localized_text(action_id):
    command = _action(action_id).command
    # The event message, display names and exception text are translated.
    for forbidden in (".Message -", "Message |", "$_.Message", "$e.Message", "$x.Message", "FormatDescription",
                      "LevelDisplayName", "TaskDisplayName", "OpcodeDisplayName", "KeywordsDisplayNames",
                      "Select-String", "-imatch", "-cmatch", "-notmatch", "-replace", "[regex]", "IndexOf"):
        assert forbidden not in command, f"{action_id}: {forbidden}"
    assert set(re.findall(r"-match '([^']*)'", command)) <= ALLOWED_MATCH_PATTERNS
    # -like / -notlike only on FullyQualifiedErrorId (an id, not text).
    for m in re.finditer(r"(\S+)\s+-(?:not)?like\b", command):
        assert "FullyQualifiedErrorId" in m.group(1), f"{action_id}: {m.group(0)}"
    # The only Message ever touched is an exception's, and only to print it.
    for m in re.finditer(r"\.Message\b", command):
        assert command[max(0, m.start() - 20):m.start()].endswith("$_.Exception"), action_id


def test_event_based_actions_filter_by_id_and_provider_name():
    triage = _action("crash_bugcheck_triage").command
    assert "Id = 41, 1001, 6008" in triage
    for provider in ("Microsoft-Windows-Kernel-Power", "Microsoft-Windows-WER-SystemErrorReporting", "'EventLog'"):
        assert provider in triage
    assert "ToXml()" in triage
    whea = _action("whea_hardware_errors").command
    assert "ProviderName = 'Microsoft-Windows-WHEA-Logger'" in whea
    assert "ToXml()" in whea
    rel = _action("reliability_history").command
    assert "Win32_ReliabilityStabilityMetrics" in rel and "Win32_ReliabilityRecords" in rel


def test_crash_dumps_cleanup_tells_to_run_the_triage_first():
    cleanup = next(a for a in load_module(MODULES_DIR / "m02_cleanup" / "actions.yaml").actions if a.id == "crash_dumps")
    for action_id in ("crash_bugcheck_triage", "crash_dump_evidence"):
        triage = _action(action_id)
        assert f"'{triage.label_sk}'" in cleanup.description_sk, action_id
        assert f"'{triage.label_en}'" in cleanup.description_en, action_id


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


def test_triage_scripts_parse_without_powershell_7_only_syntax(tmp_path):
    scripts = {a: _action(a).command for a in TRIAGE_IDS}
    scripts_file = tmp_path / "scripts.json"
    scripts_file.write_text(json.dumps(scripts), encoding="utf-8")
    env = dict(os.environ, PFSCRIPTS_FILE=str(scripts_file))
    result = subprocess.run(
        [_powershell_or_skip(), "-NoProfile", "-NonInteractive", "-Command", PARSE_CHECKER],
        env=env, capture_output=True, text=True, timeout=120,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert "PARSE_DONE" in result.stdout, result.stdout + result.stderr
    assert [line for line in result.stdout.splitlines() if line.startswith("ERR")] == []
    for action_id in TRIAGE_IDS:
        assert "\n" not in _action(action_id).command
