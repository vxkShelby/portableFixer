from pathlib import Path

from portablefix.models import ModuleCategory, RiskLevel
from portablefix.module_engine import load_module

CATALOG_PATH = Path(__file__).resolve().parent.parent / "Modules" / "m14_printing" / "actions.yaml"


def test_m14_catalog_loads_6_actions_in_repair_category():
    module = load_module(CATALOG_PATH)
    assert module.module_id == "m14_printing"
    assert module.category == ModuleCategory.REPAIR
    assert len(module.actions) == 6


def test_m14_catalog_risk_distribution():
    module = load_module(CATALOG_PATH)
    by_risk = {}
    for action in module.actions:
        by_risk.setdefault(action.risk, []).append(action.id)
    assert set(by_risk[RiskLevel.SAFE]) == {
        "print_installed_printers_report",
        "print_driver_store_report",
        "print_offline_ghost_printers_report",
    }
    assert set(by_risk[RiskLevel.MODERATE]) == {"print_remove_offline_printers"}
    assert set(by_risk[RiskLevel.DESTRUCTIVE]) == {
        "print_remove_orphaned_drivers",
        "print_reset_print_system",
    }
    assert RiskLevel.REQUIRES_REBOOT not in by_risk


def test_m14_catalog_only_offline_printer_removal_has_undo():
    module = load_module(CATALOG_PATH)
    by_id = {a.id: a for a in module.actions}
    assert by_id["print_remove_offline_printers"].undo_command is not None
    for not_undoable in (
        "print_installed_printers_report",
        "print_driver_store_report",
        "print_offline_ghost_printers_report",
        "print_remove_orphaned_drivers",
        "print_reset_print_system",
    ):
        assert by_id[not_undoable].undo_command is None, not_undoable


def test_m14_catalog_covers_expected_ids():
    module = load_module(CATALOG_PATH)
    ids = {a.id for a in module.actions}
    assert ids == {
        "print_installed_printers_report",
        "print_driver_store_report",
        "print_offline_ghost_printers_report",
        "print_remove_offline_printers",
        "print_remove_orphaned_drivers",
        "print_reset_print_system",
    }


def test_m14_catalog_every_action_has_both_language_labels_and_descriptions():
    module = load_module(CATALOG_PATH)
    for action in module.actions:
        assert action.label_sk
        assert action.label_en
        assert action.description_sk
        assert action.description_en


def test_m14_catalog_reset_print_system_verifies_it_actually_worked():
    # Stop-Service/Start-Service on Spooler silently no-op without
    # administrator - the command must check via -EA Stop/try-catch and a
    # final Get-Printer re-check, not just claim success unconditionally.
    module = load_module(CATALOG_PATH)
    action = next(a for a in module.actions if a.id == "print_reset_print_system")
    assert "-EA Stop" in action.command
    assert "exit 1" in action.command
    assert "Get-Printer -EA SilentlyContinue" in action.command
