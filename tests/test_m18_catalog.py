from pathlib import Path

from portablefix.models import ModuleCategory, RiskLevel
from portablefix.module_engine import load_module

CATALOG_PATH = Path(__file__).resolve().parent.parent / "Modules" / "m18_user_backup" / "actions.yaml"


def test_m18_catalog_loads_3_actions_in_repair_category():
    module = load_module(CATALOG_PATH)
    assert module.module_id == "m18_user_backup"
    assert module.category == ModuleCategory.REPAIR
    assert len(module.actions) == 3


def test_m18_catalog_risk_distribution():
    module = load_module(CATALOG_PATH)
    by_risk = {}
    for action in module.actions:
        by_risk.setdefault(action.risk, []).append(action.id)
    assert set(by_risk[RiskLevel.SAFE]) == {"backup_list_existing"}
    assert set(by_risk[RiskLevel.MODERATE]) == {
        "backup_user_folders",
        "backup_restore_latest",
    }
    assert RiskLevel.DESTRUCTIVE not in by_risk
    assert RiskLevel.REQUIRES_REBOOT not in by_risk


def test_m18_catalog_only_backup_creation_has_undo_and_preview():
    module = load_module(CATALOG_PATH)
    by_id = {a.id: a for a in module.actions}
    assert by_id["backup_user_folders"].undo_command is not None
    assert by_id["backup_user_folders"].preview_command is not None
    assert by_id["backup_list_existing"].undo_command is None
    assert by_id["backup_restore_latest"].undo_command is None


def test_m18_catalog_robocopy_exit_code_normalized_to_strict_zero():
    # robocopy's own exit codes 0-7 are success (bitflags), only 8+ is a real
    # failure - the command must translate that before the app's strict
    # exit_code == 0 check treats a normal robocopy run as a failure.
    module = load_module(CATALOG_PATH)
    action = next(a for a in module.actions if a.id == "backup_user_folders")
    assert "$LASTEXITCODE -ge 8" in action.command


def test_m18_catalog_covers_expected_ids():
    module = load_module(CATALOG_PATH)
    ids = {a.id for a in module.actions}
    assert ids == {
        "backup_user_folders",
        "backup_list_existing",
        "backup_restore_latest",
    }


def test_m18_catalog_every_action_has_both_language_labels_and_descriptions():
    module = load_module(CATALOG_PATH)
    for action in module.actions:
        assert action.label_sk
        assert action.label_en
        assert action.description_sk
        assert action.description_en
