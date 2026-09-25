import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from portablefix import preflight
from portablefix.models import ModuleCategory, RiskLevel
from portablefix.module_engine import load_module

CATALOG_PATH = Path(__file__).resolve().parent.parent / "Modules" / "m23_antivirus" / "actions.yaml"


def test_m23_catalog_loads_9_actions_in_antivirus_category():
    module = load_module(CATALOG_PATH)
    assert module.module_id == "m23_antivirus"
    assert module.category == ModuleCategory.ANTIVIRUS
    assert len(module.actions) == 9


def test_m23_catalog_covers_expected_ids():
    module = load_module(CATALOG_PATH)
    ids = {a.id for a in module.actions}
    assert ids == {
        "sec_defender_status",
        "sec_defender_update",
        "sec_defender_quickscan",
        "sec_defender_exclusions_list",
        "hard_defender_clear_exclusions",
        "sec_defender_threat_history",
        "sec_defender_fullscan",
        "hard_defender_pua_enable",
        "sec_defender_offline_scan",
    }


def test_m23_catalog_only_setting_changes_have_undo_command():
    module = load_module(CATALOG_PATH)
    by_id = {a.id: a for a in module.actions}
    for undoable in ("hard_defender_clear_exclusions", "hard_defender_pua_enable"):
        assert by_id[undoable].undo_command is not None, undoable
    for not_undoable in ("sec_defender_status", "sec_defender_update", "sec_defender_quickscan", "sec_defender_exclusions_list",
                         "sec_defender_threat_history", "sec_defender_fullscan", "sec_defender_offline_scan"):
        assert by_id[not_undoable].undo_command is None, not_undoable


def test_m23_catalog_risk_distribution():
    module = load_module(CATALOG_PATH)
    by_risk = {}
    for action in module.actions:
        by_risk.setdefault(action.risk, []).append(action.id)
    assert set(by_risk[RiskLevel.SAFE]) == {"sec_defender_status", "sec_defender_exclusions_list", "sec_defender_threat_history"}
    assert set(by_risk[RiskLevel.MODERATE]) == {
        "sec_defender_update", "sec_defender_quickscan", "hard_defender_clear_exclusions",
        "sec_defender_fullscan", "hard_defender_pua_enable",
    }
    assert set(by_risk[RiskLevel.REQUIRES_REBOOT]) == {"sec_defender_offline_scan"}
    assert RiskLevel.DESTRUCTIVE not in by_risk


def test_m23_clear_exclusions_fails_loudly_instead_of_faking_success():
    # Success in the report/history, and whether an undo is recorded, come
    # only from the exit code. Without administrator Get-MpPreference returns
    # "N/A: Must be an administrator to view exclusions" instead of the real
    # list - that placeholder used to overwrite the real backup, every
    # Remove-MpPreference failed non-terminating, and the action still
    # reported success with an undo that would add "N/A: ..." as exclusions.
    module = load_module(CATALOG_PATH)
    command = next(a for a in module.actions if a.id == "hard_defender_clear_exclusions").command
    assert "'N/A:*'" in command
    na_branch = command[command.index("'N/A:*'"):command.index("Set-Content")]
    assert "exit 1" in na_branch
    for kind in ("ExclusionPath", "ExclusionExtension", "ExclusionProcess", "ExclusionIpAddress"):
        assert f"Remove-MpPreference -{kind} $p.{kind} -EA Stop" in command, kind
    catch_branch = command[command.rindex("catch {"):]
    assert "exit 1" in catch_branch
    # Group-Policy-enforced exclusions survive Remove-MpPreference without an
    # error - only re-reading the preferences afterwards shows they're still there.
    after_removal = command[command.rindex("Remove-MpPreference"):command.rindex("catch {")]
    assert "Get-MpPreference -EA Stop" in after_removal
    assert "exit 1" in after_removal


def test_m23_clear_exclusions_undo_fails_loudly():
    module = load_module(CATALOG_PATH)
    undo = next(a for a in module.actions if a.id == "hard_defender_clear_exclusions").undo_command
    for key in ("Path", "Extension", "Process", "IpAddress"):
        assert f"Add-MpPreference -Exclusion{key} $b.{key} -EA Stop" in undo, key
    assert undo.index("try {") < undo.index("Add-MpPreference")
    assert "exit 1" in undo[undo.index("catch {"):]


# --- G08: threat history, full scan, Defender Offline, PUA protection -------
#
# The commands run below in PowerShell against stubbed Defender cmdlets:
# every cmdlet or tool they touch is shadowed by a function (functions win
# command lookup) and the script exits 97 unless each name really resolves
# to the stub, so a test run never scans, restarts or reconfigures the host.
# Stub errors carry Slovak messages on purpose - a command that keyed off
# English error text would fail.

