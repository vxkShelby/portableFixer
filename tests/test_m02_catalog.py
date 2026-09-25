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


# --- browser_cache_sweep: every profile of every Chromium browser (G23) ------
#
# The sweep used to hard-code "User Data\Default" of Chrome and Edge. It now
# enumerates every profile (Default, Profile N, Guest Profile, System
# Profile... - every folder under User Data holding a Preferences file, plus
# Default itself) of Chrome, Edge, Brave, Vivaldi, Opera and Opera GX, and
# every Firefox profile's cache2. Opera keeps its main profile directly in
# %APPDATA%\Opera Software\Opera Stable (Preferences, Code Cache, GPUCache,
# Service Worker there) with the HTTP Cache in the %LOCALAPPDATA% mirror of
# the same path, and extra ("side") profiles in _side_profiles\<id> under
# both. Only the five cache folders are emptied; a browser whose process is
# running is skipped and named; a junction/symlink anywhere on the way to a
# cache folder is never followed. The pwsh runs below use fake
# LOCALAPPDATA/APPDATA trees and a stubbed Get-Process.

import json as _json  # noqa: E402
import os as _os  # noqa: E402
import subprocess as _subprocess  # noqa: E402

import pytest as _pytest  # noqa: E402

from test_safe_delete import (  # noqa: E402
    PARSE_CHECKER,
    PS51_REMOVE_ITEM,
    STUB_GUARD_EXIT,
    _assert_victim_intact,
    _link,
    _powershell_or_skip,
    _ps_quote,
    _victim,
)

CHROMIUM_CACHE_DIRS = ("Cache", "Code Cache", "GPUCache", "Service Worker/CacheStorage", "Service Worker/ScriptCache")
# Profile data a cache sweep must never touch.
PROFILE_KEEPERS = (
    "Cookies",
    "Network/Cookies",
    "Login Data",
    "History",
    "Bookmarks",
    "Web Data",
    "Extensions/abcdefgh/1.0/manifest.json",
    "Local Storage/leveldb/000003.log",
    "Service Worker/Database/MANIFEST",
    "Secure Preferences",
)
MIB = 1 << 20


def _sweep_action():
    return next(a for a in load_module(CATALOG_PATH).actions if a.id == "browser_cache_sweep")


def _fill_cache(folder: Path) -> Path:
    blob = folder / "sub" / "blob.bin"
    blob.parent.mkdir(parents=True, exist_ok=True)
    blob.write_bytes(b"\0" * MIB)
    return blob


def _chromium_profile(profile: Path, prefs: bool = True, cache_dirs=CHROMIUM_CACHE_DIRS) -> dict:
    profile.mkdir(parents=True, exist_ok=True)
    if prefs:
        (profile / "Preferences").write_text("{}", encoding="utf-8")
    keepers = []
    for rel in PROFILE_KEEPERS:
        p = profile / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("keep", encoding="utf-8")
        keepers.append(p)
    blobs = [_fill_cache(profile / rel) for rel in cache_dirs]
    return {"blobs": blobs, "keepers": keepers, "caches": [profile / rel for rel in cache_dirs]}


def _cache_only(folder: Path) -> dict:
    return {"blobs": [_fill_cache(folder)], "keepers": [], "caches": [folder]}


def _assert_swept(info: dict) -> None:
    for blob in info["blobs"]:
        assert not blob.exists(), blob
    for cache in info["caches"]:
        assert cache.is_dir(), cache  # the folder stays, only its contents go
    for keeper in info["keepers"]:
        assert keeper.read_text(encoding="utf-8") == "keep", keeper


def _assert_untouched(info: dict) -> None:
    for blob in info["blobs"]:
        assert blob.exists(), blob
    for keeper in info["keepers"]:
        assert keeper.exists(), keeper


