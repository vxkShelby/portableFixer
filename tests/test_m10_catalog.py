from pathlib import Path

from portablefix.models import ModuleCategory, RiskLevel
from portablefix.module_engine import load_module

CATALOG_PATH = Path(__file__).resolve().parent.parent / "Modules" / "m10_drivers" / "actions.yaml"


def test_m10_catalog_loads_7_actions_in_diagnostics_category():
    module = load_module(CATALOG_PATH)
    assert module.module_id == "m10_drivers"
    assert module.category == ModuleCategory.DIAGNOSTICS
    assert len(module.actions) == 7


def test_m10_catalog_risk_distribution():
    module = load_module(CATALOG_PATH)
    by_risk = {}
    for action in module.actions:
        by_risk.setdefault(action.risk, []).append(action.id)
    assert set(by_risk[RiskLevel.SAFE]) == {
        "drv_problem_devices",
        "drv_third_party_list",
        "drv_export_backup",
        "drv_stale_report",
        "drv_duplicate_packages_report",
        "drv_unsigned_report",
    }
    assert set(by_risk[RiskLevel.MODERATE]) == {"drv_restore_backup"}
    assert RiskLevel.DESTRUCTIVE not in by_risk
    assert RiskLevel.REQUIRES_REBOOT not in by_risk


def test_m10_catalog_no_undo_commands():
    module = load_module(CATALOG_PATH)
    for action in module.actions:
        assert action.undo_command is None


def test_m10_catalog_covers_expected_ids():
    module = load_module(CATALOG_PATH)
    ids = {a.id for a in module.actions}
    assert ids == {
        "drv_problem_devices",
        "drv_third_party_list",
        "drv_export_backup",
        "drv_stale_report",
        "drv_restore_backup",
        "drv_duplicate_packages_report",
        "drv_unsigned_report",
    }


def test_m10_catalog_duplicate_packages_report_is_report_only():
    module = load_module(CATALOG_PATH)
    action = next(a for a in module.actions if a.id == "drv_duplicate_packages_report")
    assert action.risk == RiskLevel.SAFE
    # The command only ever prints "pnputil /delete-driver ..." as advice for
    # the user to run themselves - it must never invoke it.
    assert "& pnputil /delete-driver" not in action.command
    assert "Remove-Item" not in action.command
    assert "pnputil /delete-driver" in action.description_en
    assert "pnputil /delete-driver" in action.description_sk


def test_m10_catalog_duplicate_packages_report_parses_by_position_not_english_labels():
    # pnputil's field labels (Published Name/Original Name) are localized to
    # the OS display language - matching against the English label text
    # would silently find zero duplicates on a non-English Windows install.
    # The command must instead rely on the fixed line position within each
    # per-driver block, which pnputil emits in the same order regardless of
    # display language.
    module = load_module(CATALOG_PATH)
    action = next(a for a in module.actions if a.id == "drv_duplicate_packages_report")
    assert "Original Name" not in action.command
    assert "Published Name" not in action.command
    assert "$lines[1]" in action.command


def test_m10_catalog_restore_backup_uses_export_backup_folder_naming():
    module = load_module(CATALOG_PATH)
    action = next(a for a in module.actions if a.id == "drv_restore_backup")
    assert "DriverBackup_" in action.command
    assert action.undo_command is None


def test_m10_catalog_driver_backup_locks_down_its_output_folder():
    module = load_module(CATALOG_PATH)
    action = next(a for a in module.actions if a.id == "drv_export_backup")
    assert "icacls" in action.command
    assert "S-1-5-32-544" in action.command
    assert "S-1-5-18" in action.command
    assert "$env:USERDOMAIN" in action.command
    # Must be unconditional (icacls is idempotent) so an already-existing
    # unhardened root from an older install gets fixed too, not just a
    # freshly created one.
    assert 'icacls $root /inheritance:r' in action.command
    assert "Test-Path $root" not in action.command


def test_m10_catalog_driver_backup_exit_code_reflects_actual_result():
    module = load_module(CATALOG_PATH)
    action = next(a for a in module.actions if a.id == "drv_export_backup")
    assert "exit 1" in action.command
    assert "exit 0" in action.command
