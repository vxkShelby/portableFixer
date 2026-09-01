from pathlib import Path

from portablefix.models import ModuleCategory, RiskLevel
from portablefix.module_engine import load_module

CATALOG_PATH = Path(__file__).resolve().parent.parent / "Modules" / "m03_disk" / "actions.yaml"


def test_m03_catalog_loads_8_actions_in_repair_category():
    module = load_module(CATALOG_PATH)
    assert module.module_id == "m03_disk"
    assert module.category == ModuleCategory.REPAIR
    assert len(module.actions) == 8


def test_m03_catalog_risk_distribution():
    module = load_module(CATALOG_PATH)
    by_risk = {}
    for action in module.actions:
        by_risk.setdefault(action.risk, []).append(action.id)
    assert len(by_risk[RiskLevel.SAFE]) == 5
    assert len(by_risk[RiskLevel.MODERATE]) == 2
    assert len(by_risk[RiskLevel.REQUIRES_REBOOT]) == 1
    assert RiskLevel.DESTRUCTIVE not in by_risk


def test_m03_catalog_scan_before_spotfix_before_full_scan_reboot():
    module = load_module(CATALOG_PATH)
    ids = [a.id for a in module.actions]
    assert ids.index("disk_scan_readonly") < ids.index("disk_spotfix")
    assert ids.index("disk_spotfix") < ids.index("disk_full_scan_reboot")
    assert ids.index("disk_full_scan_reboot") < ids.index("disk_check_scheduled")


def test_m03_catalog_no_action_has_undo_command():
    module = load_module(CATALOG_PATH)
    for action in module.actions:
        assert action.undo_command is None
