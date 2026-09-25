from pathlib import Path

from portablefix.models import ModuleCategory, RiskLevel
from portablefix.module_engine import load_module

CATALOG_PATH = Path(__file__).resolve().parent.parent / "Modules" / "m17_browser_deep" / "actions.yaml"


def test_m17_catalog_loads_6_actions_in_repair_category():
    module = load_module(CATALOG_PATH)
    assert module.module_id == "m17_browser_deep"
    assert module.category == ModuleCategory.REPAIR
    assert len(module.actions) == 6


def test_m17_catalog_risk_distribution():
    module = load_module(CATALOG_PATH)
    by_risk = {}
    for action in module.actions:
        by_risk.setdefault(action.risk, []).append(action.id)
    assert set(by_risk[RiskLevel.SAFE]) == {
        "browser_extensions_report",
        "browser_policy_report",
        "browser_homepage_search_report",
    }
    assert set(by_risk[RiskLevel.MODERATE]) == {
        "browser_reset_chrome_profile",
        "browser_reset_edge_profile",
    }
    assert set(by_risk[RiskLevel.DESTRUCTIVE]) == {"browser_clear_policy_keys"}
    assert RiskLevel.REQUIRES_REBOOT not in by_risk


def test_m17_catalog_only_policy_clear_has_undo():
    module = load_module(CATALOG_PATH)
    by_id = {a.id: a for a in module.actions}
    assert by_id["browser_clear_policy_keys"].undo_command is not None
    for not_undoable in (
        "browser_extensions_report",
        "browser_policy_report",
        "browser_homepage_search_report",
        "browser_reset_chrome_profile",
        "browser_reset_edge_profile",
    ):
        assert by_id[not_undoable].undo_command is None, not_undoable


def test_m17_catalog_covers_expected_ids():
    module = load_module(CATALOG_PATH)
    ids = {a.id for a in module.actions}
    assert ids == {
        "browser_extensions_report",
        "browser_policy_report",
        "browser_homepage_search_report",
        "browser_reset_chrome_profile",
        "browser_reset_edge_profile",
        "browser_clear_policy_keys",
    }


def test_m17_catalog_reports_guard_against_suppressed_error_exit_code_1():
    # Get-ChildItem/-Content on a missing path with -EA SilentlyContinue still
    # leaves $? = $false; as the final statement that flips the process exit
    # code to 1 even though nothing actually failed. Each report guards this.
    module = load_module(CATALOG_PATH)
    by_id = {a.id: a for a in module.actions}
    assert "if (Test-Path $ffDir)" in by_id["browser_extensions_report"].command
    assert "'--- End ---'" in by_id["browser_policy_report"].command
    assert "'--- End ---'" in by_id["browser_homepage_search_report"].command


def test_m17_catalog_every_action_has_both_language_labels_and_descriptions():
    module = load_module(CATALOG_PATH)
    for action in module.actions:
        assert action.label_sk
        assert action.label_en
        assert action.description_sk
        assert action.description_en


def test_m17_catalog_profile_resets_fail_the_action_if_rename_fails():
    # Same discipline as the Outlook profile reset: Rename-Item's default
    # ErrorActionPreference is Continue, so without -EA Stop + a catch a
    # failed rename (browser still running, file locked) would still print
    # the success message and exit 0.
    module = load_module(CATALOG_PATH)
    by_id = {a.id: a for a in module.actions}
    for action_id in ("browser_reset_chrome_profile", "browser_reset_edge_profile"):
        command = by_id[action_id].command
        assert "-EA Stop" in command, action_id
        assert "catch" in command, action_id
        assert "exit 1" in command, action_id


def test_m17_clear_policy_keys_verifies_the_keys_are_actually_gone():
    # Remove-Item on an HKLM key with -EA SilentlyContinue silently no-ops
    # without administrator - the command must Test-Path both keys
    # afterward and only claim success if they're actually gone.
    module = load_module(CATALOG_PATH)
    action = next(a for a in module.actions if a.id == "browser_clear_policy_keys")
    assert "chromeGone" in action.command
    assert "edgeGone" in action.command
    assert action.command.count("exit 1") == 2


def test_m17_clear_policy_keys_refuses_on_domain_joined_or_mdm_enrolled_machine():
    module = load_module(CATALOG_PATH)
    by_id = {a.id: a for a in module.actions}
    command = by_id["browser_clear_policy_keys"].command
    assert "dsregcmd" in command
    assert "PartOfDomain" in command
    assert "exit 1" in command


