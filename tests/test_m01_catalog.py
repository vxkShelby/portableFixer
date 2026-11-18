from pathlib import Path

from portablefix.models import ModuleCategory, RiskLevel
from portablefix.module_engine import load_module

CATALOG_PATH = Path(__file__).resolve().parent.parent / "Modules" / "m01_diagnostics" / "actions.yaml"


def test_m01_catalog_loads_31_actions_in_diagnostics_category():
    module = load_module(CATALOG_PATH)
    assert module.module_id == "m01_diagnostics"
    assert module.category == ModuleCategory.DIAGNOSTICS
    assert len(module.actions) == 31


def test_m01_catalog_all_actions_safe_readonly():
    module = load_module(CATALOG_PATH)
    for action in module.actions:
        assert action.risk == RiskLevel.SAFE
        assert action.undo_command is None


def test_m01_catalog_action_ids_are_unique():
    module = load_module(CATALOG_PATH)
    ids = [a.id for a in module.actions]
    assert len(ids) == len(set(ids))


def test_m01_catalog_covers_core_system_info_actions():
    module = load_module(CATALOG_PATH)
    ids = {a.id for a in module.actions}
    assert {
        "os_info", "computer_info", "bios_info", "cpu_info", "memory_info",
        "volumes", "physical_disks", "recent_hotfixes", "defender_status",
        "top_cpu_processes", "pending_reboot", "eventlog_critical_7d",
        "bsod_summary", "disk_reliability_counters", "installed_software",
        "boot_time_breakdown", "battery_health_report", "eventlog_full_export",
        "full_system_snapshot_report", "win11_upgrade_readiness",
    }.issubset(ids)


def test_m01_catalog_covers_new_system_state_gap_actions():
    module = load_module(CATALOG_PATH)
    ids = {a.id for a in module.actions}
    assert {
        "activation_status", "restore_points", "uptime_faststartup",
        "os_edition_eol", "bitlocker_status", "tpm_status", "pagefile_info",
    }.issubset(ids)


def test_m01_catalog_covers_crash_triage_actions():
    # G06 - their behaviour is tested against stubs in tests/test_crash_triage.py.
    module = load_module(CATALOG_PATH)
    ids = {a.id for a in module.actions}
    assert {
        "crash_bugcheck_triage", "whea_hardware_errors", "reliability_history", "crash_dump_evidence",
    }.issubset(ids)


def test_m01_catalog_full_system_snapshot_has_an_inactivity_timeout():
    # It chains several potentially-slow queries (scheduled tasks, network
    # connections) into one command, so it needs more headroom than the
    # default before the inactivity watchdog would call it stalled.
    module = load_module(CATALOG_PATH)
    action = next(a for a in module.actions if a.id == "full_system_snapshot_report")
    assert action.inactivity_timeout_sec == 60


def test_m01_catalog_diagnostic_exports_lock_down_their_output_folder():
    # battery_health_report and eventlog_full_export write potentially
    # sensitive data (event logs, battery telemetry) under a shared
    # ProgramData folder - they must restrict it to the current user,
    # SYSTEM, and Administrators so other local accounts can't read it.
    module = load_module(CATALOG_PATH)
    by_id = {a.id: a for a in module.actions}
    for action_id in ("battery_health_report", "eventlog_full_export"):
        command = by_id[action_id].command
        assert "icacls" in command, action_id
        assert "S-1-5-32-544" in command, action_id
        assert "S-1-5-18" in command, action_id
        assert "$env:USERDOMAIN" in command, action_id
        # The shared $env:ProgramData\PortableFix parent must also be
        # locked down unconditionally (not just each action's own per-run
        # subfolder) - icacls is idempotent, so this must run every time,
        # not only on first creation, or an already-existing unhardened
        # root from an older install never gets fixed.
        assert 'icacls $root /inheritance:r' in command, action_id
        assert "Test-Path $root" not in command, action_id


def test_m01_catalog_eventlog_export_exit_code_reflects_actual_result():
    module = load_module(CATALOG_PATH)
    action = next(a for a in module.actions if a.id == "eventlog_full_export")
    assert "exit 1" in action.command
    assert "exit 0" in action.command


def test_m01_catalog_every_action_has_both_language_labels_and_descriptions():
    module = load_module(CATALOG_PATH)
    for action in module.actions:
        assert action.label_sk
        assert action.label_en
        assert action.description_sk
        assert action.description_en


