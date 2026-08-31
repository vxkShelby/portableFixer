# tests/test_m02_catalog.py
from pathlib import Path

from portablefix.models import ModuleCategory, RiskLevel
from portablefix.module_engine import load_module

CATALOG_PATH = Path(__file__).resolve().parent.parent / "Modules" / "m02_cleanup" / "actions.yaml"


def test_m02_catalog_loads_16_actions_all_with_preview():
    module = load_module(CATALOG_PATH)
    assert module.module_id == "m02_cleanup"
    assert len(module.actions) == 16
    assert all(a.preview_command for a in module.actions)


def test_m02_catalog_risk_distribution():
    module = load_module(CATALOG_PATH)
    by_risk = {}
    for action in module.actions:
        by_risk.setdefault(action.risk, []).append(action.id)
    assert len(by_risk[RiskLevel.SAFE]) == 7
    assert len(by_risk[RiskLevel.MODERATE]) == 5
    assert len(by_risk[RiskLevel.DESTRUCTIVE]) == 4


def test_m02_catalog_no_wmic():
    module = load_module(CATALOG_PATH)
    for action in module.actions:
        assert "wmic" not in action.command.lower()
        assert "wmic" not in (action.preview_command or "").lower()


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