STUB_GUARD_EXIT = 97
HISTORY_ID = "sec_defender_threat_history"
FULLSCAN_ID = "sec_defender_fullscan"
OFFLINE_ID = "sec_defender_offline_scan"
PUA_ID = "hard_defender_pua_enable"
HR_SERVICE_NOT_RUNNING = -2147416390  # 0x800106BA: the Defender service is not running
HR_ACCESS_DENIED = -2147024891  # 0x80070005
DENIED = f"throw [System.Runtime.InteropServices.COMException]::new('Prístup bol odmietnutý.', {HR_ACCESS_DENIED})"


def _powershell_or_skip() -> str:
    exe = os.environ.get("PORTABLEFIX_TEST_PWSH") or shutil.which("powershell") or shutil.which("pwsh")
    if not exe:
        pytest.skip("no PowerShell available")
    return exe


def _action(action_id):
    return next(a for a in load_module(CATALOG_PATH).actions if a.id == action_id)


def _ps_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _run_ps(tmp_path, stubs: dict, command: str, extra_env=None):
    """stubs: {name: (param block, body)}. Every stub gets [CmdletBinding()]
    so -EA Stop binds, and logs its call with the bound parameters (minus
    -ErrorAction)."""
    calls = tmp_path / "calls.txt"
    if calls.exists():
        calls.unlink()
    functions = [
        f"function {name} {{ [CmdletBinding()] param({params}) "
        f"Add-Content -LiteralPath $env:PF_CALLS -Value ('{name} ' + (($PSBoundParameters.Keys | Where-Object {{ $_ -ne 'ErrorAction' }} | Sort-Object | "
        "ForEach-Object { $_ + '=' + $PSBoundParameters[$_] }) -join ' ')); "
        f"{body} }}"
        for name, (params, body) in stubs.items()
    ]
    guard = (
        "foreach ($n in " + ", ".join(_ps_quote(n) for n in stubs) + ") { "
        "if ((Get-Command $n -EA SilentlyContinue | Select-Object -First 1).CommandType -ne 'Function') "
        f"{{ exit {STUB_GUARD_EXIT} }} }}"
    )
    env_values = {"PF_CALLS": str(calls), "ProgramData": str(tmp_path / "ProgramData"),
                  "PROCESSOR_ARCHITECTURE": "AMD64", "PROCESSOR_ARCHITEW6432": "", "SystemDrive": "C:"}
    env_values.update(extra_env or {})
    # Set inside the script, not in the child's environment - Windows
    # PowerShell may not even start with redirected system variables.
    env_lines = [f"$env:{name} = {_ps_quote(value)}" for name, value in env_values.items()]
    script = "; ".join(["[Console]::OutputEncoding=[Text.Encoding]::UTF8"] + env_lines + functions + [guard, command])
    result = subprocess.run(
        [_powershell_or_skip(), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script],
        env=dict(os.environ), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert result.returncode != STUB_GUARD_EXIT, "a system tool was not stubbed - refusing to run the real one"
    called = calls.read_text(encoding="utf-8").splitlines() if calls.exists() else []
    return result, called


def _called(calls, name):
    return [c for c in calls if c.split(" ", 1)[0] == name]


def _status(mode="Normal", **overrides):
    """Get-MpComputerStatus body. mode=None drops AMRunningMode (older
    Windows 10 builds do not report it); mode='throw' is Defender switched
    off by another antivirus - the cmdlet fails with 0x800106BA."""
    if mode == "throw":
        return ("throw [System.Runtime.InteropServices.COMException]::new("
                f"'Služba nie je spustená.', {HR_SERVICE_NOT_RUNNING})")
    values = {
        "AMServiceEnabled": "$true", "AntivirusEnabled": "$true", "RealTimeProtectionEnabled": "$true",
        "IsTamperProtected": "$true", "AntivirusSignatureAge": "1", "AntivirusSignatureVersion": "'1.417.12.0'",
        "AntivirusSignatureLastUpdated": "(Get-Date).AddDays(-1)", "AMEngineVersion": "'1.1.24080.9'",
        "AMProductVersion": "'4.18.24080.9'", "QuickScanAge": "2", "FullScanAge": "40",
    }
    if mode is not None:
        values["AMRunningMode"] = _ps_quote(mode)
    values.update(overrides)
    return "[pscustomobject]@{ " + "; ".join(f"{k} = {v}" for k, v in values.items()) + " }"


def _verdict(stdout: str) -> str:
    lines = [line for line in stdout.splitlines() if line.startswith("VERDICT: ")]
    assert len(lines) == 1, stdout
    return lines[0]


# --- threat history ---------------------------------------------------------

def _threat(tid, name, severity, active=False):
    return (f"[pscustomobject]@{{ ThreatID = {tid}; ThreatName = {_ps_quote(name)}; SeverityID = {severity}; "
            f"IsActive = ${str(active).lower()} }}")


def _detection(tid, days_ago, status=3, action=2, success=True, resources=("file:_C:\\Users\\jan\\Downloads\\setup.exe",)):
    res = "@(" + ", ".join(_ps_quote(r) for r in resources) + ")"
    return (f"[pscustomobject]@{{ ThreatID = {tid}; InitialDetectionTime = (Get-Date).AddDays(-{days_ago}); "
            f"ThreatStatusID = {status}; CleaningActionID = {action}; ActionSuccess = ${str(success).lower()}; "
            f"Resources = {res} }}")


def _run_history(tmp_path, status=None, threats=(), detections=(), threat_error=False, detection_error=False):
    stubs = {
        "Get-MpComputerStatus": ("", status or _status()),
        "Get-MpThreat": ("", DENIED if threat_error else "@(" + ", ".join(threats) + ")"),
        "Get-MpThreatDetection": ("", DENIED if detection_error else "@(" + ", ".join(detections) + ")"),
    }
    return _run_ps(tmp_path, stubs, _action(HISTORY_ID).command)


def test_history_joins_detections_with_threat_names_and_is_ok_when_all_remediated(tmp_path):
    result, _ = _run_history(
        tmp_path,
        threats=[_threat(2147519003, "Trojan:Win32/Wacatac.B!ml", 5)],
        detections=[_detection(2147519003, 10)],
    )
    assert result.returncode == 0, result.stdout + result.stderr
    out = result.stdout
    assert "Trojan:Win32/Wacatac.B!ml | severity: severe | status: quarantined | action: quarantine | action succeeded: True" in out
    assert "    file:_C:\\Users\\jan\\Downloads\\setup.exe" in out
    assert "--- Threat history (last 90 days): 1 detection(s), 1 in the last 30 days ---" in out
    assert "Running mode (AMRunningMode): Normal" in out
    assert "Real-time protection: on" in out
    assert "Tamper protection: on" in out
    assert "Signatures: 1.417.12.0, updated 1 day(s) ago" in out
    assert "Engine / product version: 1.1.24080.9 / 4.18.24080.9" in out
    assert "Last quick scan: 2 day(s) ago; last full scan: 40 day(s) ago" in out
    assert _verdict(out).startswith("VERDICT: OK - ")


def test_history_windows_are_90_and_30_days_and_newest_first(tmp_path):
    result, _ = _run_history(
        tmp_path,
        threats=[_threat(1, "Old:Win32/A", 1), _threat(2, "Mid:Win32/B", 2), _threat(3, "New:Win32/C", 4)],
        detections=[_detection(1, 120), _detection(2, 40), _detection(3, 3)],
    )
    assert result.returncode == 0, result.stdout + result.stderr
    out = result.stdout
    assert "2 detection(s), 1 in the last 30 days" in out
    assert "Old:Win32/A" not in out
    assert out.index("New:Win32/C | severity: high") < out.index("Mid:Win32/B | severity: moderate")


def test_history_unknown_threat_id_and_long_resource_list(tmp_path):
    resources = [f"file:_C:\\temp\\f{i}.exe" for i in range(8)]
    result, _ = _run_history(tmp_path, detections=[_detection(777, 2, resources=resources)])
    assert result.returncode == 0, result.stdout + result.stderr
    assert "ThreatID 777 | severity: unknown" in result.stdout
    assert "file:_C:\\temp\\f4.exe" in result.stdout
    assert "file:_C:\\temp\\f5.exe" not in result.stdout
    assert "... and 3 more" in result.stdout


def test_history_lists_at_most_50_detections(tmp_path):
    result, _ = _run_history(tmp_path, detections=[_detection(900 + i, 1) for i in range(53)])
    assert result.returncode == 0, result.stdout + result.stderr
    assert "53 detection(s), 53 in the last 30 days" in result.stdout
    assert len([line for line in result.stdout.splitlines() if " | severity: " in line]) == 50
    assert "... 3 older detection(s) not listed." in result.stdout


@pytest.mark.parametrize("status,action", [(103, 3), (1, 9), (102, 2), (105, 0)])
def test_history_unremediated_detection_is_attention(tmp_path, status, action):
    result, _ = _run_history(tmp_path, threats=[_threat(5, "PUA:Win32/Presenoker", 1)],
                             detections=[_detection(5, 1, status=status, action=action, success=False)])
    assert result.returncode == 0, result.stdout + result.stderr
    assert _verdict(result.stdout).startswith("VERDICT: ATTENTION - 1 detection(s) not remediated")


def test_history_still_active_threat_is_attention(tmp_path):
    result, _ = _run_history(tmp_path, threats=[_threat(6, "Backdoor:Win32/X", 5, active=True)],
                             detections=[_detection(6, 1)])
    assert _verdict(result.stdout).startswith(
        "VERDICT: ATTENTION - 0 detection(s) not remediated and 1 threat(s) still active")


def test_history_real_time_off_old_signatures_and_tamper_off_is_warning(tmp_path):
    result, _ = _run_history(tmp_path, status=_status(RealTimeProtectionEnabled="$false", AntivirusSignatureAge="12",
                                                      IsTamperProtected="$false", FullScanAge="4294967295"))
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Real-time protection: OFF" in result.stdout
    assert "Tamper protection: OFF" in result.stdout
    assert "last full scan: never" in result.stdout
    assert "No detections in the last 90 days." in result.stdout
    verdict = _verdict(result.stdout)
    assert verdict.startswith("VERDICT: WARNING - ")
    for reason in ("real-time protection is off", "signatures are 12 days old", "tamper protection is off"):
        assert reason in verdict


def test_history_never_updated_signatures_is_warning(tmp_path):
    result, _ = _run_history(tmp_path, status=_status(AntivirusSignatureAge="65535"))
    assert "signatures are missing" in _verdict(result.stdout)


@pytest.mark.parametrize("kwargs,source", [({"detection_error": True}, "Get-MpThreatDetection"),
                                           ({"threat_error": True}, "Get-MpThreat")])
def test_history_unreadable_history_is_warning_not_ok(tmp_path, kwargs, source):
    result, _ = _run_history(tmp_path, **kwargs)
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"Could not read the threat history ({source}:" in result.stdout
    assert "HRESULT 0x80070005" in result.stdout
    assert "the threat history could not be read" in _verdict(result.stdout)


@pytest.mark.parametrize("mode", ["Passive Mode", "SxS Passive Mode", "EDR Block Mode"])
def test_history_passive_defender_is_not_active_but_still_reports(tmp_path, mode):
    result, _ = _run_history(tmp_path, status=_status(mode), threats=[_threat(7, "Trojan:Win32/Y", 4)],
                             detections=[_detection(7, 5)])
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Trojan:Win32/Y" in result.stdout
    assert _verdict(result.stdout).startswith(
        f"VERDICT: NOT ACTIVE - Microsoft Defender is not the active antivirus on this PC (AMRunningMode: {mode})")


def test_history_older_windows_without_running_mode_uses_the_enabled_flags(tmp_path):
    result, _ = _run_history(tmp_path, status=_status(None))
    assert "Running mode (AMRunningMode): not reported by this Windows version" in result.stdout
    assert _verdict(result.stdout).startswith("VERDICT: OK")
    result, _ = _run_history(tmp_path, status=_status(None, AntivirusEnabled="$false"))
    assert _verdict(result.stdout).startswith("VERDICT: NOT ACTIVE")


def test_history_defender_switched_off_by_another_av_fails_with_hresult(tmp_path):
    result, calls = _run_history(tmp_path, status=_status("throw"))
    assert result.returncode == 2
    assert "HRESULT 0x800106BA" in result.stdout
    assert _verdict(result.stdout).startswith("VERDICT: NOT AVAILABLE")
    assert _called(calls, "Get-MpThreatDetection") == []


# --- full scan ----------------------------------------------------------------

def _run_fullscan(tmp_path, status=None, waits=2, final="Completed", start_error=False, receive_error=False,
                  detections="@()", command=None):
    # A fake job whose State stays Running until Wait-Job has been called
    # `waits` times - no real 60 s waits and no real background job.
    job = ("$j = [pscustomobject]@{ Id = 1 }; "
           "$j | Add-Member -MemberType ScriptProperty -Name State -Value "
           "{ if ($global:pfWaits -lt $global:pfWaitsNeeded) { 'Running' } else { $global:pfFinal } }; $j")
    stubs = {
        "Get-MpComputerStatus": ("", status or _status()),
        "Start-MpScan": ("$ScanType, [switch]$AsJob", DENIED if start_error else job),
        "Wait-Job": ("$Job, $Timeout", "$global:pfWaits++; $Job"),
        "Receive-Job": ("$Job", "Write-Error 'Sken zlyhal.'" if receive_error else ""),
        "Remove-Job": ("$Job, [switch]$Force", ""),
        "Get-MpThreatDetection": ("", detections),
    }
    prefix = f"$global:pfWaits = 0; $global:pfWaitsNeeded = {waits}; $global:pfFinal = {_ps_quote(final)}; "
    return _run_ps(tmp_path, stubs, prefix + (command or _action(FULLSCAN_ID).command))


def test_fullscan_runs_as_job_prints_a_heartbeat_per_minute_and_succeeds(tmp_path):
    result, calls = _run_fullscan(tmp_path, waits=3)
    assert result.returncode == 0, result.stdout + result.stderr
    assert _called(calls, "Start-MpScan") == ["Start-MpScan AsJob=True ScanType=FullScan"]
    waits = _called(calls, "Wait-Job")
    assert len(waits) == 3 and all("Timeout=60" in c for c in waits)
    assert len(re.findall(r"Full scan still running - \d+ min elapsed", result.stdout)) == 2
    assert _called(calls, "Remove-Job")
    assert "Full scan finished in 0 min. Detections during the scan: 0." in result.stdout


def test_fullscan_counts_only_detections_since_the_start_and_flags_unremediated(tmp_path):
    old = "(Get-Date).AddDays(-10)"
    new = "(Get-Date).AddMinutes(1)"
    detections = (
        f"@([pscustomobject]@{{ InitialDetectionTime = {old}; LastThreatStatusChangeTime = {old}; ActionSuccess = $false }}, "
        f"[pscustomobject]@{{ InitialDetectionTime = {new}; LastThreatStatusChangeTime = {new}; ActionSuccess = $true }}, "
        f"[pscustomobject]@{{ InitialDetectionTime = {old}; LastThreatStatusChangeTime = {new}; ActionSuccess = $false }})"
    )
    result, _ = _run_fullscan(tmp_path, detections=detections)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Detections during the scan: 2." in result.stdout
    assert "WARNING: 1 detection(s) were NOT remediated" in result.stdout


@pytest.mark.parametrize("final", ["Failed", "Stopped"])
def test_fullscan_job_that_does_not_complete_fails(tmp_path, final):
    result, _ = _run_fullscan(tmp_path, final=final)
    assert result.returncode == 1
    assert f"The full scan did not finish (job state: {final}" in result.stdout


def test_fullscan_job_error_fails_even_if_state_is_completed(tmp_path):
    result, _ = _run_fullscan(tmp_path, receive_error=True)
    assert result.returncode == 1
    assert "The full scan did not finish (job state: Completed" in result.stdout


def test_fullscan_start_failure_exits_one(tmp_path):
    result, calls = _run_fullscan(tmp_path, start_error=True)
    assert result.returncode == 1
    assert "Could not start the full scan" in result.stdout
    assert "HRESULT 0x80070005" in result.stdout
    assert _called(calls, "Wait-Job") == []


@pytest.mark.parametrize("status,code", [(_status("Passive Mode"), 3), (_status("throw"), 2),
                                         (_status(None, AMServiceEnabled="$false"), 3)])
def test_fullscan_refuses_when_defender_is_not_the_active_antivirus(tmp_path, status, code):
    result, calls = _run_fullscan(tmp_path, status=status)
    assert result.returncode == code, result.stdout + result.stderr
    assert _called(calls, "Start-MpScan") == []
    assert "Nothing was started" in result.stdout or "HRESULT 0x800106BA" in result.stdout


@pytest.mark.parametrize("status,expected", [
    (_status(), "Would start a Microsoft Defender full scan"),
    (_status("Passive Mode"), "Would refuse: Microsoft Defender is not the active antivirus"),
])
def test_fullscan_preview_starts_nothing(tmp_path, status, expected):
    result, calls = _run_fullscan(tmp_path, status=status, command=_action(FULLSCAN_ID).preview_command)
    assert result.returncode == 0, result.stdout + result.stderr
    assert expected in result.stdout
    assert "Last full scan: 40 day(s) ago." in result.stdout
    assert _called(calls, "Start-MpScan") == []


def test_fullscan_timeouts_outlast_the_heartbeat_and_a_long_scan():
    action = _action(FULLSCAN_ID)
    # Heartbeat every 60 s - the watchdog must allow several missed ones.
    assert "-Timeout 60" in action.command and "-AsJob" in action.command
    assert action.inactivity_timeout_sec >= 300
    assert action.hard_cap_sec >= 6 * 3600
    # Long enough that the pre-flight treats it as a long action (battery).
    assert preflight.is_long_action(action)
    assert action.changes_system is False


# --- Defender Offline ---------------------------------------------------------

def _run_offline(tmp_path, status=None, arch="AMD64", wow_arch="", update_error=False, bitlocker="off",
                 wdo_error=False, command=None):
    bitlocker_body = {
        "off": "[pscustomobject]@{ ProtectionStatus = 0; KeyProtector = @() }",
        "on": ("[pscustomobject]@{ ProtectionStatus = 1; KeyProtector = @("
               "[pscustomobject]@{ KeyProtectorType = 'Tpm'; KeyProtectorId = '{11111111-2222-3333-4444-555555555555}' }, "
               "[pscustomobject]@{ KeyProtectorType = 'RecoveryPassword'; KeyProtectorId = '{AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE}' }) }"),
        "denied": DENIED,
    }[bitlocker]
    stubs = {
        "Get-MpComputerStatus": ("", status or _status()),
        "Update-MpSignature": ("", DENIED if update_error else ""),
        "Get-BitLockerVolume": ("$MountPoint", bitlocker_body),
        "Start-MpWDOScan": ("", DENIED if wdo_error else ""),
    }
    return _run_ps(tmp_path, stubs, command or _action(OFFLINE_ID).command,
                   extra_env={"PROCESSOR_ARCHITECTURE": arch, "PROCESSOR_ARCHITEW6432": wow_arch})


def test_offline_updates_signatures_warns_then_starts(tmp_path):
    result, calls = _run_offline(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert [c.split(" ", 1)[0] for c in calls] == ["Get-MpComputerStatus", "Update-MpSignature",
                                                  "Get-BitLockerVolume", "Start-MpWDOScan"]
    out = result.stdout
    assert out.index("Signatures updated.") < out.index("WARNING: Windows restarts IMMEDIATELY") < out.index(
        "Windows is restarting now")
    assert "BitLocker" not in out


def test_offline_prints_the_bitlocker_recovery_key_id(tmp_path):
    result, calls = _run_offline(tmp_path, bitlocker="on")
    assert result.returncode == 0, result.stdout + result.stderr
    assert _called(calls, "Get-BitLockerVolume") == ["Get-BitLockerVolume MountPoint=C:"]
    assert "Recovery key ID: {AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE}" in result.stdout
    assert "11111111-2222" not in result.stdout


def test_offline_unreadable_bitlocker_state_does_not_block(tmp_path):
    result, calls = _run_offline(tmp_path, bitlocker="denied")
    assert result.returncode == 0, result.stdout + result.stderr
    assert _called(calls, "Start-MpWDOScan")


def test_offline_signature_update_failure_warns_and_still_starts(tmp_path):
    result, calls = _run_offline(tmp_path, update_error=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "WARNING: could not update signatures" in result.stdout
    assert _called(calls, "Start-MpWDOScan")


def test_offline_start_failure_exits_one_and_says_no_restart(tmp_path):
    result, _ = _run_offline(tmp_path, wdo_error=True)
    assert result.returncode == 1
    assert "Could not start Microsoft Defender Offline" in result.stdout
    assert "the PC was not restarted" in result.stdout
    assert "Windows is restarting now" not in result.stdout


@pytest.mark.parametrize("arch,wow", [("ARM64", ""), ("x86", "ARM64")])
def test_offline_refuses_on_arm64(tmp_path, arch, wow):
    result, calls = _run_offline(tmp_path, arch=arch, wow_arch=wow)
    assert result.returncode == 4
    assert "not supported on ARM64" in result.stdout
    assert _called(calls, "Start-MpWDOScan") == [] and _called(calls, "Update-MpSignature") == []


@pytest.mark.parametrize("status,code", [(_status("Passive Mode"), 3), (_status("throw"), 2)])
def test_offline_refuses_when_defender_is_not_the_active_antivirus(tmp_path, status, code):
    result, calls = _run_offline(tmp_path, status=status)
    assert result.returncode == code
    assert _called(calls, "Start-MpWDOScan") == [] and _called(calls, "Update-MpSignature") == []


@pytest.mark.parametrize("status,arch,expected", [
    (_status(), "AMD64", "Would update Defender signatures, then RESTART THE PC IMMEDIATELY"),
    (_status("Passive Mode"), "AMD64", "Would refuse: Microsoft Defender is not the active antivirus"),
    (_status(), "ARM64", "Would refuse: Microsoft Defender Offline is not supported on ARM64"),
])
def test_offline_preview_never_restarts_or_updates(tmp_path, status, arch, expected):
    result, calls = _run_offline(tmp_path, status=status, arch=arch, bitlocker="on",
                                 command=_action(OFFLINE_ID).preview_command)
    assert result.returncode == 0, result.stdout + result.stderr
    assert expected in result.stdout
    assert "BitLocker protects C:" in result.stdout
    assert _called(calls, "Start-MpWDOScan") == [] and _called(calls, "Update-MpSignature") == []


def test_offline_is_opt_in_last_and_warns_about_the_restart():
    module = load_module(CATALOG_PATH)
    action = module.actions[-1]
    # Last in the catalog: the batch runs in catalog order and this one
    # restarts Windows, so no other action of this module is cut off by it.
    assert action.id == OFFLINE_ID
    assert action.risk == RiskLevel.REQUIRES_REBOOT
    assert action.exclude_from_select_all is True
    assert action.preview_command
    assert "OKAMŽITE" in action.description_sk and "IMMEDIATELY" in action.description_en
    assert "ulož" in action.description_sk and "save" in action.description_en
    # The warning is printed (and so logged) before the restart is triggered.
    assert action.command.index("Windows restarts IMMEDIATELY") < action.command.index("Start-MpWDOScan -EA Stop")


# --- PUA protection -----------------------------------------------------------

def _run_pua(tmp_path, initial=0, status=None, locked=False, set_error=False, read_error=False, command=None):
    """Defender's PUAProtection lives in a state file so the action and its
    undo (separate processes) see the same value. locked: Group Policy wins -
    Set-MpPreference succeeds but the effective value does not change."""
    state = tmp_path / "pua_state.txt"
    if not state.exists():
        state.write_text(str(initial), encoding="ascii")
    names = "@{ 'Disabled' = 0; 'Enabled' = 1; 'AuditMode' = 2 }"
    set_body = DENIED if set_error else (
        "" if locked else f"Set-Content -LiteralPath $env:PF_PUA_STATE -Value ({names}[[string]$PUAProtection])"
    )
    stubs = {
        "Get-MpComputerStatus": ("", status or _status()),
        "Get-MpPreference": ("", DENIED if read_error else
                             "[pscustomobject]@{ PUAProtection = [byte](Get-Content -LiteralPath $env:PF_PUA_STATE -Raw) }"),
        "Set-MpPreference": ("$PUAProtection", set_body),
        "icacls": ("[Parameter(ValueFromRemainingArguments = $true)] $Rest", ""),
    }
    return _run_ps(tmp_path, stubs, command or _action(PUA_ID).command, extra_env={"PF_PUA_STATE": str(state)})


def _pua_backup(tmp_path) -> Path:
    return tmp_path / "ProgramData" / "PortableFix" / "hard_defender_pua_enable_backup.json"


def _pua_state(tmp_path) -> int:
    return int((tmp_path / "pua_state.txt").read_text(encoding="ascii").strip())


@pytest.mark.parametrize("initial,name", [(0, "Disabled"), (2, "AuditMode")])
def test_pua_enable_backs_up_previous_value_and_undo_restores_it(tmp_path, initial, name):
    result, calls = _run_pua(tmp_path, initial=initial)
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"PUA protection enabled (was: {name})" in result.stdout
    assert _called(calls, "Set-MpPreference") == ["Set-MpPreference PUAProtection=Enabled"]
    assert json.loads(_pua_backup(tmp_path).read_text(encoding="utf-8-sig"))["PUAProtection"] == initial
    assert _pua_state(tmp_path) == 1
    # The backup folder is locked down before anything is written to it.
    icacls = _called(calls, "icacls")
    assert len(icacls) == 1
    for part in ("/inheritance:r", "*S-1-5-32-544:(OI)(CI)F", "*S-1-5-18:(OI)(CI)F"):
        assert part in icacls[0]

    undo, undo_calls = _run_pua(tmp_path, command=_action(PUA_ID).undo_command)
    assert undo.returncode == 0, undo.stdout + undo.stderr
    assert _called(undo_calls, "Set-MpPreference") == [f"Set-MpPreference PUAProtection={name}"]
    assert f"restored to its value before PortableFix enabled it ({name})" in undo.stdout
    assert _pua_state(tmp_path) == initial
    # Applied once - a later undo must not replay an old backup.
    assert not _pua_backup(tmp_path).exists()
    again, again_calls = _run_pua(tmp_path, command=_action(PUA_ID).undo_command)
    assert again.returncode == 0
    assert "No backup found - nothing changed." in again.stdout
    assert _called(again_calls, "Set-MpPreference") == []


def test_pua_already_enabled_changes_nothing_and_keeps_the_first_backup(tmp_path):
    first, _ = _run_pua(tmp_path, initial=0)
    assert first.returncode == 0, first.stdout + first.stderr
    second, calls = _run_pua(tmp_path)
    assert second.returncode == 0, second.stdout + second.stderr
    assert "already enabled - nothing changed" in second.stdout
    assert _called(calls, "Set-MpPreference") == []
    assert json.loads(_pua_backup(tmp_path).read_text(encoding="utf-8-sig"))["PUAProtection"] == 0


def test_pua_enforced_by_policy_fails_and_keeps_the_backup(tmp_path):
    result, _ = _run_pua(tmp_path, initial=0, locked=True)
    assert result.returncode == 1
    assert "still Disabled after the change" in result.stdout
    assert "Group Policy" in result.stdout
    assert _pua_backup(tmp_path).exists()


@pytest.mark.parametrize("kwargs", [{"set_error": True}, {"read_error": True}])
def test_pua_cmdlet_failure_exits_one(tmp_path, kwargs):
    result, _ = _run_pua(tmp_path, initial=0, **kwargs)
    assert result.returncode == 1
    assert "Could not enable PUA protection" in result.stdout
    assert "HRESULT 0x80070005" in result.stdout


@pytest.mark.parametrize("status,code", [(_status("Passive Mode"), 3), (_status("throw"), 2)])
def test_pua_refuses_when_defender_is_not_the_active_antivirus(tmp_path, status, code):
    result, calls = _run_pua(tmp_path, initial=0, status=status)
    assert result.returncode == code
    assert _called(calls, "Set-MpPreference") == [] and _called(calls, "Get-MpPreference") == []
    assert not _pua_backup(tmp_path).exists()


@pytest.mark.parametrize("content", ['{"PUAProtection": 7}', '{"PUAProtection": "0"}', '{}', 'not json'])
def test_pua_undo_rejects_a_bad_backup_without_changing_anything(tmp_path, content):
    _pua_backup(tmp_path).parent.mkdir(parents=True)
    _pua_backup(tmp_path).write_text(content, encoding="utf-8")
    result, calls = _run_pua(tmp_path, initial=1, command=_action(PUA_ID).undo_command)
    assert result.returncode == 1
    assert _called(calls, "Set-MpPreference") == []
    assert _pua_state(tmp_path) == 1


def test_pua_undo_failure_exits_one_and_keeps_the_backup(tmp_path):
    _pua_backup(tmp_path).parent.mkdir(parents=True)
    _pua_backup(tmp_path).write_text('{"PUAProtection": 0}', encoding="utf-8")
    result, _ = _run_pua(tmp_path, initial=1, set_error=True, command=_action(PUA_ID).undo_command)
    assert result.returncode == 1
    assert "Could not restore PUA protection" in result.stdout
    assert _pua_backup(tmp_path).exists()


@pytest.mark.parametrize("initial,status,expected", [
    (0, None, "PUA protection is now Disabled - would back it up"),
    (1, None, "already enabled - would change nothing"),
    (0, _status("Passive Mode"), "Would refuse: Microsoft Defender is not the active antivirus"),
])
def test_pua_preview_changes_nothing(tmp_path, initial, status, expected):
    result, calls = _run_pua(tmp_path, initial=initial, status=status, command=_action(PUA_ID).preview_command)
    assert result.returncode == 0, result.stdout + result.stderr
    assert expected in result.stdout
    assert _called(calls, "Set-MpPreference") == [] and _called(calls, "icacls") == []
    assert not _pua_backup(tmp_path).exists()


# --- static checks -----------------------------------------------------------

G08_IDS = (HISTORY_ID, FULLSCAN_ID, OFFLINE_ID, PUA_ID)


def _scripts(action):
    return [s for s in (action.command, action.undo_command, action.preview_command) if s]


@pytest.mark.parametrize("action_id", G08_IDS)
def test_g08_never_decides_on_localized_text(action_id):
    for script in _scripts(_action(action_id)):
        for forbidden in ("-match", "-like", "-imatch", "-cmatch", "Select-String", "-replace", "-split",
                          "IndexOf", "[regex]", "StartsWith(", "EndsWith("):
            assert forbidden not in script, f"{action_id}: {forbidden}"
        # Exception messages (localized) are only ever printed, never tested.
        conditions = re.findall(r"\b(?:if|elseif|while) \((.*?)\) \{", script)
        assert conditions
        for condition in conditions:
            assert "Message" not in condition and "FullyQualifiedErrorId" not in condition, condition


def _code_only(script: str) -> str:
    # Single-quoted literals are text for the technician ("would start
    # Start-MpScan ..."), not something the script runs.
    return re.sub(r"'(?:[^']|'')*'", "''", script)


def test_g08_read_only_scripts_do_not_write():
    history = _action(HISTORY_ID)
    assert history.risk == RiskLevel.SAFE
    for verb in ("Set-", "Add-Mp", "Remove-", "New-Item", "Start-Mp", "Update-Mp", "icacls"):
        assert verb not in _code_only(history.command), verb
    for action_id in (FULLSCAN_ID, OFFLINE_ID, PUA_ID):
        preview = _code_only(_action(action_id).preview_command)
        assert "Get-MpComputerStatus" in preview
        for verb in ("Set-", "Start-Mp", "Update-Mp", "New-Item", "icacls", "Remove-"):
            assert verb not in preview, f"{action_id}: {verb}"


# Target is Windows PowerShell 5.1: the scripts must parse, and none may use
# PS7-only syntax (??, ?., ternary, &&/||) that pwsh accepts but 5.1 rejects.
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


def test_m23_catalog_scripts_parse_without_powershell_7_only_syntax(tmp_path):
    module = load_module(CATALOG_PATH)
    scripts = {
        f"{a.id}.{field}": getattr(a, field)
        for a in module.actions
        for field in ("command", "undo_command", "preview_command")
        if getattr(a, field)
    }
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