def _browser_tree(tmp_path: Path) -> dict:
    local = tmp_path / "Local"
    roaming = tmp_path / "Roaming"
    chrome = local / "Google" / "Chrome" / "User Data"
    opera_r = roaming / "Opera Software" / "Opera Stable"
    opera_l = local / "Opera Software" / "Opera Stable"
    gx_r = roaming / "Opera Software" / "Opera GX Stable"
    gx_l = local / "Opera Software" / "Opera GX Stable"
    t = {"local": local, "roaming": roaming}
    t["chrome_default"] = _chromium_profile(chrome / "Default")
    t["chrome_p1"] = _chromium_profile(chrome / "Profile 1")
    t["chrome_guest"] = _chromium_profile(chrome / "Guest Profile")
    # Not a profile (no Preferences) - e.g. a helper folder that happens to
    # hold a "Cache" sub-folder: left alone.
    t["chrome_not_profile"] = _chromium_profile(chrome / "Crashpad", prefs=False)
    t["edge_default"] = _chromium_profile(local / "Microsoft" / "Edge" / "User Data" / "Default")
    t["brave_p2"] = _chromium_profile(local / "BraveSoftware" / "Brave-Browser" / "User Data" / "Profile 2")
    t["vivaldi_default"] = _chromium_profile(local / "Vivaldi" / "User Data" / "Default")
    # Opera: everything but the HTTP Cache lives in the roaming profile.
    roaming_dirs = CHROMIUM_CACHE_DIRS[1:]
    t["opera_main"] = _chromium_profile(opera_r, cache_dirs=roaming_dirs)
    t["opera_main_local"] = _cache_only(opera_l / "Cache")
    t["opera_side"] = _chromium_profile(opera_r / "_side_profiles" / "1234", cache_dirs=roaming_dirs)
    t["opera_side_local"] = _cache_only(opera_l / "_side_profiles" / "1234" / "Cache")
    t["gx_main"] = _chromium_profile(gx_r, cache_dirs=roaming_dirs)
    t["gx_main_local"] = _cache_only(gx_l / "Cache")
    fx_profile = local / "Mozilla" / "Firefox" / "Profiles" / "ab12.default-release"
    t["firefox"] = _cache_only(fx_profile / "cache2")
    keep = fx_profile / "cookies.sqlite"
    keep.write_text("keep", encoding="utf-8")
    t["firefox"]["keepers"].append(keep)
    return t


