"""Research G02: the catalog diagnostics that report structured findings.

Each command runs in the real powershell.exe with the system cmdlets it
reads shadowed by functions, so the verdict logic is tested on the words
Windows uses in every language (enum and status names), never on
localized text. The five former problem_keywords rules are among them."""

from pathlib import Path

import pytest

from portablefix import pfjson
from portablefix.executor import PlanRun, build_execution_plan
from portablefix.module_engine import load_all_modules

REPO = Path(__file__).resolve().parent.parent
_ACTIONS = {a.id: a for m in load_all_modules(REPO / "Modules")[0] for a in m.actions}


def _run(action_id: str, stubs: str):
    run = PlanRun(build_execution_plan(stubs + "; " + _ACTIONS[action_id].command, dry_run=False),
                  inactivity_timeout_sec=60, hard_cap_sec=120)
    code = run.run()
    return code, run.captured_output, pfjson.findings(run.pfjson)


def _one(findings, fid):
    assert [f["id"] for f in findings] == [fid], findings
    return findings[0]


def test_no_action_uses_keyword_rules_any_more():
    assert not any(getattr(a, "problem_keywords", None) for a in _ACTIONS.values())


@pytest.mark.parametrize("props,severity", [
    ("EnableLUA = 0", "critical"),
    ("EnableLUA = 1; PromptOnSecureDesktop = 0", "attention"),
    ("EnableLUA = 1; PromptOnSecureDesktop = 1; ConsentPromptBehaviorAdmin = 5", "ok"),
])
def test_uac_status_finding(props, severity):
    code, _, findings = _run("sec_uac_status", "function Get-ItemProperty { [CmdletBinding()] param($Path) [pscustomobject]@{ " + props + " } }")
    assert code == 0
    finding = _one(findings, "security.uac")
    assert finding["severity"] == severity and finding["area"] == "security"
    assert finding["fix"] == ["hard_uac_restore_default"]


def test_problem_devices_finding_counts_devices_in_error():
    stubs = (
        "function Get-PnpDevice { [CmdletBinding()] param([switch]$PresentOnly) "
        "@([pscustomobject]@{ Status = 'Error'; Class = 'Net'; FriendlyName = 'Sieťový adaptér'; InstanceId = 'PCI\\X' }, "
        "[pscustomobject]@{ Status = 'Unknown'; Class = 'USB'; FriendlyName = 'U'; InstanceId = 'USB\\Y' }) }; "
        "function Get-PnpDeviceProperty { [CmdletBinding()] param($InstanceId, $KeyName) [pscustomobject]@{ Data = 28 } }"
    )
    code, lines, findings = _run("drv_problem_devices", stubs)
    assert code == 0
    finding = _one(findings, "hardware.problem_devices")
    assert finding["severity"] == "attention" and finding["msg_en"].startswith("Devices in an error state: 1")
    assert finding["fix"] == ["drv_restart_problem_devices"]
    assert any("Sieťový adaptér" in line for line in lines)


def test_gpu_finding_recognises_the_basic_display_driver_by_its_inf_not_its_localized_name():
    stubs = (
        "function Get-CimInstance { [CmdletBinding()] param($ClassName) @([pscustomobject]@{ Name = 'Základný grafický adaptér spoločnosti Microsoft'; "
        "InfFilename = 'display.inf'; Status = 'OK'; ConfigManagerErrorCode = 0; DriverVersion = '10.0'; DriverDate = $null; AdapterRAM = 0; "
        "CurrentHorizontalResolution = 1024; CurrentVerticalResolution = 768; CurrentRefreshRate = 60 }) }; "
        "function Get-ItemProperty { [CmdletBinding()] param($Path) }"
    )
    code, _, findings = _run("drv_gpu_info", stubs)
    assert code == 0
    finding = _one(findings, "hardware.gpu_driver")
    assert finding["severity"] == "attention" and finding["fix"] == ["drv_install_updates"]


@pytest.mark.parametrize("output,exit_code,severity", [
    ("safeboot                Minimal", 0, "attention"),
    ("description             Windows 11", 0, "ok"),
    ("Prístup bol odmietnutý.", 1, None),
])
def test_safe_mode_finding_and_unknown_without_admin(output, exit_code, severity):
    stubs = f"function bcdedit.exe {{ '{output}'; $global:LASTEXITCODE = {exit_code} }}"
    code, _, findings = _run("boot_safe_mode_status", stubs)
    assert code == 0
    if severity is None:
        assert findings == []
    else:
        assert _one(findings, "boot.safe_mode")["severity"] == severity


def test_pawnio_finding():
    code, _, findings = _run("pawnio_status_report", "function Get-ItemProperty { [CmdletBinding()] param($Path) }")
    assert code == 0
    finding = _one(findings, "hardware.sensors")
    assert finding["severity"] == "attention" and finding["fix"] == ["pawnio_install"]


def test_pending_reboot_finding():
    stubs = ("function Test-Path { param($Path) $Path -like '*RebootPending' }; "
             "function Get-ItemProperty { [CmdletBinding()] param($Path, $Name) }")
    code, _, findings = _run("pending_reboot", stubs)
    assert code == 0
    finding = _one(findings, "updates.pending_reboot")
    assert finding["severity"] == "attention" and "Component Based Servicing" in finding["msg_en"]


@pytest.mark.parametrize("health,severity", [("Unhealthy", "critical"), ("Warning", "attention"), ("Healthy", "ok")])
def test_physical_disk_finding(health, severity):
    stubs = ("function Get-PhysicalDisk { @([pscustomobject]@{ FriendlyName = 'Disk A'; MediaType = 'SSD'; HealthStatus = '"
             + health + "'; OperationalStatus = 'OK'; Size = 1GB }) }")
    code, _, findings = _run("physical_disks", stubs)
    assert code == 0
    assert _one(findings, "disk.health")["severity"] == severity


def test_defender_finding_is_critical_only_without_any_other_antivirus():
    mp = ("function Get-MpComputerStatus { [CmdletBinding()] param() [pscustomobject]@{ AMServiceEnabled = $true; "
          "RealTimeProtectionEnabled = $false; AntivirusEnabled = $false; AntivirusSignatureLastUpdated = (Get-Date); QuickScanAge = 1; FullScanAge = 1 } }")
    none = "function Get-CimInstance { [CmdletBinding()] param($Namespace, $ClassName) }"
    other = ("function Get-CimInstance { [CmdletBinding()] param($Namespace, $ClassName) "
             "@([pscustomobject]@{ displayName = 'Contoso AV'; productState = 266240 }) }")
    _, _, findings = _run("sec_defender_status", mp + "; " + none)
    assert _one(findings, "security.antivirus")["severity"] == "critical"
    _, _, findings = _run("sec_defender_status", mp + "; " + other)
    finding = _one(findings, "security.antivirus")
    assert finding["severity"] == "ok" and "Contoso AV" in finding["msg_en"]


@pytest.mark.parametrize("action_id,fid", [("battery_wear", "battery.wear"), ("crash_bugcheck_triage", "crashes.recent")])
def test_real_run_reports_a_well_formed_finding_or_none(action_id, fid):
    # Read-only on the real machine: whatever the verdict, the finding (if
    # the PC has a battery / the log is readable) must follow the format.
    code, _, findings = _run(action_id, "$null")
    for finding in findings:
        assert finding["id"] == fid and finding["severity"] in pfjson.SEVERITIES
