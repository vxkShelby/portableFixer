from pathlib import Path

from portablefix.models import ModuleCategory, RiskLevel
from portablefix.module_engine import load_module

CATALOG_PATH = Path(__file__).resolve().parent.parent / "Modules" / "m16_office_repair" / "actions.yaml"


def test_m16_catalog_loads_8_actions_in_repair_category():
    module = load_module(CATALOG_PATH)
    assert module.module_id == "m16_office_repair"
    assert module.category == ModuleCategory.REPAIR
    assert len(module.actions) == 8


def test_m16_catalog_risk_distribution():
    module = load_module(CATALOG_PATH)
    by_risk = {}
    for action in module.actions:
        by_risk.setdefault(action.risk, []).append(action.id)
    assert set(by_risk[RiskLevel.SAFE]) == {
        "office_version_channel_report",
        "office_addins_report",
        "office_ost_pst_report",
    }
    assert set(by_risk[RiskLevel.MODERATE]) == {
        "office_com_addin_disable_all_thirdparty",
        "office_com_addin_disable_machine_wide",
        "office_quick_repair",
        "office_online_repair",
        "office_reset_outlook_profile",
    }
    assert RiskLevel.DESTRUCTIVE not in by_risk
    assert RiskLevel.REQUIRES_REBOOT not in by_risk


def test_m16_catalog_only_addin_disable_has_undo():
    module = load_module(CATALOG_PATH)
    by_id = {a.id: a for a in module.actions}
    assert by_id["office_com_addin_disable_all_thirdparty"].undo_command is not None
    assert by_id["office_com_addin_disable_machine_wide"].undo_command is not None
    for not_undoable in (
        "office_version_channel_report",
        "office_addins_report",
        "office_ost_pst_report",
        "office_quick_repair",
        "office_online_repair",
        "office_reset_outlook_profile",
    ):
        assert by_id[not_undoable].undo_command is None, not_undoable


def test_m16_catalog_covers_expected_ids():
    module = load_module(CATALOG_PATH)
    ids = {a.id for a in module.actions}
    assert ids == {
        "office_version_channel_report",
        "office_addins_report",
        "office_ost_pst_report",
        "office_com_addin_disable_all_thirdparty",
        "office_com_addin_disable_machine_wide",
        "office_quick_repair",
        "office_online_repair",
        "office_reset_outlook_profile",
    }


def test_m16_catalog_addins_report_has_empty_result_fallback():
    module = load_module(CATALOG_PATH)
    action = next(a for a in module.actions if a.id == "office_addins_report")
    assert "No Outlook add-ins found." in action.command


def test_m16_catalog_every_action_has_both_language_labels_and_descriptions():
    module = load_module(CATALOG_PATH)
    for action in module.actions:
        assert action.label_sk
        assert action.label_en
        assert action.description_sk
        assert action.description_en


def test_m16_catalog_outlook_profile_reset_fails_the_action_if_rename_fails():
    # Rename-Item's default ErrorActionPreference is Continue - without
    # -EA Stop + a catch, a failed rename (e.g. Outlook still holding the
    # registry key open) would still print the success message and exit 0.
    module = load_module(CATALOG_PATH)
    action = next(a for a in module.actions if a.id == "office_reset_outlook_profile")
    assert "-EA Stop" in action.command
    assert "catch" in action.command
    assert "exit 1" in action.command


import json  # noqa: E402
import os  # noqa: E402
import shutil  # noqa: E402
import subprocess  # noqa: E402

import pytest  # noqa: E402

from portablefix import target_user  # noqa: E402
from portablefix.executor import build_execution_plan  # noqa: E402

IDENTITY_CALL = "[Security.Principal.WindowsIdentity]::GetCurrent()"
PROCESS_SID = "S-1-5-21-1111-2222-3333-1001"
CLIENT_SID = "S-1-5-21-1111-2222-3333-1002"
STUB_GUARD_EXIT = 97


def _m16(action_id):
    return next(a for a in load_module(CATALOG_PATH).actions if a.id == action_id)


def _pwsh_or_skip() -> str:
    exe = os.environ.get("PORTABLEFIX_TEST_PWSH") or shutil.which("powershell") or shutil.which("pwsh")
    if not exe:
        pytest.skip("no PowerShell available")
    return exe


def _target(status, sid):
    return target_user.TargetUser(
        status=status, process_sid=PROCESS_SID, process_user="PC\\technik", target_sid=sid, target_user="PC\\x", session_id=1,
    )


