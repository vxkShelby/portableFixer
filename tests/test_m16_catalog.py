from pathlib import Path

from portablefix.models import ModuleCategory, RiskLevel
from portablefix.module_engine import load_module

CATALOG_PATH = Path(__file__).resolve().parent.parent / "Modules" / "m16_office_repair" / "actions.yaml"


def test_m16_catalog_loads_6_actions_in_repair_category():
    module = load_module(CATALOG_PATH)
    assert module.module_id == "m16_office_repair"
    assert module.category == ModuleCategory.REPAIR
    assert len(module.actions) == 6


def test_m16_catalog_risk_distribution():
    module = load_module(CATALOG_PATH)
    by_risk = {}
    for action in module.actions:
        by_risk.setdefault(action.risk, []).append(action.id)
    assert set(by_risk[RiskLevel.SAFE]) == {
        "office_version_channel_report",
        "office_addins_report",
        "office_ost_pst_report",
    }
    assert set(by_risk[RiskLevel.MODERATE]) == {
        "office_com_addin_disable_all_thirdparty",
        "office_quick_repair",
        "office_online_repair",
    }
    assert RiskLevel.DESTRUCTIVE not in by_risk
    assert RiskLevel.REQUIRES_REBOOT not in by_risk


def test_m16_catalog_only_addin_disable_has_undo():
    module = load_module(CATALOG_PATH)
    by_id = {a.id: a for a in module.actions}
    assert by_id["office_com_addin_disable_all_thirdparty"].undo_command is not None
    for not_undoable in (
        "office_version_channel_report",
        "office_addins_report",
        "office_ost_pst_report",
        "office_quick_repair",
        "office_online_repair",
    ):
        assert by_id[not_undoable].undo_command is None, not_undoable


def test_m16_catalog_covers_expected_ids():
    module = load_module(CATALOG_PATH)
    ids = {a.id for a in module.actions}
    assert ids == {
        "office_version_channel_report",
        "office_addins_report",
        "office_ost_pst_report",
        "office_com_addin_disable_all_thirdparty",
        "office_quick_repair",
        "office_online_repair",
    }


def test_m16_catalog_addins_report_has_empty_result_fallback():
    module = load_module(CATALOG_PATH)
    action = next(a for a in module.actions if a.id == "office_addins_report")
    assert "No Outlook add-ins found." in action.command


def test_m16_catalog_every_action_has_both_language_labels_and_descriptions():
    module = load_module(CATALOG_PATH)
    for action in module.actions:
        assert action.label_sk
        assert action.label_en
        assert action.description_sk
        assert action.description_en
