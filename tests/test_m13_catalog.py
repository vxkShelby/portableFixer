import base64
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from portablefix import target_user
from portablefix.executor import build_execution_plan
from portablefix.models import ModuleCategory, RiskLevel
from portablefix.module_engine import load_module

CATALOG_PATH = Path(__file__).resolve().parent.parent / "Modules" / "m13_debloat" / "actions.yaml"


def test_m13_catalog_loads_22_actions_in_cleanup_category():
    module = load_module(CATALOG_PATH)
    assert module.module_id == "m13_debloat"
    assert module.category == ModuleCategory.CLEANUP
    assert len(module.actions) == 22


def test_m13_catalog_risk_distribution():
    module = load_module(CATALOG_PATH)
    by_risk = {}
    for action in module.actions:
        by_risk.setdefault(action.risk, []).append(action.id)
    assert by_risk[RiskLevel.SAFE] == ["debloat_list_installed", "debloat_disable_feedback"]
    assert set(by_risk[RiskLevel.MODERATE]) == {
        "debloat_remove_promo_apps",
        "debloat_disable_telemetry",
        "debloat_disable_suggestions",
        "debloat_remove_onedrive",
        "debloat_disable_web_search",
        "debloat_disable_copilot",
        "debloat_disable_widgets",
        "debloat_disable_advertising_id",
        "debloat_remove_xbox_identity",
        "debloat_disable_diagtrack",
        "debloat_disable_ceip_tasks",
        "debloat_disable_fast_startup",
        "debloat_disable_explorer_ads",
        "debloat_block_app_reinstall",
        "debloat_disable_recall_clicktodo",
        "debloat_disable_activity_history",
        "debloat_disable_location",
        "debloat_disable_lockscreen_spotlight",
        "debloat_disable_tailored_experiences",
    }
    assert by_risk[RiskLevel.DESTRUCTIVE] == ["debloat_remove_provisioned"]


def test_m13_registry_tweaks_have_undo_commands_removals_do_not():
    module = load_module(CATALOG_PATH)
    by_id = {a.id: a for a in module.actions}
    for undoable in (
        "debloat_disable_telemetry",
        "debloat_disable_suggestions",
        "debloat_disable_web_search",
        "debloat_disable_copilot",
        "debloat_disable_widgets",
        "debloat_disable_advertising_id",
        "debloat_disable_diagtrack",
        "debloat_disable_ceip_tasks",
        "debloat_disable_fast_startup",
        "debloat_disable_explorer_ads",
        "debloat_block_app_reinstall",
        "debloat_disable_recall_clicktodo",
        *HKLM_POLICY_ACTIONS,
    ):
        assert by_id[undoable].undo_command is not None, undoable
    for not_undoable in (
        "debloat_list_installed",
        "debloat_remove_promo_apps",
        "debloat_remove_provisioned",
        "debloat_remove_onedrive",
        "debloat_remove_xbox_identity",
    ):
        assert by_id[not_undoable].undo_command is None, not_undoable


def test_m13_removal_actions_never_touch_edge_onedrive_defender_or_store():
    module = load_module(CATALOG_PATH)
    by_id = {a.id: a for a in module.actions}
    for action_id in ("debloat_remove_promo_apps", "debloat_remove_provisioned"):
        command = by_id[action_id].command.lower()
        for forbidden in ("edge", "onedrive", "defender", "windowsstore", "storepurchaseapp", "quickassist"):
            assert forbidden not in command, f"{action_id} touches {forbidden}"


def test_m13_removal_actions_share_the_same_package_list():
    module = load_module(CATALOG_PATH)
    by_id = {a.id: a for a in module.actions}

    def extract_list(command: str) -> str:
        start = command.index("@(")
        end = command.index(")", start)
        return command[start : end + 1]

    assert extract_list(by_id["debloat_remove_promo_apps"].command) == extract_list(
        by_id["debloat_remove_provisioned"].command
    )


def test_m13_hklm_policy_writes_verify_they_actually_worked():
    # Set-ItemProperty on an HKLM policy key throws a non-terminating
    # SecurityException without administrator - the command must use
    # -EA Stop + try/catch so it can't claim success on a silent no-op.
    module = load_module(CATALOG_PATH)
    by_id = {a.id: a for a in module.actions}
    for action_id in ("debloat_disable_telemetry", "debloat_disable_widgets", *HKLM_POLICY_ACTIONS):
        command = by_id[action_id].command
        assert "-EA Stop" in command, action_id
        assert "exit 1" in command, action_id