def _run_stubbed(command: str, stubs: list[str], stubbed: tuple, target=None):
    """Runs a catalog command the way the executor does (prelude included),
    with every system cmdlet in `stubbed` replaced by the given stub
    functions; refuses (exit 97) when a stub did not take."""
    command = command.replace(IDENTITY_CALL, "(Pf-WindowsIdentity)")
    assert "WindowsIdentity]" not in command
    guard = (
        "foreach ($n in " + ", ".join(f"'{n}'" for n in stubbed) + ") { "
        "if ((Get-Command $n -EA SilentlyContinue | Select-Object -First 1).CommandType -ne 'Function') "
        f"{{ exit {STUB_GUARD_EXIT} }} }}"
    )
    plan = build_execution_plan(command, dry_run=False, target_user=target, harden_backups=False)
    result = subprocess.run(
        [_pwsh_or_skip(), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command",
         "; ".join(stubs + [guard, plan.argv[-1]])],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert result.returncode != STUB_GUARD_EXIT, "a cmdlet was not stubbed - refusing to run the real one"
    return result, [line for line in result.stdout.splitlines() if line.startswith("STUB ")]


def _run_outlook_reset(target):
    stubs = [
        f"function Pf-WindowsIdentity {{ [pscustomobject]@{{ User = [pscustomobject]@{{ Value = '{PROCESS_SID}' }} }} }}",
        "function Test-Path { [CmdletBinding()] param([Parameter(Position=0)] $Path, $LiteralPath) $true }",
        "function Get-Process { [CmdletBinding()] param($Name) }",
        "function Stop-Process { [CmdletBinding()] param($Name, [switch] $Force) [Console]::Out.WriteLine('STUB Stop-Process') }",
        "function Rename-Item { [CmdletBinding()] param([Parameter(Position=0)] $Path, $NewName, $LiteralPath) [Console]::Out.WriteLine('STUB Rename-Item ' + $Path + ' -> ' + $NewName) }",
    ]
    return _run_stubbed(
        _m16("office_reset_outlook_profile").command, stubs,
        ("Pf-WindowsIdentity", "Test-Path", "Get-Process", "Stop-Process", "Rename-Item"), target,
    )


def test_m16_outlook_profile_reset_refuses_when_the_signed_in_user_is_someone_else():
    # Over-the-shoulder elevation: HKCU is the technician's hive, so the
    # rename would park the technician's profile and leave the client's.
    result, ran = _run_outlook_reset(_target(target_user.DIFFERENT, CLIENT_SID))
    assert result.returncode == 1, result.stdout + result.stderr
    assert "Nothing was changed." in result.stdout
    assert ran == []


def test_m16_outlook_profile_reset_runs_for_the_same_user():
    result, ran = _run_outlook_reset(_target(target_user.SAME, PROCESS_SID))
    assert result.returncode == 0, result.stdout + result.stderr
    assert any(line.startswith("STUB Rename-Item ") and "Profiles.bak-" in line for line in ran), ran


ADDIN_STUBS = [
    "function Test-Path { [CmdletBinding()] param([Parameter(Position=0)] $Path, $LiteralPath) "
    "if ($Path -like '*.json') { Microsoft.PowerShell.Management\\Test-Path -LiteralPath $Path } else { $true } }",
    "function Get-ChildItem { [CmdletBinding()] param([Parameter(Position=0)] $Path) "
    "foreach ($n in 'Microsoft.VbaAddin', 'Acme.Addin', 'Contoso.Sync') { [pscustomobject]@{ PSChildName = $n; PSPath = ($Path + '\\' + $n) } } }",
    "function Get-ItemProperty { [CmdletBinding()] param([Parameter(Position=0)] $Path, $Name) "
    "$v = $global:__pfLoad[$Path]; if ($null -eq $v) { $v = [int]$env:PF_LOAD_DEFAULT }; [pscustomobject]@{ LoadBehavior = $v } }",
    "function Set-ItemProperty { [CmdletBinding()] param([Parameter(Position=0)] $Path, $Name, $Value, $Type) "
    "[Console]::Out.WriteLine('STUB Set-ItemProperty ' + $Path + ' ' + $Value); if ($env:PF_FAIL -and ($Path -like ('*' + $env:PF_FAIL))) { throw 'Prístup odmietnutý.' }; $global:__pfLoad[$Path] = $Value }",
    "function Get-Process { [CmdletBinding()] param($Name) if ($env:PF_OUTLOOK_RUNNING) { [pscustomobject]@{ ProcessName = 'OUTLOOK' } } }",
]
ADDIN_STUBBED = ("Test-Path", "Get-ChildItem", "Get-ItemProperty", "Set-ItemProperty", "Get-Process")


def _run_addins(tmp_path, field="command", outlook_running=False, fail="", target=None, load_default=3):
    """load_default: the LoadBehavior every add-in reads before the run
    (3 = loaded at startup; 0 after a previous run disabled them)."""
    program_data = tmp_path / "ProgramData"
    program_data.mkdir(exist_ok=True)
    env = [
        f"$env:ProgramData = '{program_data}'", f"$env:PF_OUTLOOK_RUNNING = '{'1' if outlook_running else ''}'",
        f"$env:PF_FAIL = '{fail}'", f"$env:PF_LOAD_DEFAULT = '{load_default}'", "$global:__pfLoad = @{}",
    ]
    result, ran = _run_stubbed(getattr(_m16("office_com_addin_disable_all_thirdparty"), field), env + ADDIN_STUBS, ADDIN_STUBBED, target)
    return result, ran, program_data / "PortableFix" / "office_addins_backup.json"


def test_m16_addin_disable_writes_the_backup_only_once(tmp_path):
    # The backup was rewritten on every run: after the first run every
    # add-in reads 0, so a second run's backup was all zeros and undo
    # re-disabled everything.
    result, ran, backup = _run_addins(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Disabled 2 of 2 third-party Outlook add-in(s)." in result.stdout
    assert [line for line in ran if "Microsoft.VbaAddin" in line] == []
    first = backup.read_text(encoding="utf-8-sig")
    assert {e["Key"]: e["LoadBehavior"] for e in json.loads(first)} == {"Acme.Addin": 3, "Contoso.Sync": 3}
    # Second run: every add-in now reads 0 - a rewritten backup would be all zeros.
    result, _, _ = _run_addins(tmp_path, load_default=0)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Earlier backup kept" in result.stdout
    assert backup.read_text(encoding="utf-8-sig") == first


def test_m16_addin_disable_lists_failed_writes_and_fails(tmp_path):
    result, _, _ = _run_addins(tmp_path, fail="Contoso.Sync")
    assert result.returncode == 1, result.stdout + result.stderr
    assert "Disabled 1 of 2" in result.stdout
    assert "Failed: Contoso.Sync (" in result.stdout


def test_m16_addin_disable_refuses_while_outlook_runs(tmp_path):
    result, ran, backup = _run_addins(tmp_path, outlook_running=True)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "Outlook is running" in result.stdout and "Nothing was changed." in result.stdout
    assert ran == [] and not backup.exists()


def test_m16_addin_disable_and_undo_use_the_signed_in_users_hive(tmp_path):
    target = _target(target_user.DIFFERENT, CLIENT_SID)
    result, ran, backup = _run_addins(tmp_path, target=target)
    assert result.returncode == 0, result.stdout + result.stderr
    assert ran and all(target.hive + "\\Software\\Microsoft\\Office\\Outlook\\Addins\\" in line for line in ran), ran
    result, ran, _ = _run_addins(tmp_path, field="undo_command", target=target)
    assert result.returncode == 0, result.stdout + result.stderr
    assert sorted(ran) == sorted(
        f"STUB Set-ItemProperty {target.hive}\\Software\\Microsoft\\Office\\Outlook\\Addins\\{k} 3" for k in ("Acme.Addin", "Contoso.Sync")
    ), ran


def test_m16_long_running_repairs_have_extended_inactivity_timeouts():
    module = load_module(CATALOG_PATH)
    by_id = {a.id: a for a in module.actions}
    assert by_id["office_online_repair"].inactivity_timeout_sec == 2400
    assert by_id["office_quick_repair"].inactivity_timeout_sec == 600
    for default_timeout in (
        "office_version_channel_report",
        "office_addins_report",
        "office_ost_pst_report",
        "office_com_addin_disable_all_thirdparty",
        "office_com_addin_disable_machine_wide",
        "office_reset_outlook_profile",
    ):
        assert by_id[default_timeout].inactivity_timeout_sec is None, default_timeout


# --- office_com_addin_disable_machine_wide (HKLM / WOW6432Node, every user) ---

HK64 = "HKLM:\\SOFTWARE\\Microsoft\\Office"
HK32 = "HKLM:\\SOFTWARE\\WOW6432Node\\Microsoft\\Office"
MW_THIRD_PARTY = [
    HK64 + "\\Outlook\\Addins\\Acme.Addin",
    HK32 + "\\Outlook\\Addins\\Acme.Addin",  # same name, 32-bit branch: its own key
    HK32 + "\\Excel\\Addins\\Contoso.Sync",
]
MW_STUBS = [
    "function Test-Path { [CmdletBinding()] param([Parameter(Position=0)] $Path, $LiteralPath) "
    "if ($Path -like '*.json') { Microsoft.PowerShell.Management\\Test-Path -LiteralPath $Path } "
    "elseif ($env:PF_GONE -and ($Path -like ('*' + $env:PF_GONE + '*'))) { $false } else { $true } }",
    "function Get-ChildItem { [CmdletBinding()] param([Parameter(Position=0)] $Path) $names = @(); if ($env:PF_EMPTY) { } "
    "elseif ($Path -eq 'HKLM:\\SOFTWARE\\Microsoft\\Office\\Outlook\\Addins') { $names = 'Microsoft.VbaAddin', 'Acme.Addin', 'TeamsAddin.FastConnect' } "
    "elseif ($Path -eq 'HKLM:\\SOFTWARE\\WOW6432Node\\Microsoft\\Office\\Outlook\\Addins') { $names = 'Acme.Addin', 'UCAddin.LyncAddin.1' } "
    "elseif ($Path -eq 'HKLM:\\SOFTWARE\\WOW6432Node\\Microsoft\\Office\\Excel\\Addins') { $names = 'Contoso.Sync' } "
    "foreach ($n in $names) { [pscustomobject]@{ PSChildName = $n; PSPath = ($Path + '\\' + $n) } } }",
    "function Get-ItemProperty { [CmdletBinding()] param([Parameter(Position=0)] $Path, $Name) [pscustomobject]@{ LoadBehavior = [int]$env:PF_LOAD_DEFAULT } }",
    "function Set-ItemProperty { [CmdletBinding()] param([Parameter(Position=0)] $Path, $Name, $Value, $Type) "
    "[Console]::Out.WriteLine('STUB Set-ItemProperty ' + $Path + ' ' + $Value); "
    "if ($env:PF_FAIL -and ($Path -like ('*' + $env:PF_FAIL))) { throw 'Prístup odmietnutý.' } }",
    "function Remove-ItemProperty { [CmdletBinding()] param([Parameter(Position=0)] $Path, $Name) [Console]::Out.WriteLine('STUB Remove-ItemProperty ' + $Path) }",
    "function Get-Process { [CmdletBinding()] param($Name) if ($env:PF_RUNNING) { [pscustomobject]@{ ProcessName = $env:PF_RUNNING } } }",
]
MW_STUBBED = ("Test-Path", "Get-ChildItem", "Get-ItemProperty", "Set-ItemProperty", "Remove-ItemProperty", "Get-Process")
MW_ID = "office_com_addin_disable_machine_wide"


def _run_machine_wide(tmp_path, field="command", backup=None, raw_backup=None, **env_vars):
    program_data = tmp_path / "ProgramData"
    (program_data / "PortableFix").mkdir(parents=True, exist_ok=True)
    backup_file = program_data / "PortableFix" / "office_addins_machine_backup.json"
    backup_file.unlink(missing_ok=True)
    if backup is not None:
        backup_file.write_text(json.dumps(backup), encoding="utf-8")
    if raw_backup is not None:
        backup_file.write_text(raw_backup, encoding="utf-8")
    env_vars = {"PF_LOAD_DEFAULT": "3", **env_vars}
    env = [f"$env:ProgramData = '{program_data}'"] + [f"$env:{k} = '{v}'" for k, v in env_vars.items()]
    result, ran = _run_stubbed(getattr(_m16(MW_ID), field), env + MW_STUBS, MW_STUBBED)
    return result, ran, backup_file


def _backup_map(path):
    return {e["Path"]: e["LoadBehavior"] for e in json.loads(path.read_text(encoding="utf-8-sig"))}


def test_m16_machine_wide_catalog_contract():
    action = _m16(MW_ID)
    assert action.risk == RiskLevel.MODERATE and action.exclude_from_select_all is True
    assert action.undo_command is not None
    # Machine-wide: never touches the signed-in user's hive (that is the other action).
    assert "HKCU" not in action.command and "__pfUserHive" not in action.command
    assert "WOW6432Node" in action.command
    for app in ("Outlook", "Word", "Excel", "PowerPoint"):
        assert app in action.command
    assert "VŠETKÝCH" in action.description_sk and "EVERY user" in action.description_en


def test_m16_machine_wide_disables_third_party_in_both_registry_views_and_backs_up_per_key(tmp_path):
    result, ran, backup = _run_machine_wide(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Disabled 3 of 3 third-party machine-wide Office add-in(s), failed 0." in result.stdout
    assert sorted(ran) == sorted(f"STUB Set-ItemProperty {k} 0" for k in MW_THIRD_PARTY), ran
    assert not any("Microsoft.VbaAddin" in line or "TeamsAddin" in line or "UCAddin" in line for line in ran)
    assert _backup_map(backup) == {k: 3 for k in MW_THIRD_PARTY}


def test_m16_machine_wide_backup_is_first_run_only_per_key(tmp_path):
    # A re-run reads 0 everywhere: it must not overwrite an earlier backup ...
    first = {k: 3 for k in MW_THIRD_PARTY}
    result, _, backup = _run_machine_wide(
        tmp_path, backup=[{"Path": k, "LoadBehavior": v} for k, v in first.items()], PF_LOAD_DEFAULT="0",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Earlier backup kept" in result.stdout
    assert _backup_map(backup) == first
    # ... but an add-in that was not in it yet is added with its current value.
    partial = [{"Path": MW_THIRD_PARTY[0], "LoadBehavior": 3}]
    result, _, backup = _run_machine_wide(tmp_path, backup=partial, PF_LOAD_DEFAULT="0")
    assert result.returncode == 0, result.stdout + result.stderr
    assert _backup_map(backup) == {MW_THIRD_PARTY[0]: 3, MW_THIRD_PARTY[1]: 0, MW_THIRD_PARTY[2]: 0}


def test_m16_machine_wide_lists_failed_writes_and_fails(tmp_path):
    result, _, _ = _run_machine_wide(tmp_path, PF_FAIL="Contoso.Sync")
    assert result.returncode == 1, result.stdout + result.stderr
    assert "Disabled 2 of 3 third-party machine-wide Office add-in(s), failed 1." in result.stdout
    assert "Failed: " + MW_THIRD_PARTY[2] + " (" in result.stdout


@pytest.mark.parametrize("app", ["OUTLOOK", "WINWORD", "EXCEL", "POWERPNT"])
def test_m16_machine_wide_refuses_while_an_office_app_runs(tmp_path, app):
    result, ran, backup = _run_machine_wide(tmp_path, PF_RUNNING=app)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "Nothing was changed." in result.stdout
    assert ran == [] and not backup.exists()


def test_m16_machine_wide_with_nothing_to_disable_changes_nothing_and_writes_no_backup(tmp_path):
    result, ran, backup = _run_machine_wide(tmp_path, PF_EMPTY="1")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "No third-party machine-wide Office add-ins found." in result.stdout
    assert ran == [] and not backup.exists()


def test_m16_machine_wide_refuses_without_a_valid_backup(tmp_path):
    result, ran, backup = _run_machine_wide(tmp_path, raw_backup="{not json")
    assert result.returncode == 1, result.stdout + result.stderr
    assert "refusing to change add-ins without a valid backup" in result.stdout
    assert ran == [] and backup.read_text(encoding="utf-8") == "{not json"


def test_m16_machine_wide_undo_restores_the_backed_up_values(tmp_path):
    backup = [{"Path": MW_THIRD_PARTY[0], "LoadBehavior": 3}, {"Path": MW_THIRD_PARTY[1], "LoadBehavior": 2},
              {"Path": MW_THIRD_PARTY[2], "LoadBehavior": None}]
    result, ran, _ = _run_machine_wide(tmp_path, field="undo_command", backup=backup, PF_LOAD_DEFAULT="0")
    assert result.returncode == 0, result.stdout + result.stderr
    assert sorted(ran) == sorted([
        f"STUB Set-ItemProperty {MW_THIRD_PARTY[0]} 3", f"STUB Set-ItemProperty {MW_THIRD_PARTY[1]} 2",
        f"STUB Remove-ItemProperty {MW_THIRD_PARTY[2]}",
    ]), ran
    assert "Restored 3 of 3" in result.stdout and "failed 0" in result.stdout


def test_m16_machine_wide_undo_reports_failures_and_uninstalled_add_ins(tmp_path):
    backup = [{"Path": k, "LoadBehavior": 3} for k in MW_THIRD_PARTY]
    result, ran, _ = _run_machine_wide(tmp_path, field="undo_command", backup=backup, PF_GONE="Excel", PF_FAIL="Acme.Addin")
    assert result.returncode == 1, result.stdout + result.stderr
    assert "Restored 0 of 3 machine-wide Office add-in LoadBehavior value(s), failed 2, no longer installed 1." in result.stdout
    assert not any("Contoso.Sync" in line for line in ran)


def test_m16_machine_wide_undo_without_a_backup_says_so(tmp_path):
    result, ran, _ = _run_machine_wide(tmp_path, field="undo_command")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "No backup found." in result.stdout and ran == []
