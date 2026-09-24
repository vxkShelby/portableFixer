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


def test_m14_reset_print_system_removes_printers_only_once_the_spooler_is_back():
    # Remove-Printer goes through the spooler: run while Spooler was stopped
    # (the old order) it removed nothing, and the final re-check then failed
    # every time. Stop -> clear the spool folder -> Start -> Remove -> re-check.
    module = load_module(CATALOG_PATH)
    command = next(a for a in module.actions if a.id == "print_reset_print_system").command
    stop = command.index("Stop-Service")
    spool = command.index("spool\\PRINTERS")
    start = command.index("Start-Service")
    remove = command.index("Remove-Printer")
    assert stop < spool < start < remove
    # @() so a single remaining printer object still counts and joins by name
    recheck = command[remove:]
    assert "@($stillThere).Count" in recheck
    assert "(@($stillThere).Name -join ', ')" in recheck
    assert "exit 1" in recheck


def test_m14_remove_orphaned_drivers_never_treats_every_driver_as_orphaned():
    # A failing Get-Printer (spooler down, corrupt queue) left $inUse empty,
    # so EVERY driver - including ones real printers use - looked orphaned
    # and was deleted, and failed removals still exited 0.
    module = load_module(CATALOG_PATH)
    command = next(a for a in module.actions if a.id == "print_remove_orphaned_drivers").command
    assert "try { $inUse = @((Get-Printer -EA Stop).DriverName)" in command
    listing_catch = command[command.index("catch {"):]
    assert "exit 1" in listing_catch[:listing_catch.index("}")]
    assert command.index("exit 1") < command.index("Remove-PrinterDriver")
    assert "$failed++" in command
    assert "exit 1" in command[command.rindex("if ($failed -gt 0)"):]
