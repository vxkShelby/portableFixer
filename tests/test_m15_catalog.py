from pathlib import Path

from portablefix.models import ModuleCategory, RiskLevel
from portablefix.module_engine import load_module

CATALOG_PATH = Path(__file__).resolve().parent.parent / "Modules" / "m15_boot_platform" / "actions.yaml"


def test_m15_catalog_loads_6_actions_in_repair_category():
    module = load_module(CATALOG_PATH)
    assert module.module_id == "m15_boot_platform"
    assert module.category == ModuleCategory.REPAIR
    assert len(module.actions) == 6


def test_m15_catalog_risk_distribution():
    module = load_module(CATALOG_PATH)
    by_risk = {}
    for action in module.actions:
        by_risk.setdefault(action.risk, []).append(action.id)
    assert set(by_risk[RiskLevel.SAFE]) == {
        "boot_bcd_report",
        "boot_tpm_status",
        "boot_bitlocker_status",
        "boot_safe_mode_status",
    }
    assert set(by_risk[RiskLevel.MODERATE]) == {
        "boot_clear_safe_mode_flag",
        "boot_enable_f8_legacy_recovery",
    }
    assert RiskLevel.DESTRUCTIVE not in by_risk
    assert RiskLevel.REQUIRES_REBOOT not in by_risk


def test_m15_catalog_reversible_actions_have_undo():
    module = load_module(CATALOG_PATH)
    by_id = {a.id: a for a in module.actions}
    for undoable in ("boot_enable_f8_legacy_recovery", "boot_clear_safe_mode_flag"):
        assert by_id[undoable].undo_command is not None, undoable
    for not_undoable in (
        "boot_bcd_report",
        "boot_tpm_status",
        "boot_bitlocker_status",
        "boot_safe_mode_status",
    ):
        assert by_id[not_undoable].undo_command is None, not_undoable


def test_m15_catalog_covers_expected_ids():
    module = load_module(CATALOG_PATH)
    ids = {a.id for a in module.actions}
    assert ids == {
        "boot_bcd_report",
        "boot_tpm_status",
        "boot_bitlocker_status",
        "boot_safe_mode_status",
        "boot_clear_safe_mode_flag",
        "boot_enable_f8_legacy_recovery",
    }


def test_m15_catalog_every_action_has_both_language_labels_and_descriptions():
    module = load_module(CATALOG_PATH)
    for action in module.actions:
        assert action.label_sk
        assert action.label_en
        assert action.description_sk
        assert action.description_en


def test_m15_catalog_clear_safe_mode_flag_does_not_overwrite_backup_on_second_run():
    module = load_module(CATALOG_PATH)
    action = next(a for a in module.actions if a.id == "boot_clear_safe_mode_flag")
    assert "if (-not (Test-Path $bk))" in action.command


def test_m15_catalog_clear_safe_mode_flag_locks_down_its_backup_folder():
    module = load_module(CATALOG_PATH)
    action = next(a for a in module.actions if a.id == "boot_clear_safe_mode_flag")
    assert "icacls" in action.command
    assert "S-1-5-32-544" in action.command
    assert "S-1-5-18" in action.command
    assert "$env:USERDOMAIN" in action.command
