from pathlib import Path

from portablefix.models import ModuleCategory, RiskLevel
from portablefix.module_engine import load_module

CATALOG_PATH = Path(__file__).resolve().parent.parent / "Modules" / "m15_boot_platform" / "actions.yaml"


def test_m15_catalog_loads_5_actions_in_repair_category():
    module = load_module(CATALOG_PATH)
    assert module.module_id == "m15_boot_platform"
    assert module.category == ModuleCategory.REPAIR
    assert len(module.actions) == 5


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
    assert set(by_risk[RiskLevel.MODERATE]) == {"boot_clear_safe_mode_flag"}
    assert RiskLevel.DESTRUCTIVE not in by_risk
    assert RiskLevel.REQUIRES_REBOOT not in by_risk


def test_m15_catalog_no_action_has_undo():
    module = load_module(CATALOG_PATH)
    for action in module.actions:
        assert action.undo_command is None, action.id


def test_m15_catalog_covers_expected_ids():
    module = load_module(CATALOG_PATH)
    ids = {a.id for a in module.actions}
    assert ids == {
        "boot_bcd_report",
        "boot_tpm_status",
        "boot_bitlocker_status",
        "boot_safe_mode_status",
        "boot_clear_safe_mode_flag",
    }


def test_m15_catalog_every_action_has_both_language_labels_and_descriptions():
    module = load_module(CATALOG_PATH)
    for action in module.actions:
        assert action.label_sk
        assert action.label_en
        assert action.description_sk
        assert action.description_en
