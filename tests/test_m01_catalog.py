from pathlib import Path

from portablefix.models import ModuleCategory, RiskLevel
from portablefix.module_engine import load_module

CATALOG_PATH = Path(__file__).resolve().parent.parent / "Modules" / "m01_diagnostics" / "actions.yaml"


def test_m01_catalog_loads_15_actions_in_diagnostics_category():
    module = load_module(CATALOG_PATH)
    assert module.module_id == "m01_diagnostics"
    assert module.category == ModuleCategory.DIAGNOSTICS
    assert len(module.actions) == 15


def test_m01_catalog_all_actions_safe_readonly():
    module = load_module(CATALOG_PATH)
    for action in module.actions:
        assert action.risk == RiskLevel.SAFE
        assert action.undo_command is None


def test_m01_catalog_action_ids_are_unique():
    module = load_module(CATALOG_PATH)
    ids = [a.id for a in module.actions]
    assert len(ids) == len(set(ids))


def test_m01_catalog_covers_core_system_info_actions():
    module = load_module(CATALOG_PATH)
    ids = {a.id for a in module.actions}
    assert {
        "os_info", "computer_info", "bios_info", "cpu_info", "memory_info",
        "volumes", "physical_disks", "recent_hotfixes", "defender_status",
        "top_cpu_processes", "pending_reboot", "eventlog_critical_7d",
        "bsod_summary", "disk_reliability_counters", "installed_software",
    }.issubset(ids)


def test_m01_catalog_every_action_has_both_language_labels_and_descriptions():
    module = load_module(CATALOG_PATH)
    for action in module.actions:
        assert action.label_sk
        assert action.label_en
        assert action.description_sk
        assert action.description_en
