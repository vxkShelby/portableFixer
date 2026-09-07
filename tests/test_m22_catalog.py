from pathlib import Path

from portablefix.models import ModuleCategory, RiskLevel
from portablefix.module_engine import load_module

CATALOG_PATH = Path(__file__).resolve().parent.parent / "Modules" / "m22_deep_cleanup" / "actions.yaml"


def test_m22_catalog_loads_6_actions_in_cleanup_category():
    module = load_module(CATALOG_PATH)
    assert module.module_id == "m22_deep_cleanup"
    assert module.category == ModuleCategory.CLEANUP
    assert len(module.actions) == 6


def test_m22_catalog_risk_distribution():
    module = load_module(CATALOG_PATH)
    by_risk = {}
    for action in module.actions:
        by_risk.setdefault(action.risk, []).append(action.id)
    assert set(by_risk[RiskLevel.SAFE]) == {
        "leftover_uninstall_keys_report",
        "duplicate_files_report",
        "broken_shortcuts_report",
        "shortcut_hijack_report",
        "disk_space_by_folder_report",
    }
    assert by_risk[RiskLevel.MODERATE] == ["wipe_free_space"]
    assert RiskLevel.DESTRUCTIVE not in by_risk
    assert RiskLevel.REQUIRES_REBOOT not in by_risk


def test_m22_catalog_no_action_has_undo_command():
    # All five report actions are deliberately report-only (no companion
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
        "shortcut_hijack_report",
        "disk_space_by_folder_report",
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
    assert by_id["disk_space_by_folder_report"].inactivity_timeout_sec == 600
    default_timeout_ids = {
        "leftover_uninstall_keys_report",
        "broken_shortcuts_report",
        "shortcut_hijack_report",
    }
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


def test_m22_catalog_disk_space_by_folder_report_skips_reparse_points():
    # Same PS 5.1 -Recurse-follows-junctions issue as duplicate_files_report:
    # without excluding reparse points at the top level, a self-referential
    # or legacy compat junction can double-count size or blow up scan time.
    module = load_module(CATALOG_PATH)
    action = next(a for a in module.actions if a.id == "disk_space_by_folder_report")
    assert "ReparsePoint" in action.command


def test_m22_catalog_disk_space_by_folder_report_emits_progress_per_folder():
    # inactivity_timeout_sec: 600 is a watchdog that resets on output. Sizing
    # C:\Windows/Program Files/Users etc. can take well past 10 minutes with
    # no output, so each top-level folder must Write-Output as soon as it is
    # measured, not just in the final summary table.
    module = load_module(CATALOG_PATH)
    action = next(a for a in module.actions if a.id == "disk_space_by_folder_report")
    assert "Write-Output" in action.command
    # the per-folder progress line must run inside the sizing loop, i.e.
    # before the final sort/select-first-15 summary step
    progress_idx = action.command.index("Write-Output ($dir.FullName")
    summary_idx = action.command.index("Sort-Object SizeGB -Descending")
    assert progress_idx < summary_idx


def test_m22_catalog_shortcut_hijack_report_excludes_pwa_app_flags():
    # chrome.exe --app=https://... is Chrome/Edge's standard "Install as
    # app" flow (Gmail, Docs, etc.), not a hijack - it must not match.
    module = load_module(CATALOG_PATH)
    action = next(a for a in module.actions if a.id == "shortcut_hijack_report")
    assert "--app" in action.command
    assert "$args" not in action.command
    assert "$linkArgs" in action.command
