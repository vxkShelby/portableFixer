import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from portablefix.models import ModuleCategory, RiskLevel
from portablefix.module_engine import load_module

CATALOG_PATH = Path(__file__).resolve().parent.parent / "Modules" / "m05_windows_update" / "actions.yaml"


def test_m05_catalog_loads_9_actions_in_repair_category():
    module = load_module(CATALOG_PATH)
    assert module.module_id == "m05_windows_update"
    assert module.category == ModuleCategory.REPAIR
    assert len(module.actions) == 9


def test_m05_catalog_risk_distribution():
    module = load_module(CATALOG_PATH)
    by_risk = {}
    for action in module.actions:
        by_risk.setdefault(action.risk, []).append(action.id)
    assert set(by_risk[RiskLevel.SAFE]) == {
        "wu_check_services",
        "wu_restart_services",
        "wu_trigger_detection",
        "wu_driver_updates_report",
        "os_win10_esu_status",
    }
    assert set(by_risk[RiskLevel.MODERATE]) == {
        "wu_stop_services",
        "wu_reset_cache",
        "wu_reregister_dlls",
    }
    assert set(by_risk[RiskLevel.REQUIRES_REBOOT]) == {"wu_uninstall_last_update"}
    assert RiskLevel.DESTRUCTIVE not in by_risk


def test_m05_catalog_covers_expected_ids():
    module = load_module(CATALOG_PATH)
    ids = {a.id for a in module.actions}
    assert ids == {
        "wu_check_services",
        "wu_stop_services",
        "wu_reset_cache",
        "wu_restart_services",
        "wu_reregister_dlls",
        "wu_trigger_detection",
        "wu_driver_updates_report",
        "wu_uninstall_last_update",
        "os_win10_esu_status",
    }


def test_m05_catalog_stop_services_before_reset_cache_before_restart_services():
    module = load_module(CATALOG_PATH)
    ids = [a.id for a in module.actions]
    assert ids.index("wu_stop_services") < ids.index("wu_reset_cache")
    assert ids.index("wu_reset_cache") < ids.index("wu_restart_services")


def test_m05_catalog_undo_commands_present_only_on_stop_services_and_reset_cache():
    module = load_module(CATALOG_PATH)
    by_id = {a.id: a for a in module.actions}
    assert by_id["wu_stop_services"].undo_command is not None
    assert by_id["wu_reset_cache"].undo_command is not None
    assert by_id["wu_check_services"].undo_command is None
    assert by_id["wu_restart_services"].undo_command is None
    assert by_id["wu_reregister_dlls"].undo_command is None
    assert by_id["wu_trigger_detection"].undo_command is None
    assert by_id["wu_driver_updates_report"].undo_command is None
    assert by_id["wu_uninstall_last_update"].undo_command is None
    assert by_id["os_win10_esu_status"].undo_command is None


def test_m05_catalog_driver_updates_report_uses_wua_com_api_not_trigger_detection():
    module = load_module(CATALOG_PATH)
    action = next(a for a in module.actions if a.id == "wu_driver_updates_report")
    assert action.risk == RiskLevel.SAFE
    assert "Microsoft.Update.Session" in action.command
    assert "UsoClient" not in action.command
    # A live WUA search against Microsoft Update can take several minutes -
    # the default 300s inactivity timeout is too tight for a single silent
    # long-running COM call with no interim output.
    assert action.inactivity_timeout_sec == 600


def test_m05_catalog_uninstall_last_update_requires_reboot():
    module = load_module(CATALOG_PATH)
    action = next(a for a in module.actions if a.id == "wu_uninstall_last_update")
    assert action.risk == RiskLevel.REQUIRES_REBOOT
    assert action.undo_command is None
    assert "wusa.exe" in action.command


def test_m05_catalog_uninstall_last_update_uses_wua_history_not_gethotfix():
    # Get-HotFix's InstalledOn field is unreliable (frequently null on real
    # machines), so sorting by it doesn't reliably surface the actual most
    # recent update. The WUA COM API's update history is more consistent.
    module = load_module(CATALOG_PATH)
    action = next(a for a in module.actions if a.id == "wu_uninstall_last_update")
    assert "Get-HotFix" not in action.command
    assert "Microsoft.Update.Session" in action.command
    assert "QueryHistory" in action.command


