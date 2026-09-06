from pathlib import Path

from portablefix.models import ModuleCategory, RiskLevel
from portablefix.module_engine import load_module

CATALOG_PATH = Path(__file__).resolve().parent.parent / "Modules" / "m05_windows_update" / "actions.yaml"


def test_m05_catalog_loads_8_actions_in_repair_category():
    module = load_module(CATALOG_PATH)
    assert module.module_id == "m05_windows_update"
    assert module.category == ModuleCategory.REPAIR
    assert len(module.actions) == 8


def test_m05_catalog_risk_distribution():
    module = load_module(CATALOG_PATH)
    by_risk = {}
    for action in module.actions:
        by_risk.setdefault(action.risk, []).append(action.id)
    assert set(by_risk[RiskLevel.SAFE]) == {
        "wu_check_services",
        "wu_restart_services",
        "wu_trigger_detection",
        "wu_driver_updates_report",
    }
    assert set(by_risk[RiskLevel.MODERATE]) == {
        "wu_stop_services",
        "wu_reset_cache",
        "wu_reregister_dlls",
    }
    assert set(by_risk[RiskLevel.REQUIRES_REBOOT]) == {"wu_uninstall_last_update"}
    assert RiskLevel.DESTRUCTIVE not in by_risk


def test_m05_catalog_covers_expected_ids():
    module = load_module(CATALOG_PATH)
    ids = {a.id for a in module.actions}
    assert ids == {
        "wu_check_services",
        "wu_stop_services",
        "wu_reset_cache",
        "wu_restart_services",
        "wu_reregister_dlls",
        "wu_trigger_detection",
        "wu_driver_updates_report",
        "wu_uninstall_last_update",
    }


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
    assert by_id["wu_driver_updates_report"].undo_command is None
    assert by_id["wu_uninstall_last_update"].undo_command is None


def test_m05_catalog_driver_updates_report_uses_wua_com_api_not_trigger_detection():
    module = load_module(CATALOG_PATH)
    action = next(a for a in module.actions if a.id == "wu_driver_updates_report")
    assert action.risk == RiskLevel.SAFE
    assert "Microsoft.Update.Session" in action.command
    assert "UsoClient" not in action.command


def test_m05_catalog_uninstall_last_update_requires_reboot():
    module = load_module(CATALOG_PATH)
    action = next(a for a in module.actions if a.id == "wu_uninstall_last_update")
    assert action.risk == RiskLevel.REQUIRES_REBOOT
    assert action.undo_command is None
    assert "wusa.exe" in action.command
