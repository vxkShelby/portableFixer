from pathlib import Path

from portablefix.models import ModuleCategory, RiskLevel
from portablefix.module_engine import load_module

CATALOG_PATH = Path(__file__).resolve().parent.parent / "Modules" / "m20_software_updates" / "actions.yaml"


def test_m20_catalog_loads_3_actions_in_repair_category():
    module = load_module(CATALOG_PATH)
    assert module.module_id == "m20_software_updates"
    assert module.category == ModuleCategory.REPAIR
    assert len(module.actions) == 3


def test_m20_catalog_risk_distribution():
    module = load_module(CATALOG_PATH)
    by_risk = {}
    for action in module.actions:
        by_risk.setdefault(action.risk, []).append(action.id)
    assert set(by_risk[RiskLevel.SAFE]) == {"winget_list_installed", "winget_list_outdated"}
    assert set(by_risk[RiskLevel.MODERATE]) == {"winget_update_all"}
    assert RiskLevel.DESTRUCTIVE not in by_risk
    assert RiskLevel.REQUIRES_REBOOT not in by_risk


def test_m20_catalog_no_action_has_undo():
    # winget upgrade has no generic downgrade path - none of these are undoable.
    module = load_module(CATALOG_PATH)
    for action in module.actions:
        assert action.undo_command is None, action.id


def test_m20_catalog_every_action_guards_missing_winget():
    module = load_module(CATALOG_PATH)
    for action in module.actions:
        assert "Get-Command winget" in action.command, action.id


def test_m20_catalog_covers_expected_ids():
    module = load_module(CATALOG_PATH)
    ids = {a.id for a in module.actions}
    assert ids == {"winget_list_installed", "winget_list_outdated", "winget_update_all"}


def test_m20_catalog_every_action_has_both_language_labels_and_descriptions():
    module = load_module(CATALOG_PATH)
    for action in module.actions:
        assert action.label_sk
        assert action.label_en
        assert action.description_sk
        assert action.description_en
