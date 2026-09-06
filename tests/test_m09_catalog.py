from pathlib import Path

from portablefix.models import ModuleCategory, RiskLevel
from portablefix.module_engine import load_module

CATALOG_PATH = Path(__file__).resolve().parent.parent / "Modules" / "m09_tuning" / "actions.yaml"


def test_m09_catalog_loads_8_actions_in_repair_category():
    module = load_module(CATALOG_PATH)
    assert module.module_id == "m09_tuning"
    assert module.category == ModuleCategory.REPAIR
    assert len(module.actions) == 8


def test_m09_catalog_risk_distribution():
    module = load_module(CATALOG_PATH)
    by_risk = {}
    for action in module.actions:
        by_risk.setdefault(action.risk, []).append(action.id)
    assert len(by_risk[RiskLevel.SAFE]) == 2
    assert len(by_risk[RiskLevel.MODERATE]) == 6
    assert RiskLevel.DESTRUCTIVE not in by_risk
    assert RiskLevel.REQUIRES_REBOOT not in by_risk


def test_m09_catalog_covers_expected_ids():
    module = load_module(CATALOG_PATH)
    ids = {a.id for a in module.actions}
    assert ids == {
        "tune_power_plan_report",
        "tune_power_high_performance",
        "tune_startup_apps_report",
        "tune_visual_effects_performance",
        "tune_end_task_taskbar",
        "tune_sticky_keys_disable",
        "tune_classic_context_menu",
        "tune_pause_background_services",
    }


def test_m09_catalog_undo_commands_on_all_moderate_actions():
    module = load_module(CATALOG_PATH)
    by_id = {a.id: a for a in module.actions}
    for undoable in (
        "tune_power_high_performance",
        "tune_visual_effects_performance",
        "tune_end_task_taskbar",
        "tune_sticky_keys_disable",
        "tune_classic_context_menu",
        "tune_pause_background_services",
    ):
        assert by_id[undoable].undo_command is not None, undoable
    assert by_id["tune_power_plan_report"].undo_command is None
    assert by_id["tune_startup_apps_report"].undo_command is None


def test_m09_catalog_power_undo_restores_balanced_plan():
    module = load_module(CATALOG_PATH)
    by_id = {a.id: a for a in module.actions}
    assert "381b4222-f694-41f0-9685-ff5bb260df2e" in by_id["tune_power_high_performance"].undo_command


def test_m09_catalog_pause_services_verifies_the_stop_actually_worked():
    # Stop-Service/Start-Service on WSearch/SysMain silently no-op without
    # administrator - the command must check the service's actual status
    # afterward and fail rather than claim success on a no-op.
    module = load_module(CATALOG_PATH)
    action = next(a for a in module.actions if a.id == "tune_pause_background_services")
    assert action.risk == RiskLevel.MODERATE
    assert "$svc.Refresh()" in action.command
    assert "-eq 'Stopped'" in action.command
    assert "exit 1" in action.command
    assert "$svc.Refresh()" in action.undo_command
    assert "exit 1" in action.undo_command
