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


def test_m13_catalog_loads_17_actions_in_cleanup_category():
    module = load_module(CATALOG_PATH)
    assert module.module_id == "m13_debloat"
    assert module.category == ModuleCategory.CLEANUP
    assert len(module.actions) == 17


def test_m13_catalog_risk_distribution():
    module = load_module(CATALOG_PATH)
    by_risk = {}
    for action in module.actions:
        by_risk.setdefault(action.risk, []).append(action.id)
    assert by_risk[RiskLevel.SAFE] == ["debloat_list_installed"]
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
    for action_id in ("debloat_disable_telemetry", "debloat_disable_widgets"):
        command = by_id[action_id].command
        assert "-EA Stop" in command, action_id
        assert "exit 1" in command, action_id


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


def _run_user_hive_command(command: str, hive: str, hive_loaded: bool = True, target=None):
    """Runs `command` the way the executor does (prelude included when
    `target` is given) with every registry and file cmdlet stubbed: each
    registry call prints "REG <cmdlet> <path>" straight to the console (a
    command's own "| Out-Null" must not hide it), nothing touches the real
    registry. Test-Path answers `hive_loaded` for the hive, $true otherwise."""
    stubs = [
        f"$global:__pfTestHive = '{hive}'",
        f"$global:__pfTestLoaded = ${str(hive_loaded).lower()}",
        "function Test-Path { [CmdletBinding()] param([Parameter(Position=0)] $Path, $LiteralPath) "
        "if ($Path -eq $global:__pfTestHive) { return $global:__pfTestLoaded }; $true }",
        "function Get-Content { [CmdletBinding()] param([Parameter(Position=0)] $Path, [switch] $Raw) "
        f"'{BACKUP_JSON}' }}",
        "function Set-Content { [CmdletBinding()] param([Parameter(ValueFromPipeline=$true)] $Value, $Path, $Encoding) process { } }",
    ]
    for name in REGISTRY_STUBS:
        stubs.append(
            f"function {name} {{ [CmdletBinding()] param([Parameter(Position=0)] $Path, $Name, $Value, $Type, "
            f"$PropertyType, $ItemType, [switch] $Force) [Console]::Out.WriteLine('REG {name} ' + $Path) }}"
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


def test_m13_catalog_parses_in_powershell():
    texts = []
    for action in load_module(CATALOG_PATH).actions:
        for text in (action.command, action.undo_command, action.preview_command):
            if text:
                texts.append(base64.b64encode(text.encode("utf-8")).decode())
    script = (
        "$bad = 0; foreach ($b in @(" + ",".join(f"'{t}'" for t in texts) + ")) { "
        "$text = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($b)); $errs = $null; "
        "[void][System.Management.Automation.Language.Parser]::ParseInput($text, [ref]$null, [ref]$errs); "
        "if ($errs.Count) { $bad++; Write-Output $errs[0].Message } }; exit $bad"
    )
    result = subprocess.run(
        [_pwsh_or_skip(), "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True, text=True, timeout=120, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert result.returncode == 0, result.stdout + result.stderr
