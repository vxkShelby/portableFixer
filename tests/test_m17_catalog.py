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


def test_m17_clear_policy_keys_refuses_on_domain_joined_or_mdm_enrolled_machine():
    module = load_module(CATALOG_PATH)
    by_id = {a.id: a for a in module.actions}
    command = by_id["browser_clear_policy_keys"].command
    assert "dsregcmd" in command
    assert "PartOfDomain" in command
    assert "exit 1" in command