# The research-debloat-additions.md gaps: one HKLM policy key each.
HKLM_POLICY_ACTIONS = {
    "debloat_disable_activity_history": "HKLM:\\SOFTWARE\\Policies\\Microsoft\\Windows\\System",
    "debloat_disable_location": "HKLM:\\SOFTWARE\\Policies\\Microsoft\\Windows\\LocationAndSensors",
    "debloat_disable_lockscreen_spotlight": "HKLM:\\SOFTWARE\\Policies\\Microsoft\\Windows\\CloudContent",
    "debloat_disable_tailored_experiences": "HKLM:\\SOFTWARE\\Policies\\Microsoft\\Windows\\CloudContent",
    "debloat_disable_feedback": "HKLM:\\SOFTWARE\\Policies\\Microsoft\\Windows\\DataCollection",
}


def test_m13_hklm_policy_actions_never_recreate_a_shared_key():
    # New-Item -Force on an existing registry key wipes all its values: these
    # keys hold other policies (CloudContent is shared by two of them).
    for action_id in HKLM_POLICY_ACTIONS:
        action = _m13_action(action_id)
        assert "if (-not (Test-Path -Path $k)) { New-Item" in action.command, action_id
        # The loader refuses a check_command on a SAFE action.
        assert bool(action.check_command) == (action.risk != RiskLevel.SAFE), action_id
        assert "exit" not in action.undo_command, action_id


@pytest.mark.parametrize("action_id", HKLM_POLICY_ACTIONS)
@pytest.mark.parametrize("field", ["command", "undo_command"])
def test_m13_hklm_policy_actions_touch_only_their_policy_key(action_id, field):
    result, registry = _run_user_hive_command(getattr(_m13_action(action_id), field), "HKCU:")
    assert result.returncode == 0, result.stdout + result.stderr
    assert registry, result.stdout
    assert set(registry) == {HKLM_POLICY_ACTIONS[action_id]}, registry


# --- research G25: per-user settings go to the signed-in user's hive --------

CLIENT_SID = "S-1-5-21-1111-2222-3333-1002"
CLIENT_HIVE = f"Registry::HKEY_USERS\\{CLIENT_SID}"
# Every m13 action that writes the user's own registry - HKCU in the
# catalog before G25, $__pfUserHive (fallback HKCU:) now.
USER_HIVE_ACTIONS = (
    "debloat_disable_suggestions",
    "debloat_disable_web_search",
    "debloat_disable_copilot",
    "debloat_disable_advertising_id",
    "debloat_disable_explorer_ads",
    "debloat_block_app_reinstall",
    "debloat_disable_recall_clicktodo",
)
REGISTRY_STUBS = ("New-Item", "Set-ItemProperty", "New-ItemProperty", "Remove-ItemProperty", "Get-ItemProperty")
STUB_GUARD_EXIT = 97
# What the stubbed Get-Content hands the undo commands as their backup.
BACKUP_JSON = (
    '{"SilentInstalledAppsEnabled":1,"Enabled":1,"BingSearchEnabled":1,"DisableSearchBoxSuggestions":0,'
    '"HideRecommendedSection":0,"ShowSyncProviderNotifications":1}'
)


def _m13_action(action_id):
    return next(a for a in load_module(CATALOG_PATH).actions if a.id == action_id)


def _pwsh_or_skip() -> str:
    exe = os.environ.get("PORTABLEFIX_TEST_PWSH") or shutil.which("powershell") or shutil.which("pwsh")
    if not exe:
        pytest.skip("no PowerShell available")
    return exe


