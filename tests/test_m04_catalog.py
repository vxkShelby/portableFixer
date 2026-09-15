from pathlib import Path

from portablefix.models import ModuleCategory, RiskLevel
from portablefix.module_engine import load_module

CATALOG_PATH = Path(__file__).resolve().parent.parent / "Modules" / "m04_integrity" / "actions.yaml"


def test_m04_catalog_loads_12_actions_in_repair_category():
    module = load_module(CATALOG_PATH)
    assert module.module_id == "m04_integrity"
    assert module.category == ModuleCategory.REPAIR
    assert len(module.actions) == 12


def test_m04_catalog_risk_distribution():
    module = load_module(CATALOG_PATH)
    by_risk = {}
    for action in module.actions:
        by_risk.setdefault(action.risk, []).append(action.id)
    assert len(by_risk[RiskLevel.SAFE]) == 5
    assert len(by_risk[RiskLevel.MODERATE]) == 5
    assert len(by_risk[RiskLevel.DESTRUCTIVE]) == 1
    assert len(by_risk[RiskLevel.REQUIRES_REBOOT]) == 1


def test_m04_catalog_covers_expected_ids():
    module = load_module(CATALOG_PATH)
    ids = {a.id for a in module.actions}
    assert ids == {
        "dism_checkhealth",
        "dism_scanhealth",
        "dism_restorehealth",
        "sfc_scannow",
        "sfc_verifyonly",
        "appx_reregister",
        "wmi_verify",
        "wmi_backup",
        "wmi_salvage",
        "search_index_rebuild",
        "store_cache_reset",
        "perf_counters_rebuild",
    }


def test_m04_catalog_new_moderate_repair_actions_have_no_undo():
    # None of these have a sensible undo (rebuild/cache-clear operations
    # with no better "previous state" to roll back to) - matches the
    # existing no-undo precedent of dism_scanhealth/disk_optimize_volume.
    module = load_module(CATALOG_PATH)
    by_id = {a.id: a for a in module.actions}
    for action_id in ("search_index_rebuild", "store_cache_reset", "perf_counters_rebuild"):
        assert by_id[action_id].risk == RiskLevel.MODERATE, action_id
        assert by_id[action_id].undo_command is None, action_id


def test_m04_catalog_wmi_salvage_undo_restores_from_wmi_backup_action():
    module = load_module(CATALOG_PATH)
    by_id = {a.id: a for a in module.actions}
    assert by_id["wmi_backup"].undo_command is None
    assert by_id["wmi_salvage"].undo_command is not None
    assert "wmi_backup.bin" in by_id["wmi_backup"].command
    assert "wmi_backup.bin" in by_id["wmi_salvage"].undo_command


def test_m04_catalog_dism_restorehealth_before_sfc_scannow():
    module = load_module(CATALOG_PATH)
    ids = [a.id for a in module.actions]
    assert ids.index("dism_restorehealth") < ids.index("sfc_scannow")
    assert ids.index("sfc_scannow") < ids.index("sfc_verifyonly")


def test_m04_catalog_no_preview_command_set():
    module = load_module(CATALOG_PATH)
    for action in module.actions:
        assert action.preview_command is None
