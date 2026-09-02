from pathlib import Path

from portablefix.models import ModuleCategory, RiskLevel
from portablefix.module_engine import load_module

CATALOG_PATH = Path(__file__).resolve().parent.parent / "Modules" / "m13_debloat" / "actions.yaml"


def test_m13_catalog_loads_17_actions_in_cleanup_category():
    module = load_module(CATALOG_PATH)
    assert module.module_id == "m13_debloat"
    assert module.category == ModuleCategory.CLEANUP
    assert len(module.actions) == 17


def test_m13_catalog_risk_distribution():
    module = load_module(CATALOG_PATH)
    by_risk = {}
    for action in module.actions:
        by_risk.setdefault(action.risk, []).append(action.id)
    assert by_risk[RiskLevel.SAFE] == ["debloat_list_installed"]
    assert set(by_risk[RiskLevel.MODERATE]) == {
        "debloat_remove_promo_apps",
        "debloat_disable_telemetry",
        "debloat_disable_suggestions",
        "debloat_remove_onedrive",
        "debloat_disable_web_search",
        "debloat_disable_copilot",
        "debloat_disable_widgets",
        "debloat_disable_advertising_id",
        "debloat_remove_xbox_identity",
        "debloat_disable_diagtrack",
        "debloat_disable_ceip_tasks",
        "debloat_disable_fast_startup",
        "debloat_disable_explorer_ads",
        "debloat_block_app_reinstall",
        "debloat_disable_recall_clicktodo",
    }
    assert by_risk[RiskLevel.DESTRUCTIVE] == ["debloat_remove_provisioned"]


def test_m13_registry_tweaks_have_undo_commands_removals_do_not():
    module = load_module(CATALOG_PATH)
    by_id = {a.id: a for a in module.actions}
    for undoable in (
        "debloat_disable_telemetry",
        "debloat_disable_suggestions",
        "debloat_disable_web_search",
        "debloat_disable_copilot",
        "debloat_disable_widgets",
        "debloat_disable_advertising_id",
        "debloat_disable_diagtrack",
        "debloat_disable_ceip_tasks",
        "debloat_disable_fast_startup",
        "debloat_disable_explorer_ads",
        "debloat_block_app_reinstall",
        "debloat_disable_recall_clicktodo",
    ):
        assert by_id[undoable].undo_command is not None, undoable
    for not_undoable in (
        "debloat_list_installed",
        "debloat_remove_promo_apps",
        "debloat_remove_provisioned",
        "debloat_remove_onedrive",
        "debloat_remove_xbox_identity",
    ):
        assert by_id[not_undoable].undo_command is None, not_undoable


def test_m13_removal_actions_never_touch_edge_onedrive_defender_or_store():
    module = load_module(CATALOG_PATH)
    by_id = {a.id: a for a in module.actions}
    for action_id in ("debloat_remove_promo_apps", "debloat_remove_provisioned"):
        command = by_id[action_id].command.lower()
        for forbidden in ("edge", "onedrive", "defender", "windowsstore", "storepurchaseapp", "quickassist"):
            assert forbidden not in command, f"{action_id} touches {forbidden}"


def test_m13_removal_actions_share_the_same_package_list():
    module = load_module(CATALOG_PATH)
    by_id = {a.id: a for a in module.actions}

    def extract_list(command: str) -> str:
        start = command.index("@(")
        end = command.index(")", start)
        return command[start : end + 1]

    assert extract_list(by_id["debloat_remove_promo_apps"].command) == extract_list(
        by_id["debloat_remove_provisioned"].command
    )
