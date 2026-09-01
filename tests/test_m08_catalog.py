from pathlib import Path

from portablefix.models import ModuleCategory, RiskLevel
from portablefix.module_engine import load_module

CATALOG_PATH = Path(__file__).resolve().parent.parent / "Modules" / "m08_security" / "actions.yaml"


def test_m08_catalog_loads_5_actions_in_security_category():
    module = load_module(CATALOG_PATH)
    assert module.module_id == "m08_security"
    assert module.category == ModuleCategory.SECURITY
    assert len(module.actions) == 5


def test_m08_catalog_risk_distribution():
    module = load_module(CATALOG_PATH)
    by_risk = {}
    for action in module.actions:
        by_risk.setdefault(action.risk, []).append(action.id)
    assert len(by_risk[RiskLevel.SAFE]) == 3
    assert set(by_risk[RiskLevel.MODERATE]) == {"sec_defender_quickscan", "sec_defender_update"}
    assert RiskLevel.DESTRUCTIVE not in by_risk
    assert RiskLevel.REQUIRES_REBOOT not in by_risk


def test_m08_catalog_no_action_has_undo_command():
    module = load_module(CATALOG_PATH)
    for action in module.actions:
        assert action.undo_command is None


def test_m08_catalog_covers_expected_audit_surfaces():
    module = load_module(CATALOG_PATH)
    ids = {a.id for a in module.actions}
    assert ids == {
        "sec_defender_status",
        "sec_firewall_status",
        "sec_defender_quickscan",
        "sec_defender_update",
        "sec_uac_status",
    }