# --- G23: every profile, every Chromium browser; resets never kill ----------
#
# The extensions and homepage/search reports used to read only
# "User Data\Default" of Chrome and Edge. They now share the m02
# browser_cache_sweep enumeration (Chrome, Edge, Brave, Vivaldi, Opera,
# Opera GX; every profile folder with a Preferences file, Opera's main
# profile and _side_profiles) - a hijacker that lands in "Profile 2" or in
# Brave is no longer invisible. The profile resets used to Stop-Process the
# browser without asking; they now refuse (exit 1, nothing changed) while it
# runs - see the action descriptions for why.

import json  # noqa: E402
import os  # noqa: E402
import re  # noqa: E402
import subprocess  # noqa: E402

import pytest  # noqa: E402

from test_m02_catalog import _chromium_profile, _run_browser_ps  # noqa: E402
from test_safe_delete import PARSE_CHECKER, _powershell_or_skip  # noqa: E402

M02_PATH = CATALOG_PATH.parent.parent / "m02_cleanup" / "actions.yaml"


def _m17(action_id):
    return next(a for a in load_module(CATALOG_PATH).actions if a.id == action_id)


def _prefs(profile: Path, data: dict, name: str = "Preferences") -> None:
    profile.mkdir(parents=True, exist_ok=True)
    (profile / name).write_text(json.dumps(data), encoding="utf-8")


def _ext(name: str, state: int) -> dict:
    return {"manifest": {"name": name}, "state": state}