def test_m05_reset_cache_in_use_warning_fails_and_points_at_stop_services():
    # A folder still locked by a running service used to print a WARNING and
    # exit 0 - the report/history showed success and an undo was recorded for
    # a reset that never happened. Name the real step that fixes it.
    module = load_module(CATALOG_PATH)
    by_id = {a.id: a for a in module.actions}
    command = by_id["wu_reset_cache"].command
    warning_branch = command[command.index("still in use"):]
    assert "exit 1" in warning_branch
    assert f'"{by_id["wu_stop_services"].label_en}"' in warning_branch


def test_m05_reset_cache_removes_stale_bak_before_rename_so_a_rerun_works():
    # Rename-Item fails when <folder>.bak is left over from an earlier reset,
    # so a second run on the same machine could never succeed. The stale .bak
    # goes through the junction-safe helper (tests/test_safe_delete.py).
    module = load_module(CATALOG_PATH)
    command = next(a for a in module.actions if a.id == "wu_reset_cache").command
    for folder in ("SoftwareDistribution", "System32\\catroot2"):
        bak_removal = f'$null = Remove-PfSafe "$env:WINDIR\\{folder}.bak"'
        rename = f'Rename-Item -Path "$env:WINDIR\\{folder}"'
        assert bak_removal in command, folder
        assert command.index(bak_removal) < command.index(rename), folder


def test_m05_reregister_dlls_checks_each_regsvr32_exit_code():
    # regsvr32 is a GUI-subsystem exe: a bare call returns before it finishes
    # and its exit code was never looked at, so the action always "worked".
    # Start-Process -Wait -PassThru gives a real ExitCode per dll; dlls not
    # present on this Windows build are skipped, not counted as failures.
    module = load_module(CATALOG_PATH)
    command = next(a for a in module.actions if a.id == "wu_reregister_dlls").command
    for token in ("Start-Process", "-Wait", "-PassThru", "Test-Path", "$r.ExitCode"):
        assert token in command, token
    assert "regsvr32.exe /s" not in command
    assert command.index("Test-Path") < command.index("Start-Process")
    assert "exit 1" in command[command.rindex("if ($failed.Count -gt 0)"):]


# --- G29 os_win10_esu_status -----------------------------------------------------
#
# The real catalog command runs in PowerShell against stubbed registry,
# servicing, WUA, service and identity lookups (functions win over cmdlets
# in command lookup); the script refuses to run (exit 97) unless every
# stubbed name resolves to the stub.

ESU_ID = "os_win10_esu_status"
STUB_GUARD_EXIT = 97
CV_KEY = "HKLM:\\SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion"
CBS_KEY = "HKLM:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Component Based Servicing\\Packages"
ESU_HKCU = "HKCU:\\Software\\Microsoft\\Windows NT\\CurrentVersion\\Windows\\ConsumerESU"
ESU_HKLM = "HKLM:\\SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion\\Windows\\ConsumerESU"
UNINSTALL_KEY = "HKLM:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Uninstall"
SECURITY_UPDATES = "0fa1201d-4330-4fa8-8ae9-b877473b6441"
DEFINITION_UPDATES = "e0789628-ce08-4437-be74-2495b842f43b"
WIN10 = {"CurrentBuildNumber": "19045", "UBR": 6456, "EditionID": "Professional", "DisplayVersion": "22H2",
         "InstallationType": "Client"}

ESU_STUB_HELPERS = r"""
function Pf-Ft($days, $part) { $ft = [DateTime]::UtcNow.AddDays(-$days).ToFileTimeUtc() -bor 2147483648; if ($part -eq 'hi') { [int]($ft -shr 32) } else { [BitConverter]::ToInt32([BitConverter]::GetBytes([int64]($ft -band 4294967295)), 0) } }
function Get-ItemProperty { [CmdletBinding()] param([string] $LiteralPath) if ($global:REG.ContainsKey($LiteralPath)) { $global:REG[$LiteralPath] } }
function Get-ChildItem { [CmdletBinding()] param([string] $LiteralPath) if ($global:KIDS.ContainsKey($LiteralPath)) { $global:KIDS[$LiteralPath] } }
function New-Object { [CmdletBinding()] param([string] $ComObject) if ($null -eq $global:WUH) { throw [System.Runtime.InteropServices.COMException]::new('Trieda nie je zaregistrovaná.') }; $searcher = [pscustomobject]@{}; $searcher | Add-Member ScriptMethod GetTotalHistoryCount { @($global:WUH).Count }; $searcher | Add-Member ScriptMethod QueryHistory { param($start, $count) $global:WUH }; $session = [pscustomobject]@{ S = $searcher }; $session | Add-Member ScriptMethod CreateUpdateSearcher { $this.S }; $session }
function Get-Service { [CmdletBinding()] param([string] $Name) if ($global:SVC -and $Name -eq '0patchservice') { [pscustomobject]@{ Name = $Name; Status = 'Running' } } }
function whoami { if ($env:PF_WHOAMI) { $env:PF_WHOAMI } else { $global:LASTEXITCODE = 1 } }
function Get-CimInstance { [CmdletBinding()] param([string] $ClassName) [pscustomobject]@{ UserName = $env:PF_CONSOLE_USER } }
"""


