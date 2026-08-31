# tests/test_m02_catalog.py
from pathlib import Path

from portablefix.models import RiskLevel
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
