from pathlib import Path

from portablefix.models import ModuleCategory, RiskLevel
from portablefix.module_engine import load_module

CATALOG_PATH = Path(__file__).resolve().parent.parent / "Modules" / "m19_win_features" / "actions.yaml"


def test_m19_catalog_loads_5_actions_in_repair_category():
    module = load_module(CATALOG_PATH)
    assert module.module_id == "m19_win_features"
    assert module.category == ModuleCategory.REPAIR
    assert len(module.actions) == 5


def test_m19_catalog_risk_distribution():
    module = load_module(CATALOG_PATH)
    by_risk = {}
    for action in module.actions:
        by_risk.setdefault(action.risk, []).append(action.id)
    assert set(by_risk[RiskLevel.SAFE]) == {
        "feature_list_report",
        "feature_legacy_insecure_report",
    }
    assert set(by_risk[RiskLevel.MODERATE]) == {"feature_disable_powershell_v2"}
    assert set(by_risk[RiskLevel.REQUIRES_REBOOT]) == {
        "feature_enable_dotnet35",
        "feature_enable_sandbox",
    }
    assert RiskLevel.DESTRUCTIVE not in by_risk


def test_m19_catalog_every_toggleable_feature_has_undo():
    module = load_module(CATALOG_PATH)
    by_id = {a.id: a for a in module.actions}
    for undoable in (
        "feature_disable_powershell_v2",
        "feature_enable_dotnet35",
        "feature_enable_sandbox",
    ):
        assert by_id[undoable].undo_command is not None, undoable
    for not_undoable in ("feature_list_report", "feature_legacy_insecure_report"):
        assert by_id[not_undoable].undo_command is None, not_undoable


def test_m19_catalog_report_actions_degrade_gracefully_without_admin():
    # Get-WindowsOptionalFeature -Online requires elevation even to just
    # list features; a SAFE action must not surface a raw COMException.
    module = load_module(CATALOG_PATH)
    by_id = {a.id: a for a in module.actions}
    for report_id in ("feature_list_report", "feature_legacy_insecure_report"):
        command = by_id[report_id].command
        assert "try {" in command
        assert "catch {" in command
        assert "needs administrator" in command


def test_m19_catalog_covers_expected_ids():
    module = load_module(CATALOG_PATH)
    ids = {a.id for a in module.actions}
    assert ids == {
        "feature_list_report",
        "feature_legacy_insecure_report",
        "feature_enable_dotnet35",
        "feature_disable_powershell_v2",
        "feature_enable_sandbox",
    }


def test_m19_catalog_every_action_has_both_language_labels_and_descriptions():
    module = load_module(CATALOG_PATH)
    for action in module.actions:
        assert action.label_sk
        assert action.label_en
        assert action.description_sk
        assert action.description_en