def test_m17_commands_parse_without_powershell_7_only_syntax(tmp_path):
    scripts = {a.id: a.command for a in load_module(CATALOG_PATH).actions}
    scripts_file = tmp_path / "scripts.json"
    scripts_file.write_text(json.dumps(scripts), encoding="utf-8")
    result = subprocess.run(
        [_powershell_or_skip(), "-NoProfile", "-NonInteractive", "-Command", PARSE_CHECKER],
        env=dict(os.environ, PFSCRIPTS_FILE=str(scripts_file)),
        capture_output=True, text=True, timeout=120,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert "PARSE_DONE" in result.stdout, result.stdout + result.stderr
    assert [line for line in result.stdout.splitlines() if line.startswith("ERR")] == []


def test_m17_reports_share_the_cache_sweep_profile_enumeration():
    # One enumeration for the whole app: the reports cannot drift from the
    # sweep (a browser or profile layout added to one but not the other).
    sweep = next(a for a in load_module(M02_PATH).actions if a.id == "browser_cache_sweep").command
    shared = sweep[sweep.index("function Test-PfRealDir") : sweep.index("$cacheRels = ")]
    assert "function Get-PfProfiles" in shared and "$browsers = @(" in shared
    for action_id in ("browser_extensions_report", "browser_homepage_search_report"):
        command = _m17(action_id).command
        assert command.startswith(shared), action_id
        assert "User Data\\Default" not in command, action_id
        # Windows PowerShell 5.1 reads a BOM-less file as ANSI - non-ASCII
        # extension names and homepages would come out garbled.
        assert "-Raw -EA Stop" not in command, action_id
        assert "-Raw -Encoding UTF8 -EA Stop" in command, action_id


def test_m17_no_action_closes_a_browser():
    for action in load_module(CATALOG_PATH).actions:
        assert "Stop-Process" not in action.command, action.id
        assert "taskkill" not in action.command.lower(), action.id


def test_m17_extensions_report_lists_every_profile_of_every_browser(tmp_path):
    local = tmp_path / "Local"
    roaming = tmp_path / "Roaming"
    chrome = local / "Google" / "Chrome" / "User Data"
    # Secure Preferences and Preferences each hold part of the list; an
    # entry without a manifest in one file is filled from the other.
    _prefs(chrome / "Default", {"extensions": {"settings": {"aaa": _ext("Good Ext", 1), "bbb": {"state": 1}}}},
           "Secure Preferences")
    _prefs(chrome / "Default", {"extensions": {"settings": {"bbb": _ext("Evil Toolbar", 0)}}})
    # Newer Chromium drops "state": no disable_reasons means enabled.
    _prefs(chrome / "Profile 2", {"extensions": {"settings": {
        "ccc": {"manifest": {"name": "Coupon Helper"}},
        "ddd": {"manifest": {"name": "Blocked Addon"}, "disable_reasons": [1]},
        "ggg": {"state": 1},
    }}})
    (chrome / "Profile 3").mkdir(parents=True)
    (chrome / "Profile 3" / "Preferences").write_text("{not json", encoding="utf-8")
    (chrome / "Crashpad").mkdir()  # no Preferences - not a profile
    _prefs(local / "BraveSoftware" / "Brave-Browser" / "User Data" / "Default",
           {"extensions": {"settings": {"ddd": _ext("Brave Addon", 1)}}})
    opera = roaming / "Opera Software" / "Opera Stable"
    _prefs(opera, {"extensions": {"settings": {"eee": _ext("Opera Main Ext", 1)}}})
    _prefs(opera / "_side_profiles" / "77", {"extensions": {"settings": {"fff": _ext("Opera Side Ext", 1)}}})
    for fx, addon, active in (("a1.default-release", "uBlock", True), ("b2.work", "Shady VPN", False)):
        p = roaming / "Mozilla" / "Firefox" / "Profiles" / fx
        p.mkdir(parents=True)
        (p / "extensions.json").write_text(json.dumps({"addons": [{"defaultLocale": {"name": addon}, "active": active}]}),
                                           encoding="utf-8")
    result, _ = _run_browser_ps(tmp_path, _m17("browser_extensions_report").command, local, roaming)
    out = result.stdout
    assert result.returncode == 0, out + result.stderr
    for header in (
        "--- Chrome / Default (2 extensions) ---",
        "--- Chrome / Profile 2 (3 extensions) ---",
        "--- Brave / Default (1 extensions) ---",
        "--- Opera / (main) (1 extensions) ---",
        "--- Opera / _side_profiles\\77 (1 extensions) ---",
        "--- Firefox / a1.default-release (1 extensions) ---",
        "--- Firefox / b2.work (1 extensions) ---",
    ):
        assert header in out, header + "\n" + out
    for name in ("Good Ext", "Evil Toolbar", "Coupon Helper", "Brave Addon", "Opera Main Ext", "Opera Side Ext",
                 "uBlock", "Shady VPN"):
        assert name in out, name
    lines = out.splitlines()
    for line in ("  enabled   Good Ext", "  disabled  Evil Toolbar", "  enabled   Coupon Helper", "  disabled  Blocked Addon",
                 "  enabled   (ggg)", "  enabled   Opera Side Ext", "  enabled   uBlock", "  disabled  Shady VPN"):
        assert line in lines, line + "\n" + out
    assert "Chrome / Profile 3: could not parse preferences file." in out
    assert "Crashpad" not in out
    assert out.rstrip().endswith("--- End ---")


def test_m17_extensions_report_with_no_browsers_and_no_appdata_exits_zero(tmp_path):
    result, _ = _run_browser_ps(tmp_path, _m17("browser_extensions_report").command, tmp_path / "Local", None)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "--- End ---"


def test_m17_homepage_report_shows_every_profile_of_every_browser(tmp_path):
    local = tmp_path / "Local"
    roaming = tmp_path / "Roaming"
    _prefs(local / "Google" / "Chrome" / "User Data" / "Default",
           {"homepage": "https://www.google.com", "default_search_provider": {"name": "Google"}})
    _prefs(local / "Google" / "Chrome" / "User Data" / "Profile 1", {
        "homepage": "http://search.hijacker.example",
        "default_search_provider": {"name": "HijackSearch"},
        "default_search_provider_data": {"template_url_data": {"url": "http://search.hijacker.example/?q={searchTerms}"}},
        "session": {"restore_on_startup": 4, "startup_urls": ["http://a.example", "http://b.example"]},
    })
    _prefs(local / "Microsoft" / "Edge" / "User Data" / "Profile 4", {"homepage": "https://www.msn.com"})
    _prefs(local / "Vivaldi" / "User Data" / "Default", {"homepage": "https://vivaldi.com"})
    _prefs(roaming / "Opera Software" / "Opera GX Stable", {"homepage": "https://gx.example"})
    bad = local / "BraveSoftware" / "Brave-Browser" / "User Data" / "Default"
    bad.mkdir(parents=True)
    (bad / "Preferences").write_text("{broken", encoding="utf-8")
    result, _ = _run_browser_ps(tmp_path, _m17("browser_homepage_search_report").command, local, roaming)
    out = result.stdout
    assert result.returncode == 0, out + result.stderr
    blocks = re.split(r"(?m)^--- ", out)
    by_name = {b.split(" ---", 1)[0]: b for b in blocks if " ---" in b}
    assert "Homepage: https://www.google.com" in by_name["Chrome / Default"]
    hijacked = by_name["Chrome / Profile 1"]
    assert "Homepage: http://search.hijacker.example" in hijacked
    assert "Default search: HijackSearch" in hijacked
    assert "Search URL: http://search.hijacker.example/?q={searchTerms}" in hijacked
    assert "Startup URLs: http://a.example, http://b.example" in hijacked
    assert "Homepage: https://www.msn.com" in by_name["Edge / Profile 4"]
    assert "Homepage: https://vivaldi.com" in by_name["Vivaldi / Default"]
    assert "Homepage: https://gx.example" in by_name["Opera GX / (main)"]
    assert "Brave / Default: could not parse preferences file." in out
    assert out.rstrip().endswith("--- End ---")


def _reset_tree(local: Path, rel: str) -> Path:
    root = local.joinpath(*rel.split("/"))
    _chromium_profile(root / "Default")
    _prefs(root / "Profile 1", {})
    _prefs(root / "Default.bak-20250101_000000", {})
    return root


RESETS = [
    ("browser_reset_chrome_profile", "chrome", "Chrome", "Google/Chrome/User Data"),
    ("browser_reset_edge_profile", "msedge", "Edge", "Microsoft/Edge/User Data"),
]


@pytest.mark.parametrize("action_id, proc, label, rel", RESETS)
def test_m17_profile_reset_refuses_while_the_browser_runs_and_changes_nothing(tmp_path, action_id, proc, label, rel):
    # Killing the browser discards the client's open tabs and unsaved form
    # input and can cut a Preferences write in half; the reset's job is the
    # profile, not closing the browser - the technician closes it first.
    local = tmp_path / "Local"
    root = _reset_tree(local, rel)
    before = sorted(p.name for p in root.iterdir())
    result, stops = _run_browser_ps(tmp_path, _m17(action_id).command, local, None, running=(proc,))
    assert result.returncode == 1, result.stdout + result.stderr
    assert f"{label} is running - refusing to reset the profile" in result.stdout
    assert "Nothing was changed." in result.stdout
    assert sorted(p.name for p in root.iterdir()) == before
    assert (root / "Default" / "Preferences").exists()
    assert stops == []


@pytest.mark.parametrize("action_id, proc, label, rel", RESETS)
def test_m17_profile_reset_renames_default_and_names_the_profiles_it_left(tmp_path, action_id, proc, label, rel):
    local = tmp_path / "Local"
    root = _reset_tree(local, rel)
    other = "msedge" if proc == "chrome" else "chrome"
    result, stops = _run_browser_ps(tmp_path, _m17(action_id).command, local, None, running=(other,))
    assert result.returncode == 0, result.stdout + result.stderr
    assert not (root / "Default").exists()
    backups = sorted(p.name for p in root.iterdir() if p.name.startswith("Default.bak-") and p.name != "Default.bak-20250101_000000")
    assert len(backups) == 1 and (root / backups[0] / "Cookies").exists()
    assert f"{label} profile reset. Old profile saved as " in result.stdout
    # Other profiles are not reset - they are named so the technician knows.
    assert "Other profiles left unchanged: Profile 1" in result.stdout, result.stdout
    assert (root / "Profile 1" / "Preferences").exists()
    assert stops == []


@pytest.mark.parametrize("action_id, proc, label, rel", RESETS)
def test_m17_profile_reset_without_a_default_profile_says_so(tmp_path, action_id, proc, label, rel):
    result, _ = _run_browser_ps(tmp_path, _m17(action_id).command, tmp_path / "Local", None)
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"{label} default profile not found." in result.stdout


def test_m17_profile_reset_descriptions_state_the_refusal_instead_of_closing():
    for action_id in ("browser_reset_chrome_profile", "browser_reset_edge_profile"):
        action = _m17(action_id)
        assert "bez varovania" not in action.description_sk and "without asking" not in action.description_en
        assert "odmietne" in action.description_sk and "refuses" in action.description_en
        assert "Default" in action.description_sk and "Default" in action.description_en


def test_m17_report_descriptions_name_all_profiles_and_browsers():
    for action_id in ("browser_extensions_report", "browser_homepage_search_report"):
        action = _m17(action_id)
        assert "len profil Default" not in action.description_sk
        assert "Default profile only" not in action.description_en
        for browser in ("Brave", "Vivaldi", "Opera"):
            assert browser in action.description_sk and browser in action.description_en, (action_id, browser)
        assert "všetk" in action.description_sk and "every profile" in action.description_en