def _powershell_or_skip() -> str:
    exe = os.environ.get("PORTABLEFIX_TEST_PWSH") or shutil.which("powershell") or shutil.which("pwsh")
    if not exe:
        pytest.skip("no PowerShell available")
    return exe


def _ps_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _ps_obj(values: dict) -> str:
    def val(v):
        if isinstance(v, bool):
            return "$true" if v else "$false"
        if isinstance(v, int):
            return str(v)
        return _ps_quote(v)
    return "[pscustomobject]@{ " + "; ".join(f"{_ps_quote(k)} = {val(v)}" for k, v in values.items()) + " }"


def _esu_command():
    return next(a for a in load_module(CATALOG_PATH).actions if a.id == ESU_ID).command


def _run_esu(tmp_path, cv=WIN10, rollups=(), hkcu=None, hklm=None, wu_history=None, patch_service=False,
             patch_uninstall=None, who=("PC\\technik", "S-1-5-21-1-2-3-1001"), console=None, dll=True):
    """rollups: (package name, CurrentState, days ago). wu_history: None = WUA
    unavailable, else (operation, result code, days ago, [category ids])."""
    windows = tmp_path / "Windows"
    (windows / "System32").mkdir(parents=True, exist_ok=True)
    if dll:
        (windows / "System32" / "ConsumerESUMgr.dll").write_bytes(b"MZ")
    reg = [f"{_ps_quote(CV_KEY)} = {_ps_obj(cv)}"] if cv is not None else []
    kids = []
    packages = []
    for i, (name, state, days) in enumerate(rollups):
        path = f"{CBS_KEY}\\{name}"
        packages.append(f"[pscustomobject]@{{ PSChildName = {_ps_quote(name)}; PSPath = {_ps_quote(path)} }}")
        reg.append(f"{_ps_quote(path)} = [pscustomobject]@{{ CurrentState = {state}; "
                   f"InstallTimeHigh = (Pf-Ft {days} 'hi'); InstallTimeLow = (Pf-Ft {days} 'lo') }}")
    # An unrelated package the command must skip by name.
    packages.append(f"[pscustomobject]@{{ PSChildName = 'Package_for_KB5000000~31bf3856ad364e35~amd64~~19041.1'; "
                    f"PSPath = {_ps_quote(CBS_KEY + chr(92) + 'Package_for_KB5000000')} }}")
    kids.append(f"{_ps_quote(CBS_KEY)} = @({', '.join(packages)})")
    if hkcu is not None:
        reg.append(f"{_ps_quote(ESU_HKCU)} = {_ps_obj(hkcu)}")
    if hklm is not None:
        reg.append(f"{_ps_quote(ESU_HKLM)} = {_ps_obj(hklm)}")
    if patch_uninstall:
        path = UNINSTALL_KEY + "\\{0PATCH}"
        kids.append(f"{_ps_quote(UNINSTALL_KEY)} = @([pscustomobject]@{{ PSChildName = '{{0PATCH}}'; PSPath = {_ps_quote(path)} }})")
        reg.append(f"{_ps_quote(path)} = [pscustomobject]@{{ DisplayName = {_ps_quote(patch_uninstall)} }}")
    if wu_history is None:
        wuh = "$null"
    else:
        entries = []
        for op, rc, days, cats in wu_history:
            cat_objs = ", ".join(f"[pscustomobject]@{{ CategoryID = {_ps_quote(c)} }}" for c in cats)
            entries.append(f"[pscustomobject]@{{ Operation = {op}; ResultCode = {rc}; "
                           f"Date = [DateTime]::UtcNow.AddDays(-{days}); Categories = @({cat_objs}) }}")
        wuh = "@(" + ", ".join(entries) + ")"
    env_vars = {"SystemRoot": str(windows), "PF_WHOAMI": f'"{who[0]}","{who[1]}"' if who else "",
                "PF_CONSOLE_USER": console if console is not None else (who[0] if who else "")}
    # Set inside the script, not in the child's environment: Windows
    # PowerShell cannot start with a fake SystemRoot.
    script = "\n".join(
        ["[Console]::OutputEncoding=[Text.Encoding]::UTF8"]
        + [f"$env:{k} = {_ps_quote(v)}" for k, v in env_vars.items()]
        + [ESU_STUB_HELPERS,
           "$global:REG = @{ " + "; ".join(reg) + " }",
           "$global:KIDS = @{ " + "; ".join(kids) + " }",
           f"$global:WUH = {wuh}",
           f"$global:SVC = {'$true' if patch_service else '$false'}",
           "foreach ($n in 'Get-ItemProperty', 'Get-ChildItem', 'New-Object', 'Get-Service', 'whoami', 'Get-CimInstance') { "
           f"if ((Get-Command $n -EA SilentlyContinue | Select-Object -First 1).CommandType -ne 'Function') {{ exit {STUB_GUARD_EXIT} }} }}",
           _esu_command()]
    )
    result = subprocess.run(
        [_powershell_or_skip(), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script],
        env=dict(os.environ), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert result.returncode != STUB_GUARD_EXIT, "a system lookup was not stubbed - refusing to run the real one"
    return result


def _verdict(stdout: str) -> str:
    lines = [line for line in stdout.splitlines() if line.startswith("VERDICT: ")]
    assert len(lines) == 1, stdout
    return lines[0]


ROLLUP = "Package_for_RollupFix~31bf3856ad364e35~amd64~~19041.6456.1.10"
ENROLLED = {"ESUEligibility": 3, "ESUEligibilityResult": 1}


def test_esu_windows_11_is_not_applicable(tmp_path):
    result = _run_esu(tmp_path, cv=dict(WIN10, CurrentBuildNumber="26100", UBR=6584, DisplayVersion="24H2"),
                      rollups=[(ROLLUP, 112, 10.5)])
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Windows build: 26100.6584 (EditionID Professional, DisplayVersion 24H2" in result.stdout
    assert _verdict(result.stdout) == "VERDICT: NOT APPLICABLE - Windows 11 (build 26100): Windows 10 ESU does not apply."
    assert "ConsumerESU" not in result.stdout


def test_esu_windows_11_unpatched_still_warns(tmp_path):
    result = _run_esu(tmp_path, cv=dict(WIN10, CurrentBuildNumber="22631"), rollups=[(ROLLUP, 112, 80.5)])
    assert "WARNING: the last cumulative update is 80 days old" in _verdict(result.stdout)


def test_esu_enrolled_and_patched_is_ok(tmp_path):
    result = _run_esu(tmp_path, rollups=[(ROLLUP, 112, 10.5)], hkcu=ENROLLED)
    assert result.returncode == 0, result.stdout + result.stderr
    out = result.stdout
    assert "Windows build: 19045.6456 (EditionID Professional, DisplayVersion 22H2, InstallationType Client)" in out
    assert re.search(r"Last cumulative update installed: \d{4}-\d{2}-\d{2} \(10 days ago; source: servicing package "
                     + re.escape(ROLLUP), out), out
    assert f"{ESU_HKCU}: ESUEligibility = 3 DeviceEnrolled, ESUEligibilityResult = 1 SUCCESS" in out
    assert f"{ESU_HKLM}: ESUEligibility = not set, ESUEligibilityResult = not set" in out
    assert "Consumer ESU component (ConsumerESUMgr.dll): present." in out
    assert "0patch agent: not installed." in out
    assert _verdict(out) == "VERDICT: OK - enrolled in Consumer ESU and patched (last cumulative update 10 days ago)."
    # The end date is data, never "today"/"days left" computed from the clock.
    assert "Consumer ESU end date (Microsoft): 2026-10-13." in out
    assert "Options: 1) upgrade to Windows 11" in out and "3) a new PC." in out
    assert "HKCU = registry hive of PC\\technik (S-1-5-21-1-2-3-1001)" in out
    assert "WARNING" not in out


def test_esu_newest_installed_rollup_wins_and_superseded_or_staged_ones_are_ignored(tmp_path):
    rollups = [(ROLLUP, 112, 20.5), ("Package_for_RollupFix~31bf3856ad364e35~amd64~~19041.6500.1.1", 80, 2.5),
               ("Package_for_RollupFix~31bf3856ad364e35~amd64~~19041.6400.1.1", 112, 50.5)]
    result = _run_esu(tmp_path, rollups=rollups, hkcu=ENROLLED)
    assert "(20 days ago; source: servicing package " + ROLLUP in result.stdout


def test_esu_enrolled_via_microsoft_account_in_hklm_but_unpatched(tmp_path):
    result = _run_esu(tmp_path, rollups=[(ROLLUP, 112, 50.5)], hklm={"ESUEligibility": 5, "ESUEligibilityResult": 1})
    assert f"{ESU_HKLM}: ESUEligibility = 5 MSAEnrolled" in result.stdout
    assert _verdict(result.stdout) == (
        "VERDICT: ACTION NEEDED - enrolled in Consumer ESU, but the last cumulative update is 50 days old: run Windows Update."
    )


def test_esu_not_enrolled_and_unpatched_lists_the_options(tmp_path):
    result = _run_esu(tmp_path, rollups=[(ROLLUP, 112, 60.5)], hkcu={"ESUEligibility": 2, "ESUEligibilityResult": 1})
    assert result.returncode == 0
    assert "ESUEligibility = 2 Eligible (not enrolled)" in result.stdout
    assert _verdict(result.stdout).startswith("VERDICT: UNPATCHED - Windows 10 without an ESU enrollment and the last "
                                              "cumulative update is 60 days old")
    assert "Options: " in result.stdout


def test_esu_exactly_45_days_is_still_patched(tmp_path):
    result = _run_esu(tmp_path, rollups=[(ROLLUP, 112, 45.2)], hkcu=ENROLLED)
    assert _verdict(result.stdout).startswith("VERDICT: OK")
    result = _run_esu(tmp_path, rollups=[(ROLLUP, 112, 46.2)], hkcu=ENROLLED)
    assert _verdict(result.stdout).startswith("VERDICT: ACTION NEEDED")


def test_esu_reenroll_required(tmp_path):
    result = _run_esu(tmp_path, rollups=[(ROLLUP, 112, 10.5)], hkcu={"ESUEligibility": 4, "ESUEligibilityResult": 1})
    assert "ESUEligibility = 4 ReEnrollReq" in result.stdout
    assert _verdict(result.stdout).startswith("VERDICT: ACTION NEEDED - the Consumer ESU enrollment must be renewed")


def test_esu_values_outside_the_public_decoding_are_unknown(tmp_path):
    result = _run_esu(tmp_path, rollups=[(ROLLUP, 112, 10.5)], hkcu={"ESUEligibility": 99, "ESUEligibilityResult": "x"})
    assert f"{ESU_HKCU}: ESUEligibility = 99 Unknown, ESUEligibilityResult = x Unknown" in result.stdout
    assert _verdict(result.stdout).startswith("VERDICT: PATCHED - no Consumer ESU enrollment recorded here")


def test_esu_key_based_esu_is_named(tmp_path):
    result = _run_esu(tmp_path, rollups=[(ROLLUP, 112, 10.5)], hklm={"ESUEligibility": 1, "ESUEligibilityResult": 12})
    assert "ESUEligibilityResult = 12 KEY_BASED_ESU" in result.stdout
    assert _verdict(result.stdout).endswith("(key-based ESU reported).")


def test_esu_falls_back_to_security_updates_in_wua_history(tmp_path):
    # A Defender definition update from yesterday must not count as a
    # cumulative update; a failed install neither.
    history = [(1, 2, 1.5, [DEFINITION_UPDATES]), (1, 4, 3.5, [SECURITY_UPDATES]),
               (1, 2, 20.5, [SECURITY_UPDATES]), (2, 2, 2.5, [SECURITY_UPDATES])]
    result = _run_esu(tmp_path, rollups=(), wu_history=history, hkcu=ENROLLED)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "(20 days ago; source: Windows Update history (security update)" in result.stdout
    assert _verdict(result.stdout).startswith("VERDICT: OK")


def test_esu_patch_age_unknown(tmp_path):
    result = _run_esu(tmp_path, rollups=(), wu_history=None)
    assert "Last cumulative update installed: unknown" in result.stdout
    assert _verdict(result.stdout).startswith("VERDICT: UNKNOWN - no Consumer ESU enrollment recorded here")
    result = _run_esu(tmp_path, rollups=(), wu_history=None, hkcu=ENROLLED)
    assert _verdict(result.stdout).startswith("VERDICT: CHECK - enrolled in Consumer ESU")


def test_esu_detects_0patch_by_service_and_uninstall_entry(tmp_path):
    result = _run_esu(tmp_path, rollups=[(ROLLUP, 112, 60.5)], patch_service=True, patch_uninstall="0patch Agent")
    assert ("0patch agent: installed - service 0patchservice (Running), installed program 0patch Agent. "
            "Its micropatches cover selected vulnerabilities only") in result.stdout
    result = _run_esu(tmp_path, rollups=[(ROLLUP, 112, 60.5)], patch_uninstall="Some Other App")
    assert "0patch agent: not installed." in result.stdout


def test_esu_warns_when_hkcu_is_not_the_signed_in_users(tmp_path):
    result = _run_esu(tmp_path, rollups=[(ROLLUP, 112, 10.5)], console="PC\\zakaznik")
    assert "WARNING: the signed-in user is PC\\zakaznik, but PortableFix runs as PC\\technik" in result.stdout
    result = _run_esu(tmp_path, rollups=[(ROLLUP, 112, 10.5)], who=None, console="PC\\zakaznik")
    assert "whoami failed, name unknown" in result.stdout


def test_esu_missing_component_and_pre_22h2_build(tmp_path):
    result = _run_esu(tmp_path, cv=dict(WIN10, CurrentBuildNumber="19044"), rollups=[(ROLLUP, 112, 10.5)], dll=False)
    assert "Consumer ESU component (ConsumerESUMgr.dll): missing" in result.stdout
    assert "ESU needs Windows 10 22H2 (build 19045); this PC runs build 19044" in result.stdout


@pytest.mark.parametrize("cv,verdict", [
    (dict(WIN10, InstallationType="Server", CurrentBuildNumber="17763"), "VERDICT: NOT APPLICABLE - Windows Server"),
    (dict(WIN10, EditionID="EnterpriseS", CurrentBuildNumber="19044"), "VERDICT: NOT APPLICABLE - Windows 10 LTSC/LTSB edition (EnterpriseS)"),
    (dict(WIN10, EditionID="IoTEnterpriseS", CurrentBuildNumber="19044"), "VERDICT: NOT APPLICABLE - Windows 10 LTSC/LTSB edition (IoTEnterpriseS)"),
    (dict(WIN10, CurrentBuildNumber="9600"), "VERDICT: NOT APPLICABLE - build 9600 is not Windows 10."),
])
def test_esu_other_editions_are_not_applicable(tmp_path, cv, verdict):
    result = _run_esu(tmp_path, cv=cv, rollups=[(ROLLUP, 112, 10.5)])
    assert result.returncode == 0, result.stdout + result.stderr
    assert _verdict(result.stdout).startswith(verdict)


def test_esu_unreadable_build_fails(tmp_path):
    result = _run_esu(tmp_path, cv=None)
    assert result.returncode == 1
    assert "the Windows build is unknown" in result.stdout


def test_esu_is_read_only_and_never_parses_localized_text():
    command = _esu_command()
    action = next(a for a in load_module(CATALOG_PATH).actions if a.id == ESU_ID)
    assert action.risk == RiskLevel.SAFE and action.preview_command is None
    for verb in ("Set-", "New-Item", "Remove-", "reg add", "Install-", "wusa", "Start-Service"):
        assert verb not in command, verb
    # KB titles, Get-HotFix dates and exception messages are localized or
    # unreliable; the only patterns are registry/package names and edition ids.
    for forbidden in ("Get-HotFix", "Win32_QuickFixEngineering", ".Title", ".Message", "Select-String", "-replace"):
        assert forbidden not in command, forbidden
    assert set(re.findall(r"-match '([^']*)'", command)) == {"^(IoT)?EnterpriseS"}
    assert set(re.findall(r"-like '([^']*)'", command)) == {"Package_for_RollupFix*", "0patch*"}
    # ProductName says "Windows 10" on Windows 11 - the build decides.
    assert "ProductName" not in command


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


def test_m05_catalog_scripts_parse_without_powershell_7_only_syntax(tmp_path):
    module = load_module(CATALOG_PATH)
    scripts = {
        f"{a.id}.{field}": getattr(a, field)
        for a in module.actions
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
