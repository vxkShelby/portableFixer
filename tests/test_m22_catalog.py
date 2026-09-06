from pathlib import Path

from portablefix.models import ModuleCategory, RiskLevel
from portablefix.module_engine import load_module

CATALOG_PATH = Path(__file__).resolve().parent.parent / "Modules" / "m22_deep_cleanup" / "actions.yaml"


def test_m22_catalog_loads_4_actions_in_cleanup_category():
    module = load_module(CATALOG_PATH)
    assert module.module_id == "m22_deep_cleanup"
    assert module.category == ModuleCategory.CLEANUP
    assert len(module.actions) == 4


def test_m22_catalog_risk_distribution():
    module = load_module(CATALOG_PATH)
    by_risk = {}
    for action in module.actions:
        by_risk.setdefault(action.risk, []).append(action.id)
    assert set(by_risk[RiskLevel.SAFE]) == {
        "leftover_uninstall_keys_report",
        "duplicate_files_report",
        "broken_shortcuts_report",
    }
    assert by_risk[RiskLevel.MODERATE] == ["wipe_free_space"]
    assert RiskLevel.DESTRUCTIVE not in by_risk
    assert RiskLevel.REQUIRES_REBOOT not in by_risk


def test_m22_catalog_no_action_has_undo_command():
    # All three report actions are deliberately report-only (no companion
    # delete/fix action - registry/hash/shortcut false positives make manual
    # review the right call), and wipe_free_space only touches already-free
    # space, so there is nothing to undo.
    module = load_module(CATALOG_PATH)
    for action in module.actions:
        assert action.undo_command is None, action.id


def test_m22_catalog_covers_expected_ids():
    module = load_module(CATALOG_PATH)
    ids = {a.id for a in module.actions}
    assert ids == {
        "leftover_uninstall_keys_report",
        "duplicate_files_report",
        "broken_shortcuts_report",
        "wipe_free_space",
    }


def test_m22_catalog_every_action_has_both_language_labels_and_descriptions():
    module = load_module(CATALOG_PATH)
    for action in module.actions:
        assert action.label_sk
        assert action.label_en
        assert action.description_sk
        assert action.description_en


def test_m22_catalog_long_running_actions_declare_inactivity_timeout_sec():
    module = load_module(CATALOG_PATH)
    by_id = {a.id: a for a in module.actions}
    assert by_id["duplicate_files_report"].inactivity_timeout_sec == 900
    assert by_id["wipe_free_space"].inactivity_timeout_sec == 1800
    default_timeout_ids = {"leftover_uninstall_keys_report", "broken_shortcuts_report"}
    for action_id in default_timeout_ids:
        assert by_id[action_id].inactivity_timeout_sec is None, action_id


def test_m22_catalog_wipe_free_space_has_a_raised_hard_cap():
    # cipher /w on a large/mostly-full drive can genuinely run for hours -
    # the global 2-hour HARD_CAP_SEC would kill a legitimately-progressing
    # wipe and report it as "timed out" instead of just slow.
    module = load_module(CATALOG_PATH)
    action = next(a for a in module.actions if a.id == "wipe_free_space")
    assert action.hard_cap_sec == 21600


def test_m22_catalog_wipe_free_space_targets_the_system_drive_not_a_hardcoded_letter():
    # A portable tool can't assume the OS lives on C: - every other
    # drive-wide action in this codebase (m03_disk) uses $env:SystemDrive.
    module = load_module(CATALOG_PATH)
    action = next(a for a in module.actions if a.id == "wipe_free_space")
    assert "$env:SystemDrive" in action.command
    assert "C:\\" not in action.command


def test_m22_catalog_duplicate_files_report_skips_legacy_compatibility_junctions():
    # Windows PowerShell 5.1's -Recurse follows reparse points, so classic
    # per-profile compat junctions (Application Data -> AppData\Roaming,
    # My Documents -> Documents, etc.) would make -Recurse visit the same
    # physical file twice under two different logical paths, producing a
    # false-positive "duplicate".
    module = load_module(CATALOG_PATH)
    action = next(a for a in module.actions if a.id == "duplicate_files_report")
    assert "ReparsePoint" in action.command
