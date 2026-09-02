from pathlib import Path

from portablefix.models import ModuleCategory, RiskLevel
from portablefix.module_engine import load_module

CATALOG_PATH = Path(__file__).resolve().parent.parent / "Modules" / "m09_tuning" / "actions.yaml"


def test_m09_catalog_loads_7_actions_in_repair_category():
    module = load_module(CATALOG_PATH)
    assert module.module_id == "m09_tuning"
    assert module.category == ModuleCategory.REPAIR
    assert len(module.actions) == 7


def test_m09_catalog_risk_distribution():
    module = load_module(CATALOG_PATH)
    by_risk = {}
    for action in module.actions:
        by_risk.setdefault(action.risk, []).append(action.id)
    assert len(by_risk[RiskLevel.SAFE]) == 2
    assert len(by_risk[RiskLevel.MODERATE]) == 5
    assert RiskLevel.DESTRUCTIVE not in by_risk
    assert RiskLevel.REQUIRES_REBOOT not in by_risk


def test_m09_catalog_undo_commands_on_all_moderate_actions():
    module = load_module(CATALOG_PATH)
    by_id = {a.id: a for a in module.actions}
    for undoable in (
        "tune_power_high_performance",
        "tune_visual_effects_performance",
        "tune_end_task_taskbar",
        "tune_sticky_keys_disable",
        "tune_classic_context_menu",
    ):
        assert by_id[undoable].undo_command is not None, undoable
    assert by_id["tune_power_plan_report"].undo_command is None
    assert by_id["tune_startup_apps_report"].undo_command is None


def test_m09_catalog_power_undo_restores_balanced_plan():
    module = load_module(CATALOG_PATH)
    by_id = {a.id: a for a in module.actions}
    assert "381b4222-f694-41f0-9685-ff5bb260df2e" in by_id["tune_power_high_performance"].undo_command