def _run_user_hive_command(command: str, hive: str, hive_loaded: bool = True, target=None, keys_exist: bool = True):
    """Runs `command` the way the executor does (prelude included when
    `target` is given) with every registry and file cmdlet stubbed: each
    registry call prints "REG <cmdlet> <path>" straight to the console (a
    command's own "| Out-Null" must not hide it), nothing touches the real
    registry. Test-Path answers `hive_loaded` for the hive, `keys_exist`
    otherwise."""
    stubs = [
        f"$global:__pfTestHive = '{hive}'",
        f"$global:__pfTestLoaded = ${str(hive_loaded).lower()}",
        f"$global:__pfTestKeysExist = ${str(keys_exist).lower()}",
        "function Test-Path { [CmdletBinding()] param([Parameter(Position=0)] $Path, $LiteralPath) "
        "if ($Path -eq $global:__pfTestHive) { return $global:__pfTestLoaded }; $global:__pfTestKeysExist }",
        "function Get-Content { [CmdletBinding()] param([Parameter(Position=0)] $Path, [switch] $Raw) "
        f"'{BACKUP_JSON}' }}",
        "function Set-Content { [CmdletBinding()] param([Parameter(ValueFromPipeline=$true)] $Value, $Path, $Encoding) "
        "begin { [Console]::Out.WriteLine('FILE ' + $Path) } process { } }",
    ]
    for name in REGISTRY_STUBS:
        # Writes also report "VAL <name>=<value>" so a test can check what
        # was written, not only where.
        value_line = "; [Console]::Out.WriteLine('VAL ' + $Name + '=' + $Value)" if name.startswith(("Set-", "New-ItemProperty")) else ""
        stubs.append(
            f"function {name} {{ [CmdletBinding()] param([Parameter(Position=0)] $Path, $Name, $Value, $Type, "
            f"$PropertyType, $ItemType, [switch] $Force) [Console]::Out.WriteLine('REG {name} ' + $Path){value_line} }}"
        )
    stubbed = ("Test-Path", "Get-Content", "Set-Content") + REGISTRY_STUBS
    guard = (
        "foreach ($n in " + ", ".join(f"'{n}'" for n in stubbed) + ") { "
        "if ((Get-Command $n -EA SilentlyContinue | Select-Object -First 1).CommandType -ne 'Function') "
        f"{{ exit {STUB_GUARD_EXIT} }} }}"
    )
    plan = build_execution_plan(command, dry_run=False, target_user=target)
    script = "; ".join(stubs + [guard, plan.argv[-1]])
    result = subprocess.run(
        [_pwsh_or_skip(), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert result.returncode != STUB_GUARD_EXIT, "a cmdlet was not stubbed - refusing to run the real one"
    registry = [
        line.split(" ", 2)[2] for line in result.stdout.splitlines()
        if line.startswith("REG ") and ("HK" in line or "Registry::" in line)
    ]
    return result, registry


# Every action that creates a registry key (folders use New-Item -ItemType Directory).
KEY_CREATING_ACTIONS = tuple(
    a.id for a in load_module(CATALOG_PATH).actions if "New-Item -Path" in a.command + (a.undo_command or "")
)


def test_m13_key_creating_actions_are_the_known_ones():
    assert set(KEY_CREATING_ACTIONS) == {
        "debloat_disable_telemetry",
        "debloat_disable_web_search",
        "debloat_disable_copilot",
        "debloat_disable_widgets",
        "debloat_disable_advertising_id",
        "debloat_disable_recall_clicktodo",
        *HKLM_POLICY_ACTIONS,
    }


@pytest.mark.parametrize("action_id", KEY_CREATING_ACTIONS)
@pytest.mark.parametrize("keys_exist", [True, False])
def test_m13_new_item_only_creates_a_missing_key(action_id, keys_exist):
    # New-Item -Force on an existing registry key deletes all its values and
    # subkeys - e.g. telemetry would wipe feedback's DataCollection policy.
    result, _ = _run_user_hive_command(_m13_action(action_id).command, "HKCU:", keys_exist=keys_exist)
    assert result.returncode == 0, result.stdout + result.stderr
    created = [line for line in result.stdout.splitlines() if line.startswith("REG New-Item ") and "HK" in line]
    assert bool(created) == (not keys_exist), created


def _client():
    return target_user.TargetUser(
        status=target_user.DIFFERENT, process_sid="S-1-5-21-1111-2222-3333-1001", process_user="PC\\technik",
        target_sid=CLIENT_SID, target_user="PC\\klient", session_id=1,
    )


def test_m13_no_literal_hkcu_path_is_left():
    # Over-the-shoulder elevation made HKCU:\ the technician's profile.
    for action in load_module(CATALOG_PATH).actions:
        for text in (action.command, action.undo_command or "", action.preview_command or ""):
            assert "HKCU:\\" not in text, action.id


def test_m13_user_hive_actions_are_exactly_the_former_hkcu_ones():
    using = {a.id for a in load_module(CATALOG_PATH).actions if "$__pfUserHive" in a.command}
    assert using == set(USER_HIVE_ACTIONS)
    for action_id in USER_HIVE_ACTIONS:
        action = _m13_action(action_id)
        for text in (action.command, action.undo_command):
            # Falls back to HKCU: when run outside PortableFix (no prelude).
            assert text.startswith("$uh = if ($__pfUserHive) { $__pfUserHive } else { 'HKCU:' }; "), action_id
            assert "Profile hive not loaded, skipped" in text, action_id
        # undo.ps1 runs every step in one script: an exit there would stop
        # all the steps after it.
        assert "exit" not in action.undo_command, action_id


@pytest.mark.parametrize("action_id", USER_HIVE_ACTIONS)
@pytest.mark.parametrize("field", ["command", "undo_command"])
def test_m13_user_settings_go_to_the_target_hive(action_id, field):
    result, registry = _run_user_hive_command(getattr(_m13_action(action_id), field), CLIENT_HIVE, target=_client())
    assert result.returncode == 0, result.stdout + result.stderr
    assert registry, result.stdout
    for path in registry:
        assert path.startswith(CLIENT_HIVE + "\\") or path.startswith("HKLM:\\"), path
    assert any(path.startswith(CLIENT_HIVE + "\\") for path in registry)


@pytest.mark.parametrize("action_id", USER_HIVE_ACTIONS)
def test_m13_without_prelude_falls_back_to_hkcu(action_id):
    result, registry = _run_user_hive_command(_m13_action(action_id).command, "HKCU:")
    assert result.returncode == 0, result.stdout + result.stderr
    assert any(path.startswith("HKCU:\\") for path in registry), registry
    assert not any("HKEY_USERS" in path for path in registry)


@pytest.mark.parametrize("action_id", USER_HIVE_ACTIONS)
def test_m13_signed_out_user_is_skipped_with_failure(action_id):
    result, registry = _run_user_hive_command(
        _m13_action(action_id).command, CLIENT_HIVE, hive_loaded=False, target=_client(),
    )
    assert result.returncode != 0
    assert "Profile hive not loaded, skipped: " + CLIENT_HIVE in result.stdout
    # Nothing changed - not even the HKLM half of a mixed action.
    assert registry == []


@pytest.mark.parametrize("action_id", USER_HIVE_ACTIONS)
def test_m13_undo_for_signed_out_user_skips_without_ending_undo_script(action_id):
    command = _m13_action(action_id).undo_command + "; Write-Output 'NEXT STEP RAN'"
    result, registry = _run_user_hive_command(command, CLIENT_HIVE, hive_loaded=False, target=_client())
    assert "Profile hive not loaded, skipped" in result.stdout
    assert "NEXT STEP RAN" in result.stdout
    assert registry == []


def _written_values(stdout: str) -> dict:
    values = {}
    for line in stdout.splitlines():
        if line.startswith("VAL "):
            name, value = line[4:].split("=", 1)
            values.setdefault(name, set()).add(value)
    return values


def test_m13_recall_policy_forbids_recall_instead_of_allowing_it():
    # AllowRecallEnablement is an allow-policy: writing 1 (as every other
    # value in the loop gets) explicitly ALLOWS Recall. It must be 0; the
    # three disable-policies 1. The HKLM write must fail loudly like the
    # sibling policy actions, not print success after a silent no-op.
    action = _m13_action("debloat_disable_recall_clicktodo")
    for text in (action.command, action.check_command, action.undo_command):
        assert "AllowRecallEnablement = 0" in text
        assert "AllowRecallEnablement = 1" not in text
    assert "-EA Stop" in action.command and "exit 1" in action.command
    result, registry = _run_user_hive_command(action.command, CLIENT_HIVE, target=_client())
    assert result.returncode == 0, result.stdout + result.stderr
    assert _written_values(result.stdout) == {
        "AllowRecallEnablement": {"0"},
        "DisableAIDataAnalysis": {"1"},
        "TurnOffSavingSnapshots": {"1"},
        "DisableClickToDo": {"1"},
    }
    assert any(p.startswith("HKLM:\\") for p in registry) and any(p.startswith(CLIENT_HIVE + "\\") for p in registry)


# --- per-user backups are keyed by the target user's SID -------------------

BACKED_UP_USER_ACTIONS = (
    "debloat_disable_suggestions",
    "debloat_disable_web_search",
    "debloat_disable_advertising_id",
    "debloat_disable_explorer_ads",
)


def _backup_files(stdout: str) -> list[str]:
    return [line[5:] for line in stdout.splitlines() if line.startswith("FILE ")]


@pytest.mark.parametrize("action_id", BACKED_UP_USER_ACTIONS)
def test_m13_user_backups_are_named_by_the_target_sid(action_id):
    # One fixed backup path meant: run for user A, later for user B, and
    # B's undo restored A's values into B's hive.
    paths = []
    for sid in ("S-1-5-21-1111-2222-3333-1002", "S-1-5-21-1111-2222-3333-1003"):
        target = target_user.TargetUser(
            status=target_user.DIFFERENT, process_sid="S-1-5-21-1111-2222-3333-1001", process_user="PC\\technik",
            target_sid=sid, target_user="PC\\klient", session_id=1,
        )
        result, _ = _run_user_hive_command(
            _m13_action(action_id).command, target.hive, target=target, keys_exist=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        files = _backup_files(result.stdout)
        assert len(files) == 1 and sid in files[0], files
        paths.append(files[0])
    assert len(set(paths)) == 2
    # The undo reads the same SID-keyed file.
    undo = _m13_action(action_id).undo_command
    assert "_backup_' + $sid + '.json'" in undo and "$__pfUserSid" in undo


def test_m13_explorer_ads_no_longer_writes_the_11_se_only_policy():
    action = _m13_action("debloat_disable_explorer_ads")
    for text in (action.command, action.undo_command):
        assert "HideRecommendedSection" not in text
        assert "HKLM" not in text


# --- debloat_remove_onedrive: refuses over-the-shoulder, checks the exit code -

IDENTITY_CALL = "[Security.Principal.WindowsIdentity]::GetCurrent()"
PROCESS_SID = "S-1-5-21-1111-2222-3333-1001"


def _same_user():
    return target_user.TargetUser(
        status=target_user.SAME, process_sid=PROCESS_SID, process_user="PC\\technik",
        target_sid=PROCESS_SID, target_user="PC\\technik", session_id=1,
    )


def _run_onedrive(target, setup_exit: int):
    """OneDriveSetup, Stop-Process and the identity lookup stubbed; every
    stub prints "STUB <cmdlet> ..." so the test sees what ran."""
    command = _m13_action("debloat_remove_onedrive").command.replace(IDENTITY_CALL, "(Pf-WindowsIdentity)")
    assert "WindowsIdentity]" not in command
    stubbed = ("Test-Path", "Stop-Process", "Start-Process", "Pf-WindowsIdentity")
    stubs = [
        f"function Pf-WindowsIdentity {{ [pscustomobject]@{{ User = [pscustomobject]@{{ Value = '{PROCESS_SID}' }} }} }}",
        "function Test-Path { [CmdletBinding()] param([Parameter(Position=0)] $Path, $LiteralPath) $true }",
        "function Stop-Process { [CmdletBinding()] param($Name, [switch] $Force) [Console]::Out.WriteLine('STUB Stop-Process ' + $Name) }",
        "function Start-Process { [CmdletBinding()] param([Parameter(Position=0)] $FilePath, $ArgumentList, [switch] $Wait, [switch] $PassThru) "
        f"[Console]::Out.WriteLine('STUB Start-Process ' + $FilePath + ' ' + $ArgumentList); [pscustomobject]@{{ ExitCode = {setup_exit} }} }}",
        "foreach ($n in " + ", ".join(f"'{n}'" for n in stubbed) + ") { "
        "if ((Get-Command $n -EA SilentlyContinue | Select-Object -First 1).CommandType -ne 'Function') "
        f"{{ exit {STUB_GUARD_EXIT} }} }}",
    ]
    plan = build_execution_plan(command, dry_run=False, target_user=target)
    result = subprocess.run(
        [_pwsh_or_skip(), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command",
         "; ".join(stubs + [plan.argv[-1]])],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert result.returncode != STUB_GUARD_EXIT, "a cmdlet was not stubbed - refusing to run the real one"
    return result, [line for line in result.stdout.splitlines() if line.startswith("STUB ")]


def test_m13_onedrive_removal_refuses_when_the_signed_in_user_is_someone_else():
    # OneDriveSetup /uninstall removes the per-user install of the account
    # running it - over the shoulder that is the technician's, not the client's.
    result, ran = _run_onedrive(_client(), setup_exit=0)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "Nothing was changed." in result.stdout
    assert ran == []


@pytest.mark.parametrize("setup_exit, expected", [(0, 0), (1, 1)])
def test_m13_onedrive_removal_reports_the_installer_exit_code(setup_exit, expected):
    result, ran = _run_onedrive(_same_user(), setup_exit=setup_exit)
    assert result.returncode == expected, result.stdout + result.stderr
    assert any(line.startswith("STUB Start-Process ") and line.endswith(" /uninstall") for line in ran), ran
    assert ("failed with exit code 1" in result.stdout) == (setup_exit == 1)


# --- Appx removals count successes, not attempts, and target the user ------


def _run_appx_removal(action_id: str, target=None, fail_names=("Bad",)):
    """Get-AppxPackage answers two packages for the first list entry (Good
    and Bad); Remove-AppxPackage raises an error for `fail_names`. Each stub
    reports the -User it was given."""
    command = _m13_action(action_id).command
    fail = ", ".join(f"'{n}'" for n in fail_names)
    stubs = [
        "function Get-AppxPackage { [CmdletBinding()] param($Name, $User) [Console]::Out.WriteLine('STUB Get-AppxPackage user=' + $User); "
        "if ($Name -eq 'Microsoft.XboxIdentityProvider' -or $Name -eq 'Microsoft.549981C3F5F10') { @([pscustomobject]@{ Name = 'Good' }, [pscustomobject]@{ Name = 'Bad' }) } }",
        "function Remove-AppxPackage { [CmdletBinding()] param([Parameter(ValueFromPipeline=$true)] $Package, $User) "
        f"process {{ [Console]::Out.WriteLine('STUB Remove-AppxPackage ' + $Package.Name + ' user=' + $User); if (@({fail}) -contains $Package.Name) {{ Write-Error ('locked: ' + $Package.Name) }} }} }}",
        "foreach ($n in 'Get-AppxPackage', 'Remove-AppxPackage') { "
        "if ((Get-Command $n -EA SilentlyContinue | Select-Object -First 1).CommandType -ne 'Function') "
        f"{{ exit {STUB_GUARD_EXIT} }} }}",
    ]
    plan = build_execution_plan(command, dry_run=False, target_user=target)
    result = subprocess.run(
        [_pwsh_or_skip(), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command",
         "; ".join(stubs + [plan.argv[-1]])],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert result.returncode != STUB_GUARD_EXIT, "a cmdlet was not stubbed - refusing to run the real one"
    return result, [line for line in result.stdout.splitlines() if line.startswith("STUB ")]


@pytest.mark.parametrize("action_id", ["debloat_remove_promo_apps", "debloat_remove_xbox_identity"])
def test_m13_appx_removal_counts_only_real_successes(action_id):
    # $removed++ used to run after Remove-AppxPackage -EA SilentlyContinue,
    # so a package that failed (in use, policy-blocked) counted as removed.
    result, ran = _run_appx_removal(action_id)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Removed packages: 1, errors: 1" in result.stdout
    assert all(line.endswith(" user=") for line in ran), ran


@pytest.mark.parametrize("action_id", ["debloat_remove_promo_apps", "debloat_remove_xbox_identity"])
def test_m13_appx_removal_fails_when_nothing_was_removed_and_errors_occurred(action_id):
    result, _ = _run_appx_removal(action_id, fail_names=("Good", "Bad"))
    assert result.returncode == 1, result.stdout + result.stderr
    assert "Removed packages: 0, errors: 2" in result.stdout


@pytest.mark.parametrize("action_id", ["debloat_remove_promo_apps", "debloat_remove_xbox_identity"])
def test_m13_appx_removal_targets_the_signed_in_user(action_id):
    # Over the shoulder, Get-AppxPackage without -User lists the technician's
    # packages - nothing is removed from the client's account.
    result, ran = _run_appx_removal(action_id, target=_client())
    assert result.returncode == 0, result.stdout + result.stderr
    assert ran and all(line.endswith(" user=" + CLIENT_SID) for line in ran), ran


def test_m13_catalog_parses_in_powershell(tmp_path):
    texts = []
    for action in load_module(CATALOG_PATH).actions:
        for text in (action.command, action.undo_command, action.preview_command):
            if text:
                texts.append(base64.b64encode(text.encode("utf-8")).decode())
    # Through a file: inlined, the catalog outgrows the 32K command-line limit.
    data = tmp_path / "texts.txt"
    data.write_text("\n".join(texts), encoding="ascii")
    script = (
        f"$bad = 0; foreach ($b in (Get-Content -LiteralPath '{data}')) {{ "
        "$text = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($b)); $errs = $null; "
        "[void][System.Management.Automation.Language.Parser]::ParseInput($text, [ref]$null, [ref]$errs); "
        "if ($errs.Count) { $bad++; Write-Output $errs[0].Message } }; exit $bad"
    )
    result = subprocess.run(
        [_pwsh_or_skip(), "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True, text=True, timeout=120, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert result.returncode == 0, result.stdout + result.stderr
