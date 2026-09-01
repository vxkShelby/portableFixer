from pathlib import Path

from portablefix.models import ModuleCategory, RiskLevel
from portablefix.module_engine import load_module

CATALOG_PATH = Path(__file__).resolve().parent.parent / "Modules" / "m05_windows_update" / "actions.yaml"


def test_m05_catalog_loads_6_actions_in_repair_category():
    module = load_module(CATALOG_PATH)
    assert module.module_id == "m05_windows_update"
    assert module.category == ModuleCategory.REPAIR
    assert len(module.actions) == 6


def test_m05_catalog_risk_distribution():
    module = load_module(CATALOG_PATH)
    by_risk = {}
    for action in module.actions:
        by_risk.setdefault(action.risk, []).append(action.id)
    assert len(by_risk[RiskLevel.SAFE]) == 3
    assert len(by_risk[RiskLevel.MODERATE]) == 3
    assert RiskLevel.DESTRUCTIVE not in by_risk
    assert RiskLevel.REQUIRES_REBOOT not in by_risk


def test_m05_catalog_stop_services_before_reset_cache_before_restart_services():
    module = load_module(CATALOG_PATH)
    ids = [a.id for a in module.actions]
    assert ids.index("wu_stop_services") < ids.index("wu_reset_cache")
    assert ids.index("wu_reset_cache") < ids.index("wu_restart_services")


def test_m05_catalog_undo_commands_present_only_on_stop_services_and_reset_cache():
    module = load_module(CATALOG_PATH)
    by_id = {a.id: a for a in module.actions}
    assert by_id["wu_stop_services"].undo_command is not None
    assert by_id["wu_reset_cache"].undo_command is not None
    assert by_id["wu_check_services"].undo_command is None
    assert by_id["wu_restart_services"].undo_command is None
    assert by_id["wu_reregister_dlls"].undo_command is None
    assert by_id["wu_trigger_detection"].undo_command is None
