# tests/test_m02_catalog.py
from pathlib import Path

from portablefix.models import ModuleCategory, RiskLevel
from portablefix.module_engine import load_module

CATALOG_PATH = Path(__file__).resolve().parent.parent / "Modules" / "m02_cleanup" / "actions.yaml"


def test_m02_catalog_loads_24_actions_all_with_preview():
    module = load_module(CATALOG_PATH)
    assert module.module_id == "m02_cleanup"
    assert len(module.actions) == 24
    assert all(a.preview_command for a in module.actions)


def test_m02_catalog_risk_distribution():
    module = load_module(CATALOG_PATH)
    by_risk = {}
    for action in module.actions:
        by_risk.setdefault(action.risk, []).append(action.id)
    assert len(by_risk[RiskLevel.SAFE]) == 11
    assert len(by_risk[RiskLevel.MODERATE]) == 8
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


def test_m02_user_temp_never_deletes_claude_code_scratch_data():
    # AI coding agents like Claude Code keep per-session scratch state at
    # %TEMP%\claude\<project>\<session-id>\ - a top-level folder directly
    # under %TEMP% that a wildcard "clean temp files" pass would otherwise
    # wipe, including the currently-running session's own data. This was
    # the actual (long unexplained) cause of a "mystery" scratch-file-loss
    # bug hit repeatedly during this project's own development.
    module = load_module(CATALOG_PATH)
    action = next(a for a in module.actions if a.id == "user_temp")
    for command in (action.command, action.preview_command):
        assert "claude" in command


def test_m02_user_temp_never_deletes_other_ai_agent_scratch_data():
    # Same evidence class as the Claude Code bug: GitHub Copilot CLI and
    # OpenAI's Codex CLI keep live session state directly under a top-level
    # %TEMP%\<toolname>\ folder while running, and context-mode (an MCP
    # plugin used in this very project's own sessions) keeps a live
    # context-mode-guidance-s-<session-id> scratch folder that was directly
    # observed present - and would have been wiped - on the dev machine.
    module = load_module(CATALOG_PATH)
    action = next(a for a in module.actions if a.id == "user_temp")
    for command in (action.command, action.preview_command):
        assert "copilot-cli" in command
        assert "codex" in command
        assert "context-mode-guidance-s-" in command


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


def test_temp_wiping_actions_skip_recent_items_and_running_programs():
    # A user's freshly installed/extracted portable app (living directly
    # under %TEMP% or %WINDIR%\Temp, which some installers do) got deleted
    # by a SAFE-labeled "clean temp files" run - the exclude list only knew
    # about PortableFix's own artifacts and a handful of named CLI tools, not
    # arbitrary third-party software. Fixed generically: skip any top-level
    # item still owned by a currently running process, and skip anything
    # modified in the last 3 days regardless of what it is.
    module = load_module(CATALOG_PATH)
    by_id = {a.id: a for a in module.actions}
    for action_id in ("user_temp", "system_temp"):
        action = by_id[action_id]
        for command in (action.command, action.preview_command):
            assert "Get-Process" in command, action_id
            assert "$runningNames -notcontains $_.Name" in command, action_id
            assert "$_.LastWriteTime -lt $cutoff" in command, action_id
            assert "AddDays(-3)" in command, action_id


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
        "crash_dumps",
    ):
        command = by_id[action_id].command
        assert "-ErrorVariable errs" in command, action_id
        assert command.rstrip().endswith(")"), action_id
        assert "Write-Output" in command.split("-ErrorVariable errs", 1)[1], action_id


def test_crash_dumps_is_opt_in_because_it_destroys_bsod_evidence():
    # bsod_summary (m01) reads the same Minidump folder - wiping it as part
    # of a blanket "select all" cleanup would destroy the crash evidence.
    module = load_module(CATALOG_PATH)
    by_id = {a.id: a for a in module.actions}
    action = by_id["crash_dumps"]
    assert action.risk == RiskLevel.MODERATE
    assert action.exclude_from_select_all is True
    for path in ("Minidump", "MEMORY.DMP", "LiveKernelReports"):
        assert path in action.command, path
        assert path in action.preview_command, path
    assert "Remove-Item" not in action.preview_command


def test_hidden_large_data_report_never_deletes_anything():
    # An iOS backup may be the only copy of a phone; WSL/Docker disks hold
    # whole Linux installs - report only, in both command and preview.
    module = load_module(CATALOG_PATH)
    by_id = {a.id: a for a in module.actions}
    action = by_id["hidden_large_data_report"]
    assert action.risk == RiskLevel.SAFE
    assert action.preview_command == action.command
    assert "MobileSync" in action.command
    assert ".vhdx" in action.command
    for verb in ("Remove-Item", "Optimize-VHD -", "wsl --unregister"):
        assert verb not in action.command, verb


def test_service_and_explorer_restart_failures_exit_non_zero():
    # "FAILED" in the output alone still exited 0, so the report/history said
    # success while the service - or, for thumbnail_cache, the whole desktop
    # shell - stayed down. The FAILED branch must exit 1; the OK path still
    # ends on the trailing Write-Output checked by the skipped-items test.
    module = load_module(CATALOG_PATH)
    by_id = {a.id: a for a in module.actions}
    for action_id in ("thumbnail_cache", "font_cache", "windows_update_cache"):
        command = by_id[action_id].command
        failed = command.index('"FAILED"')
        condition = command[command.rindex("if (", 0, failed):failed]
        assert "$restarted" in condition or "$startErrs" in condition, action_id
        assert "exit 1" in command[failed:command.index("}", failed)], action_id


def test_shadow_copies_oldest_checks_the_oldest_shadow_age_before_vssadmin():
    # main_window creates one System Restore Point right before the first
    # DESTRUCTIVE/REPAIR action of a batch - often the only shadow copy on
    # C:, so a bare "vssadmin delete shadows /oldest" later in the same batch
    # deleted exactly the safety net just made for it. The command must
    # query Win32_ShadowCopy, bail out on none, and refuse to delete a
    # shadow younger than 24 h - all before vssadmin is ever reached - and
    # must not treat vssadmin's English "No items found" text as success
    # (it is localized on non-English Windows).
    module = load_module(CATALOG_PATH)
    action = next(a for a in module.actions if a.id == "shadow_copies_oldest")
    command = action.command
    assert "Win32_ShadowCopy" in command
    assert "AddHours(-24)" in command
    assert "No items found" not in command
    vssadmin_at = command.index("vssadmin")
    before_vssadmin = command[:vssadmin_at]
    assert "Win32_ShadowCopy" in before_vssadmin
    assert "$shadows.Count -eq 0" in before_vssadmin
    assert "AddHours(-24)" in before_vssadmin
    # A failed CIM query (typically: not elevated) must fail loudly instead of
    # falling through to an unguarded vssadmin call.
    assert "catch {" in before_vssadmin and "needs administrator" in before_vssadmin
    assert "exit $LASTEXITCODE" in command[vssadmin_at:]


def test_shadow_copies_oldest_preview_and_descriptions_state_the_24h_protection():
    module = load_module(CATALOG_PATH)
    action = next(a for a in module.actions if a.id == "shadow_copies_oldest")
    assert "AddHours(-24)" in action.preview_command
    assert "vssadmin" not in action.preview_command
    assert "24 h" in action.description_sk and "24 h" in action.description_en
    assert "práve vytvoril" in action.description_sk
    assert "just created" in action.description_en
