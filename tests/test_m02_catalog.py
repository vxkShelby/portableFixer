# tests/test_m02_catalog.py
from pathlib import Path

from portablefix.models import ModuleCategory, RiskLevel
from portablefix.module_engine import load_module

CATALOG_PATH = Path(__file__).resolve().parent.parent / "Modules" / "m02_cleanup" / "actions.yaml"


def test_m02_catalog_loads_22_actions_all_with_preview():
    module = load_module(CATALOG_PATH)
    assert module.module_id == "m02_cleanup"
    assert len(module.actions) == 22
    assert all(a.preview_command for a in module.actions)


def test_m02_catalog_risk_distribution():
    module = load_module(CATALOG_PATH)
    by_risk = {}
    for action in module.actions:
        by_risk.setdefault(action.risk, []).append(action.id)
    assert len(by_risk[RiskLevel.SAFE]) == 10
    assert len(by_risk[RiskLevel.MODERATE]) == 7
    assert len(by_risk[RiskLevel.DESTRUCTIVE]) == 5


def test_m02_catalog_no_wmic():
    module = load_module(CATALOG_PATH)
    for action in module.actions:
        assert "wmic" not in action.command.lower()


def test_m02_user_temp_never_deletes_the_running_apps_own_files():
    # user_temp wildcard-deletes everything under %TEMP% - it must exclude
    # the running PyInstaller onefile app's own _MEI* extraction folder, its
    # %TEMP%\PortableFix fallback state (Data/Logs/Reports/Backups), and an
    # in-progress auto-update's staging folder/script, or a SAFE-labeled
    # "clean temp files" action can corrupt or delete the app running it.
    module = load_module(CATALOG_PATH)
    action = next(a for a in module.actions if a.id == "user_temp")
    for command in (action.command, action.preview_command):
        assert "-notmatch $excludePattern" in command
        assert "_MEI" in command
        assert "PortableFix" in command
        assert "PortableFixUpdate_" in command
        assert "portablefix_update_" in command
        assert "wmic" not in (action.preview_command or "").lower()


def test_temp_wiping_actions_use_pfprotect_equality_guard():
    # user_temp ($env:TEMP) and system_temp ($env:WINDIR\Temp) both
    # wildcard-delete everything under their root - both must consult the
    # $__pfProtect variable main_window.py injects (the resolved top-level
    # child that contains the running app, or None), via a plain
    # case-insensitive full-path equality check, or a SAFE-labeled cleanup
    # action can delete the folder the app is currently running from.
    module = load_module(CATALOG_PATH)
    by_id = {a.id: a for a in module.actions}
    for action_id in ("user_temp", "system_temp"):
        action = by_id[action_id]
        for command in (action.command, action.preview_command):
            assert "$__pfProtect" in command, action_id
            assert "$_.FullName.Equals($__pfProtect, [StringComparison]::OrdinalIgnoreCase)" in command, action_id


def test_shadow_copies_and_windows_old_commands_avoid_interactive_prompts():
    module = load_module(CATALOG_PATH)
    by_id = {a.id: a for a in module.actions}
    assert "/quiet" in by_id["shadow_copies_oldest"].command
    assert "/D Y" in by_id["windows_old_removal"].command


def test_stale_user_profiles_excludes_null_lastusetime_and_loaded_profiles():
    module = load_module(CATALOG_PATH)
    action = next(a for a in module.actions if a.id == "stale_user_profiles")
    assert "-not $_.Loaded" in action.command
    assert "$_.LastUseTime -ne $null" in action.command
    assert "-not $_.Loaded" in action.preview_command
    assert "$_.LastUseTime -ne $null" in action.preview_command


def test_m02_catalog_has_cleanup_category():
    module = load_module(CATALOG_PATH)
    assert module.category == ModuleCategory.CLEANUP


def test_deletion_actions_report_skipped_locked_items_and_exit_zero():
    # Locked/in-use files are normal on a live system; suppressed errors must
    # not flip the whole action to exit 1 with no explanation. The pattern:
    # -ErrorVariable collects suppressed errors, a trailing Write-Output
    # reports the count and makes the last statement succeed (exit 0).
    module = load_module(CATALOG_PATH)
    by_id = {a.id: a for a in module.actions}
    for action_id in (
        "user_temp",
        "system_temp",
        "recycle_bin",
        "prefetch",
        "wer_reports",
        "cbs_logs",
        "thumbnail_cache",
        "font_cache",
        "windows_update_cache",
        "windows_old_removal",
        "stale_user_profiles",
    ):
        command = by_id[action_id].command
        assert "-ErrorVariable errs" in command, action_id
        assert command.rstrip().endswith(")"), action_id
        assert "Write-Output" in command.split("-ErrorVariable errs", 1)[1], action_id
