from pathlib import Path

from portablefix.models import ModuleCategory, RiskLevel
from portablefix.module_engine import load_module

CATALOG_PATH = Path(__file__).resolve().parent.parent / "Modules" / "m10_drivers" / "actions.yaml"


def test_m10_catalog_loads_3_actions_in_diagnostics_category():
    module = load_module(CATALOG_PATH)
    assert module.module_id == "m10_drivers"
    assert module.category == ModuleCategory.DIAGNOSTICS
    assert len(module.actions) == 3


def test_m10_catalog_all_actions_safe_readonly():
    module = load_module(CATALOG_PATH)
    for action in module.actions:
        assert action.risk == RiskLevel.SAFE
        assert action.undo_command is None


def test_m10_catalog_covers_problem_devices_and_third_party_drivers():
    module = load_module(CATALOG_PATH)
    ids = {a.id for a in module.actions}
    assert ids == {"drv_problem_devices", "drv_third_party_list", "drv_export_backup"}