# --- os_edition_eol (lifecycle verdict) and win11_upgrade_readiness ---------------
# Both run in PowerShell against stubbed registry / CIM / clock (tests/report_rig.py);
# the session culture is Slovak, so a date parsed with the current culture fails here.

import pytest

from report_rig import registry_stub, run_ps

CV_KEY = "HKLM:\\SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion"
SB_KEY = "HKLM:\\SYSTEM\\CurrentControlSet\\Control\\SecureBoot\\State"


def _action(action_id):
    return next(a for a in load_module(CATALOG_PATH).actions if a.id == action_id)


def _eol(build, edition="Core", today="2026-11-18", install_type="Client"):
    cv = {"CurrentBuildNumber": str(build), "DisplayVersion": "x", "UBR": 1, "EditionID": edition,
          "InstallationType": install_type}
    clock = f"function Get-Date {{ [datetime]'{today}' }}"
    result = run_ps(_action("os_edition_eol").command, [registry_stub({CV_KEY: cv}), clock],
                    names=("Get-ItemProperty", "Get-Date"))
    assert result.returncode == 0, result.stderr
    return [line for line in result.stdout.splitlines() if line.startswith("VERDICT")][0]


@pytest.mark.parametrize("build,edition,today,expected", [
    (26200, "Core", "2026-11-18", "SUPPORTED - Windows 11 25H2 (Home/Pro) is supported until 2027-10-12 (328 days)"),
    (26100, "Professional", "2026-11-18", "OUT OF SUPPORT - Windows 11 24H2 (Home/Pro) ended on 2026-10-13 (36 days ago)"),
    (26100, "Enterprise", "2026-11-18", "SUPPORTED - Windows 11 24H2 (Enterprise/Education) is supported until 2027-10-12"),
    (22631, "Education", "2026-11-18", "OUT OF SUPPORT - Windows 11 23H2 (Enterprise/Education) ended on 2026-11-10"),
    (26200, "Core", "2027-05-01", "ENDING SOON - Windows 11 25H2 (Home/Pro) support ends in 164 days (2027-10-12)"),
])
def test_m01_os_edition_eol_gives_a_windows_11_verdict(build, edition, today, expected):
    assert expected in _eol(build, edition, today)


def test_m01_os_edition_eol_stale_table_says_unknown_but_an_ended_release_stays_out_of_support():
    # Table last updated 2026-11-18, stale after 180 days: a future end date can
    # no longer be trusted as "supported", a past one cannot become supported.
    assert "UNKNOWN - the built-in table is older than 180 days (last updated 2026-11-18)" in _eol(26200, "Core", "2027-06-01")
    assert "OUT OF SUPPORT" in _eol(26100, "Core", "2027-06-01")


@pytest.mark.parametrize("build,edition,install_type,expected", [
    (28000, "Core", "Client", "UNKNOWN - build 28000 is not in the built-in table"),
    (26100, "EnterpriseS", "Client", "UNKNOWN - this edition"),
    (26100, "ServerStandard", "Server", "UNKNOWN - this edition"),
    (19045, "Professional", "Client", "OUT OF SUPPORT - Windows 10 reached end of support on 2025-10-14"),
])
def test_m01_os_edition_eol_never_calls_an_unlisted_release_supported(build, edition, install_type, expected):
    assert expected in _eol(build, edition, install_type=install_type)


def test_m01_os_edition_eol_prints_the_table_date_and_is_read_only():
    action = _action("os_edition_eol")
    assert "$tableUpdated = '2026-11-18'" in action.command
    assert action.risk == RiskLevel.SAFE and action.undo_command is None
    for verb in ("Set-ItemProperty", "Remove-ItemProperty", "New-Item"):
        assert verb not in action.command


def _cim_stub(tpm="throw", disks=None, ram_gb=16, cpu="Intel(R) Core(TM) i7-8550U CPU @ 1.80GHz"):
    disks = disks if disks is not None else "[pscustomobject]@{ IsBoot = $true; PartitionStyle = 2; Size = 512GB }"
    tpm_body = "throw 'denied'" if tpm == "throw" else tpm
    return (
        "function Get-CimInstance { [CmdletBinding()] param([string] $Namespace, [string] $ClassName) "
        "switch ($ClassName) { "
        f"'Win32_Tpm' {{ {tpm_body} }} "
        f"'MSFT_Disk' {{ @({disks}) }} "
        f"'Win32_ComputerSystem' {{ [pscustomobject]@{{ TotalPhysicalMemory = [int64]({ram_gb} * 1GB) }} }} "
        f"'Win32_Processor' {{ [pscustomobject]@{{ Name = '{cpu}' }} }} "
        "} }"
    )


