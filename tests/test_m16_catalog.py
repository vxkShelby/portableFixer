from pathlib import Path

from portablefix.models import ModuleCategory, RiskLevel
from portablefix.module_engine import load_module

CATALOG_PATH = Path(__file__).resolve().parent.parent / "Modules" / "m16_office_repair" / "actions.yaml"


def test_m16_catalog_loads_7_actions_in_repair_category():
    module = load_module(CATALOG_PATH)
    assert module.module_id == "m16_office_repair"
    assert module.category == ModuleCategory.REPAIR
    assert len(module.actions) == 7


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
    plan = build_execution_plan(command, dry_run=False, target_user=target)
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
        "office_reset_outlook_profile",
    ):
        assert by_id[default_timeout].inactivity_timeout_sec is None, default_timeout
