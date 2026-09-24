# tests/test_m02_catalog.py
import re
from pathlib import Path

from portablefix.models import ModuleCategory, RiskLevel
from portablefix.module_engine import load_module
from portablefix.preflight import is_long_action

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
        assert "^PortableFixUpdate|" in command
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
    # takeown only prompts (Y/N per folder it cannot list) with /R, which
    # the command no longer uses - see the next test.
    assert "takeown /F $p /A |" in by_id["windows_old_removal"].command


def test_windows_old_and_upgrade_leftovers_never_take_ownership_recursively():
    # takeown /R and icacls /T walk the tree themselves, and neither tool's
    # documentation promises to stop at a junction; takeown has no switch
    # to act on a link itself. A folder a standard user pre-created with
    # junctions inside would get System32 re-owned and re-ACLed. Ownership
    # and ACLs are reset one real folder at a time (tests/test_safe_delete.py
    # runs it), and only under a root owned by SYSTEM, TrustedInstaller or
    # Administrators - checked by SID, so a localized account name can
    # never fail or fool it.
    module = load_module(CATALOG_PATH)
    by_id = {a.id: a for a in module.actions}
    for action_id in ("windows_old_removal", "windows_upgrade_leftovers"):
        command = by_id[action_id].command
        assert re.search(r"(?i)takeown[^;|]*\s/R\b", command) is None, action_id
        assert re.search(r"(?i)icacls[^;|]*\s/T\b", command) is None, action_id
        assert "icacls $p /reset /L /C /Q" in command, action_id
        assert ".GetOwner([Security.Principal.SecurityIdentifier]).Value" in command, action_id
        assert ".Owner" not in command, action_id
        for sid in ("'S-1-5-18'", "'S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464'", "'S-1-5-32-544'"):
            assert sid in command, (action_id, sid)
        assert "exit 1" in command, action_id
        # The owner check comes before any ownership change or delete of a
        # real folder (a root that is a link is only unlinked, earlier).
        body = command[command.index("$trusted = ") :]
        assert body.index("Get-PfOwnerSid $i.FullName") < body.index("Grant-PfAdminTree $i.FullName") < body.rindex("Remove-PfSafe $p"), action_id



def test_windows_old_and_upgrade_leftovers_declare_long_timeouts_and_print_progress():
    # Two native tool launches per folder over tens of thousands of folders,
    # then a silent delete: the default 300 s inactivity / 2 h cap would
    # kill both actions partway on a real machine. The long timeouts also
    # make pre-flight treat them as long-running (battery warning).
    module = load_module(CATALOG_PATH)
    by_id = {a.id: a for a in module.actions}
    for action_id in ("windows_old_removal", "windows_upgrade_leftovers"):
        action = by_id[action_id]
        assert action.inactivity_timeout_sec == 3600, action_id
        assert action.hard_cap_sec == 21600, action_id
        assert is_long_action(action), action_id
        assert "' folder(s) so far...'" in action.command, action_id
        # The walk (not only the end of it) writes the progress line.
        walk = action.command[action.command.index("function Grant-PfAdminTree") : action.command.index("$trusted = ")]
        assert "($done % 1000 -eq 0) -or ($sw.Elapsed.TotalSeconds -ge 10)" in walk, action_id

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
    # -ErrorVariable collects suppressed errors (or, for tree deletes, the
    # Remove-PfSafe helper returns its failure count - see
    # test_safe_delete.py), a trailing Write-Output reports the count and
    # makes the last statement succeed (exit 0).
    module = load_module(CATALOG_PATH)
    by_id = {a.id: a for a in module.actions}
    for action_id in (
        "recycle_bin",
        "prefetch",
        "cbs_logs",
        "thumbnail_cache",
        "font_cache",
        "stale_user_profiles",
    ):
        command = by_id[action_id].command
        assert "-ErrorVariable errs" in command, action_id
        assert command.rstrip().endswith(")"), action_id
        assert "Write-Output" in command.split("-ErrorVariable errs", 1)[1], action_id
    for action_id in (
        "user_temp",
        "system_temp",
        "wer_reports",
        "windows_update_cache",
        "windows_old_removal",
        "crash_dumps",
    ):
        command = by_id[action_id].command
        assert "$skipped += Remove-PfSafe" in command or "$skipped = Remove-PfSafe" in command, action_id
        assert command.rstrip().endswith(")"), action_id
        assert '"Skipped locked/in-use items: " + $skipped' in command, action_id


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


def test_m02_user_temp_exclude_pattern_protects_the_update_logs_and_downloads():
    # %TEMP%\PortableFixUpdate holds the updater's launch diagnostics - the
    # evidence for why an update failed; '^PortableFixUpdate_' let a 3-day
    # temp cleanup delete it.
    import re

    module = load_module(CATALOG_PATH)
    action = next(a for a in module.actions if a.id == "user_temp")
    for command in (action.command, action.preview_command):
        pattern = re.search(r"\$excludePattern = '([^']*)'", command).group(1)
        for name in ("PortableFixUpdate", "PortableFixUpdate_abc123", "_MEI12345", "PortableFix"):
            assert re.search(pattern, name), name
        assert not re.search(pattern, "SomeOtherApp")
