from pathlib import Path

from portablefix.models import ModuleCategory, RiskLevel
from portablefix.module_engine import load_module

CATALOG_PATH = Path(__file__).resolve().parent.parent / "Modules" / "m12_online" / "actions.yaml"


def test_m12_catalog_loads_5_actions_in_diagnostics_category():
    module = load_module(CATALOG_PATH)
    assert module.module_id == "m12_online"
    assert module.category == ModuleCategory.DIAGNOSTICS
    assert len(module.actions) == 5


def test_m12_catalog_risk_and_undo():
    module = load_module(CATALOG_PATH)
    by_id = {a.id: a for a in module.actions}
    for action_id in ("online_connectivity_ladder", "online_dns_benchmark", "online_proxy_check", "online_speed_test"):
        assert by_id[action_id].risk == RiskLevel.SAFE, action_id
        assert by_id[action_id].undo_command is None, action_id
    assert by_id["online_proxy_reset"].risk == RiskLevel.MODERATE
    assert by_id["online_proxy_reset"].undo_command is not None


def test_m12_catalog_covers_connectivity_dns_and_proxy():
    module = load_module(CATALOG_PATH)
    ids = {a.id for a in module.actions}
    assert ids == {
        "online_connectivity_ladder",
        "online_dns_benchmark",
        "online_proxy_check",
        "online_proxy_reset",
        "online_speed_test",
    }
