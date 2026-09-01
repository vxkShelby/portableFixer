from pathlib import Path

from portablefix.models import ModuleCategory, RiskLevel
from portablefix.module_engine import load_module

CATALOG_PATH = Path(__file__).resolve().parent.parent / "Modules" / "m12_online" / "actions.yaml"


def test_m12_catalog_loads_3_actions_in_diagnostics_category():
    module = load_module(CATALOG_PATH)
    assert module.module_id == "m12_online"
    assert module.category == ModuleCategory.DIAGNOSTICS
    assert len(module.actions) == 3


def test_m12_catalog_all_actions_safe_readonly():
    module = load_module(CATALOG_PATH)
    for action in module.actions:
        assert action.risk == RiskLevel.SAFE
        assert action.undo_command is None


def test_m12_catalog_covers_connectivity_dns_and_proxy():
    module = load_module(CATALOG_PATH)
    ids = {a.id for a in module.actions}
    assert ids == {"online_connectivity_ladder", "online_dns_benchmark", "online_proxy_check"}
