from pathlib import Path

from portablefix.models import ModuleCategory, RiskLevel
from portablefix.module_engine import load_module

CATALOG_PATH = Path(__file__).resolve().parent.parent / "Modules" / "m13_debloat" / "actions.yaml"


def test_m13_catalog_loads_5_actions_in_cleanup_category():
    module = load_module(CATALOG_PATH)
    assert module.module_id == "m13_debloat"
    assert module.category == ModuleCategory.CLEANUP
    assert len(module.actions) == 5


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
    }
    assert by_risk[RiskLevel.DESTRUCTIVE] == ["debloat_remove_provisioned"]


def test_m13_registry_tweaks_have_undo_commands_removals_do_not():
    module = load_module(CATALOG_PATH)
    by_id = {a.id: a for a in module.actions}
    assert by_id["debloat_disable_telemetry"].undo_command is not None
    assert by_id["debloat_disable_suggestions"].undo_command is not None
    assert by_id["debloat_list_installed"].undo_command is None
    assert by_id["debloat_remove_promo_apps"].undo_command is None
    assert by_id["debloat_remove_provisioned"].undo_command is None


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
