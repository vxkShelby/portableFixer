from pathlib import Path

from portablefix.models import ModuleCategory, RiskLevel
from portablefix.module_engine import load_module

CATALOG_PATH = Path(__file__).resolve().parent.parent / "Modules" / "m04_integrity" / "actions.yaml"


def test_m04_catalog_loads_8_actions_in_repair_category():
    module = load_module(CATALOG_PATH)
    assert module.module_id == "m04_integrity"
    assert module.category == ModuleCategory.REPAIR
    assert len(module.actions) == 8


def test_m04_catalog_risk_distribution():
    module = load_module(CATALOG_PATH)
    by_risk = {}
    for action in module.actions:
        by_risk.setdefault(action.risk, []).append(action.id)
    assert len(by_risk[RiskLevel.SAFE]) == 4
    assert len(by_risk[RiskLevel.MODERATE]) == 2
    assert len(by_risk[RiskLevel.DESTRUCTIVE]) == 1
    assert len(by_risk[RiskLevel.REQUIRES_REBOOT]) == 1


def test_m04_catalog_dism_restorehealth_before_sfc_scannow():
    module = load_module(CATALOG_PATH)
    ids = [a.id for a in module.actions]
    assert ids.index("dism_restorehealth") < ids.index("sfc_scannow")
    assert ids.index("sfc_scannow") < ids.index("sfc_verifyonly")


def test_m04_catalog_no_preview_command_set():
    module = load_module(CATALOG_PATH)
    for action in module.actions:
        assert action.preview_command is None