GOOD_TPM = "[pscustomobject]@{ SpecVersion = '2.0, 0, 1.59'; IsEnabled_InitialValue = $true }"


def _ready(build=19045, sb=True, **cim):
    tables = {CV_KEY: {"CurrentBuildNumber": str(build)}}
    if sb is not None:
        tables[SB_KEY] = {"UEFISecureBootEnabled": 1 if sb else 0}
    result = run_ps(_action("win11_upgrade_readiness").command,
                    [registry_stub(tables), _cim_stub(**cim)], names=("Get-ItemProperty", "Get-CimInstance"))
    assert result.returncode == 0, result.stderr
    return result.stdout


def test_m01_readiness_is_a_read_only_safe_report():
    action = _action("win11_upgrade_readiness")
    assert action.risk == RiskLevel.SAFE
    assert action.undo_command is None and action.check_command is None
    for verb in ("Set-", "Remove-", "New-Item", "Get-Tpm", "Confirm-SecureBootUEFI", "Get-Disk", "Get-Partition"):
        assert verb not in action.command, verb


def test_m01_readiness_good_pc_is_likely_ready():
    out = _ready(tpm=GOOD_TPM)
    assert "[FAIL]" not in out and "[UNKNOWN]" not in out
    assert out.count("[PASS]") == 7
    assert "VERDICT: LIKELY READY" in out


def test_m01_readiness_lists_every_failed_requirement():
    old_disk = "[pscustomobject]@{ IsBoot = $true; PartitionStyle = 1; Size = 32GB }"
    out = _ready(sb=None, tpm="$null", disks=old_disk, ram_gb=2, cpu="Intel(R) Core(TM) i5-6200U CPU @ 2.30GHz")
    for part in ("[FAIL] TPM 2.0: no TPM found", "[FAIL] UEFI firmware", "[FAIL] Secure Boot",
                 "[FAIL] System disk partition style: MBR", "[FAIL] System disk size: 32 GB",
                 "[FAIL] RAM: 2 GB", "[FAIL] CPU generation (heuristic)"):
        assert part in out, part
    assert "VERDICT: NOT READY - 7 requirement(s) failed" in out


def test_m01_readiness_old_tpm_and_secure_boot_off_but_capable():
    out = _ready(sb=False, tpm="[pscustomobject]@{ SpecVersion = '1.2, 2, 3'; IsEnabled_InitialValue = $true }")
    assert "[FAIL] TPM 2.0: the TPM reports spec 1.2" in out
    assert "[PASS] Secure Boot: capable, currently switched off" in out


def test_m01_readiness_unreadable_tpm_is_unknown_not_fail():
    out = _ready()  # the stub throws on Win32_Tpm like a non-elevated session
    assert "[UNKNOWN] TPM 2.0" in out and "[FAIL]" not in out
    assert "VERDICT: INCOMPLETE" in out


def test_m01_readiness_does_not_apply_on_windows_11():
    assert "already runs Windows 11" in _ready(build=26200)


@pytest.mark.parametrize("cpu,verdict", [
    ("Intel(R) Core(TM) i7-8550U CPU @ 1.80GHz", "PASS"),
    ("Intel(R) Core(TM) i7-7700 CPU @ 3.60GHz", "FAIL"),
    ("11th Gen Intel(R) Core(TM) i7-1165G7 @ 2.80GHz", "PASS"),
    ("Intel(R) Core(TM) i5-1235U", "PASS"),
    ("12th Gen Intel(R) Core(TM) i5-12400", "PASS"),
    ("Intel(R) Core(TM) i7-920 CPU @ 2.67GHz", "FAIL"),
    ("Intel(R) Core(TM) Ultra 7 155H", "PASS"),
    ("AMD Ryzen 5 3600 6-Core Processor", "PASS"),
    ("AMD Ryzen 5 PRO 4650U with Radeon Graphics", "PASS"),
    ("AMD Ryzen 7 1700 Eight-Core Processor", "FAIL"),
    ("Intel(R) Celeron(R) N3350 CPU @ 1.10GHz", "UNKNOWN"),
])
def test_m01_readiness_cpu_generation_heuristic(cpu, verdict):
    line = [l for l in _ready(tpm=GOOD_TPM, cpu=cpu).splitlines() if "CPU generation" in l][0]
    assert line.startswith(f"[{verdict}]"), line
