from pathlib import Path

from portablefix.models import ModuleCategory, RiskLevel
from portablefix.module_engine import load_module

CATALOG_PATH = Path(__file__).resolve().parent.parent / "Modules" / "m23_antivirus" / "actions.yaml"


def test_m23_catalog_loads_5_actions_in_antivirus_category():
    module = load_module(CATALOG_PATH)
    assert module.module_id == "m23_antivirus"
    assert module.category == ModuleCategory.ANTIVIRUS
    assert len(module.actions) == 5


def test_m23_catalog_covers_expected_ids():
    module = load_module(CATALOG_PATH)
    ids = {a.id for a in module.actions}
    assert ids == {
        "sec_defender_status",
        "sec_defender_update",
        "sec_defender_quickscan",
        "sec_defender_exclusions_list",
        "hard_defender_clear_exclusions",
    }


def test_m23_catalog_only_clear_exclusions_has_undo_command():
    module = load_module(CATALOG_PATH)
    by_id = {a.id: a for a in module.actions}
    assert by_id["hard_defender_clear_exclusions"].undo_command is not None
    for not_undoable in ("sec_defender_status", "sec_defender_update", "sec_defender_quickscan", "sec_defender_exclusions_list"):
        assert by_id[not_undoable].undo_command is None, not_undoable


def test_m23_catalog_risk_distribution():
    module = load_module(CATALOG_PATH)
    by_risk = {}
    for action in module.actions:
        by_risk.setdefault(action.risk, []).append(action.id)
    assert set(by_risk[RiskLevel.SAFE]) == {"sec_defender_status", "sec_defender_exclusions_list"}
    assert set(by_risk[RiskLevel.MODERATE]) == {"sec_defender_update", "sec_defender_quickscan", "hard_defender_clear_exclusions"}
