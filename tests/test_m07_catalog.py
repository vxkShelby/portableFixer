from pathlib import Path

from portablefix.models import ModuleCategory, RiskLevel
from portablefix.module_engine import load_module

CATALOG_PATH = Path(__file__).resolve().parent.parent / "Modules" / "m07_autoruns" / "actions.yaml"


def test_m07_catalog_loads_5_actions_in_diagnostics_category():
    module = load_module(CATALOG_PATH)
    assert module.module_id == "m07_autoruns"
    assert module.category == ModuleCategory.DIAGNOSTICS
    assert len(module.actions) == 5


def test_m07_catalog_all_actions_safe_readonly():
    module = load_module(CATALOG_PATH)
    for action in module.actions:
        assert action.risk == RiskLevel.SAFE
        assert action.undo_command is None


def test_m07_catalog_covers_all_autostart_surfaces():
    module = load_module(CATALOG_PATH)
    ids = {a.id for a in module.actions}
    assert ids == {
        "autoruns_registry_run",
        "autoruns_startup_folder",
        "autoruns_scheduled_tasks",
        "autoruns_autostart_services",
        "autoruns_wmi_event_subscriptions",
    }


def test_m07_catalog_wmi_subscriptions_degrades_gracefully_and_notes_legitimate_scm_entry():
    module = load_module(CATALOG_PATH)
    by_id = {a.id: a for a in module.actions}
    action = by_id["autoruns_wmi_event_subscriptions"]
    assert "try {" in action.command
    assert "catch {" in action.command
    assert "SCM Event Log" in action.description_en


def test_m07_catalog_registry_action_covers_hklm_and_hkcu():
    module = load_module(CATALOG_PATH)
    by_id = {a.id: a for a in module.actions}
    command = by_id["autoruns_registry_run"].command
    assert "HKLM:" in command
    assert "HKCU:" in command
    assert "RunOnce" in command