def _run_browser_ps(tmp_path: Path, script: str, local, roaming, running=()):
    """Runs a catalog command with LOCALAPPDATA/APPDATA redirected inside the
    script (never in the child environment), Get-Process stubbed to report
    `running` and Stop-Process stubbed to a logger - a cache sweep must never
    close a browser. Remove-Item is the Windows PowerShell 5.1 stand-in that
    follows links, so nothing passes by relying on pwsh 7's safer one."""
    log = tmp_path / "stop.log"
    log.write_text("", encoding="utf-8")
    procs = ", ".join(f"[PSCustomObject]@{{ ProcessName = {_ps_quote(n)}; Id = {100 + i} }}" for i, n in enumerate(running))
    prelude = [
        "[Console]::OutputEncoding=[Text.Encoding]::UTF8",
        f"function Get-Process {{ [CmdletBinding()] param([string[]]$Name) @({procs}) }}",
        f"function Stop-Process {{ Add-Content -LiteralPath {_ps_quote(str(log))} -Value ('Stop-Process ' + ($args -join ' ')) }}",
        "foreach ($n in @('Get-Process', 'Stop-Process')) { if ((Get-Command $n -EA SilentlyContinue | Select-Object -First 1).CommandType -ne 'Function') "
        f"{{ exit {STUB_GUARD_EXIT} }} }}",
        PS51_REMOVE_ITEM,
        f"$env:LOCALAPPDATA = {_ps_quote(str(local) if local else '')}",
        f"$env:APPDATA = {_ps_quote(str(roaming) if roaming else '')}",
    ]
    cwd = tmp_path / "cwd"
    cwd.mkdir(exist_ok=True)
    result = _subprocess.run(
        [_powershell_or_skip(), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command",
         "; ".join(prelude) + "; " + script],
        cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180,
        creationflags=getattr(_subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert result.returncode != STUB_GUARD_EXIT, "a system tool was not stubbed - refusing to run the real one"
    return result, log.read_text(encoding="utf-8-sig").splitlines()


def test_browser_cache_sweep_command_and_preview_parse_without_powershell_7_only_syntax(tmp_path):
    action = _sweep_action()
    scripts_file = tmp_path / "scripts.json"
    scripts_file.write_text(_json.dumps({"command": action.command, "preview": action.preview_command}), encoding="utf-8")
    result = _subprocess.run(
        [_powershell_or_skip(), "-NoProfile", "-NonInteractive", "-Command", PARSE_CHECKER],
        env=dict(_os.environ, PFSCRIPTS_FILE=str(scripts_file)),
        capture_output=True, text=True, timeout=120,
        creationflags=getattr(_subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert "PARSE_DONE" in result.stdout, result.stdout + result.stderr
    assert [line for line in result.stdout.splitlines() if line.startswith("ERR")] == []


def test_browser_cache_sweep_never_hard_codes_default_and_never_closes_a_browser():
    action = _sweep_action()
    for script in (action.command, action.preview_command):
        assert "User Data\\Default" not in script
        assert "Stop-Process" not in script and "taskkill" not in script.lower()
        for browser in ("Chrome", "Edge", "Brave", "Vivaldi", "Opera", "Opera GX"):
            assert f"N='{browser}'" in script, browser
        for proc in ("chrome", "msedge", "brave", "vivaldi", "opera", "firefox"):
            assert f"'{proc}'" in script, proc
    # Only cache folders - never a data file or folder a user would miss.
    rels = re.search(r"\$cacheRels = @\(([^)]*)\)", action.command).group(1)
    assert rels == "'Cache', 'Code Cache', 'GPUCache', 'Service Worker\\CacheStorage', 'Service Worker\\ScriptCache'"
    for data in ("Cookies", "Login Data", "History", "Bookmarks", "Extensions", "Local Storage", "Web Data"):
        assert f"'{data}'" not in action.command, data


def test_browser_cache_sweep_preview_uses_the_same_enumeration_as_the_command():
    # The dry run must list exactly what the real run would clean: the
    # enumeration (profile walk, link checks, browser table, running check,
    # target list) is one shared text in both.
    action = _sweep_action()
    shared = action.command[action.command.index("function Test-PfRealDir") : action.command.index("$swept = 0;")]
    assert shared in action.preview_command
    size_fn = re.search(r"function Get-PfSize.*?return @\(\$s, \$f\) \}", action.command).group(0)
    assert action.preview_command.startswith(size_fn + "; ")


def test_browser_cache_sweep_cleans_every_profile_of_every_browser_and_reports_freed_mb(tmp_path):
    t = _browser_tree(tmp_path)
    result, stops = _run_browser_ps(tmp_path, _sweep_action().command, t["local"], t["roaming"])
    out = result.stdout
    assert result.returncode == 0, out + result.stderr
    for key in ("chrome_default", "chrome_p1", "chrome_guest", "edge_default", "brave_p2", "vivaldi_default",
                "opera_main", "opera_main_local", "opera_side", "opera_side_local", "gx_main", "gx_main_local", "firefox"):
        _assert_swept(t[key])
    _assert_untouched(t["chrome_not_profile"])
    for line in (
        "Chrome / Default: 5 cache folder(s), freed 5 MB",
        "Chrome / Profile 1: 5 cache folder(s), freed 5 MB",
        "Chrome / Guest Profile: 5 cache folder(s), freed 5 MB",
        "Edge / Default: 5 cache folder(s), freed 5 MB",
        "Brave / Profile 2: 5 cache folder(s), freed 5 MB",
        "Vivaldi / Default: 5 cache folder(s), freed 5 MB",
        "Opera / (main): 5 cache folder(s), freed 5 MB",
        "Opera / _side_profiles\\1234: 5 cache folder(s), freed 5 MB",
        "Opera GX / (main): 5 cache folder(s), freed 5 MB",
        "Firefox / ab12.default-release: 1 cache folder(s), freed 1 MB",
    ):
        assert line in out.splitlines(), line + "\n" + out
    assert "Swept 46 cache folder(s), skipped/locked: 0, freed 46 MB" in out, out
    assert "Skipped," not in out
    assert stops == []


def test_browser_cache_sweep_skips_and_names_a_running_browser_without_closing_it(tmp_path):
    t = _browser_tree(tmp_path)
    result, stops = _run_browser_ps(
        tmp_path, _sweep_action().command, t["local"], t["roaming"], running=("chrome", "opera", "firefox", "explorer")
    )
    out = result.stdout
    assert result.returncode == 0, out + result.stderr
    for key in ("chrome_default", "chrome_p1", "chrome_guest", "opera_main", "opera_main_local", "opera_side",
                "gx_main", "gx_main_local", "firefox"):
        _assert_untouched(t[key])
    for key in ("edge_default", "brave_p2", "vivaldi_default"):
        _assert_swept(t[key])
    assert (
        "Skipped, browser running (close it completely, including background apps in the tray, and run again): "
        "Chrome, Opera, Opera GX, Firefox"
    ) in out, out
    assert "Swept 15 cache folder(s), skipped/locked: 0, freed 15 MB" in out, out
    assert stops == []  # never closed


def test_browser_cache_sweep_a_running_but_absent_browser_is_not_reported(tmp_path):
    local = tmp_path / "Local"
    info = _chromium_profile(local / "Microsoft" / "Edge" / "User Data" / "Default")
    result, _ = _run_browser_ps(tmp_path, _sweep_action().command, local, tmp_path / "Roaming", running=("chrome",))
    assert result.returncode == 0, result.stdout + result.stderr
    _assert_swept(info)
    assert "browser running" not in result.stdout


def test_browser_cache_sweep_with_nothing_installed_and_no_appdata_exits_zero(tmp_path):
    action = _sweep_action()
    result, _ = _run_browser_ps(tmp_path, action.command, tmp_path / "Local", None)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip().splitlines() == ["Swept 0 cache folder(s), skipped/locked: 0, freed 0 MB"]
    preview, _ = _run_browser_ps(tmp_path, action.preview_command, tmp_path / "Local", None)
    assert preview.returncode == 0, preview.stdout + preview.stderr
    assert preview.stdout.strip().splitlines() == [
        "Would delete 0 files, 0 MB from browser caches (Chrome/Edge/Brave/Vivaldi/Opera/Firefox, all profiles)"
    ]


def test_browser_cache_sweep_a_default_folder_without_preferences_is_still_swept(tmp_path):
    # A Default whose Preferences was lost (crash, manual cleanup) is still
    # the primary profile - its caches go like any other.
    local = tmp_path / "Local"
    info = _chromium_profile(local / "Google" / "Chrome" / "User Data" / "Default", prefs=False)
    result, _ = _run_browser_ps(tmp_path, _sweep_action().command, local, None)
    assert result.returncode == 0, result.stdout + result.stderr
    _assert_swept(info)
    assert "Chrome / Default: 5 cache folder(s), freed 5 MB" in result.stdout


@_pytest.mark.parametrize("where", ["user_data", "profile", "service_worker", "cache", "side_profiles", "firefox_profiles"])
def test_browser_cache_sweep_never_follows_a_link_on_the_way_to_a_cache(tmp_path, where):
    # A standard user can plant a link anywhere under their own AppData; an
    # elevated sweep that followed it would empty "Cache" folders wherever it
    # points. Every level (browser root, profile, Service Worker, the cache
    # folder itself, Opera's _side_profiles, Firefox's Profiles) is checked
    # for a reparse point first; a link is left in place, never entered.
    victim = _victim(tmp_path)
    # Shaped like a browser tree, so following the link would find caches.
    for sub in ("Default/Preferences", "Default/Cache/x.bin", "Cache/x.bin", "CacheStorage/x.bin", "1/Preferences",
                "1/Code Cache/x.bin", "p/cache2/x.bin", "Preferences"):
        f = victim / sub
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("precious", encoding="utf-8")
    local = tmp_path / "Local"
    roaming = tmp_path / "Roaming"
    chrome = local / "Google" / "Chrome" / "User Data"
    default = chrome / "Default"
    if where == "user_data":
        chrome.parent.mkdir(parents=True)
        link = chrome
    elif where == "profile":
        chrome.mkdir(parents=True)
        link = chrome / "Profile 3"
    elif where in ("service_worker", "cache"):
        default.mkdir(parents=True)
        (default / "Preferences").write_text("{}", encoding="utf-8")
        link = default / ("Service Worker" if where == "service_worker" else "Cache")
    elif where == "side_profiles":
        opera = roaming / "Opera Software" / "Opera Stable"
        opera.mkdir(parents=True)
        (opera / "Preferences").write_text("{}", encoding="utf-8")
        link = opera / "_side_profiles"
    else:
        (local / "Mozilla" / "Firefox").mkdir(parents=True)
        link = local / "Mozilla" / "Firefox" / "Profiles"
    _link(link, victim, "symlink")
    before = sorted(str(p.relative_to(victim)) for p in victim.rglob("*"))
    action = _sweep_action()
    preview, _ = _run_browser_ps(tmp_path, action.preview_command, local, roaming)
    result, _ = _run_browser_ps(tmp_path, action.command, local, roaming)
    assert result.returncode == 0, result.stdout + result.stderr
    assert sorted(str(p.relative_to(victim)) for p in victim.rglob("*")) == before
    _assert_victim_intact(victim)
    assert _os.path.islink(link)  # left in place, not removed
    assert "Swept 0 cache folder(s), skipped/locked: 0, freed 0 MB" in result.stdout, result.stdout
    assert "Would delete 0 files, 0 MB" in preview.stdout, preview.stdout
    if where in ("profile", "side_profiles"):
        # Profiles are only enumerated among real folders - a link is simply
        # not a profile, nothing below it is looked at.
        assert "link not followed" not in result.stdout
    else:
        assert "Skipped, junction/symbolic link not followed: " in result.stdout, result.stdout
        assert link.name in result.stdout
        assert "Would skip, junction/symbolic link not followed: " in preview.stdout, preview.stdout


def test_browser_cache_sweep_preview_lists_what_would_go_and_deletes_nothing(tmp_path):
    t = _browser_tree(tmp_path)
    result, stops = _run_browser_ps(tmp_path, _sweep_action().preview_command, t["local"], t["roaming"], running=("msedge",))
    out = result.stdout
    assert result.returncode == 0, out + result.stderr
    for key in ("chrome_default", "chrome_p1", "edge_default", "opera_side", "opera_side_local", "firefox"):
        _assert_untouched(t[key])
    assert "Would clean Chrome / Profile 1: 5 files, 5 MB in 5 cache folder(s)" in out, out
    assert "Would clean Opera / _side_profiles\\1234: 5 files, 5 MB in 5 cache folder(s)" in out, out
    assert "Would clean Firefox / ab12.default-release: 1 files, 1 MB in 1 cache folder(s)" in out, out
    assert "Would clean Edge" not in out
    assert "Would skip, browser running: Edge" in out, out
    assert "Would delete 41 files, 41 MB from browser caches" in out, out
    assert stops == []


def test_browser_cache_sweep_label_and_description_name_all_browsers_profiles_and_the_running_skip():
    action = _sweep_action()
    for browser in ("Brave", "Vivaldi", "Opera"):
        assert browser in action.label_en and browser in action.label_sk
        assert browser in action.description_en and browser in action.description_sk
    assert "profil" in action.description_sk and "profile" in action.description_en
    assert "beží" in action.description_sk and "running" in action.description_en
