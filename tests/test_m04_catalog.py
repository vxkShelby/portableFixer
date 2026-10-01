from pathlib import Path

from portablefix.models import ModuleCategory, RiskLevel
from portablefix.module_engine import load_module

CATALOG_PATH = Path(__file__).resolve().parent.parent / "Modules" / "m04_integrity" / "actions.yaml"


def test_m04_catalog_loads_14_actions_in_repair_category():
    module = load_module(CATALOG_PATH)
    assert module.module_id == "m04_integrity"
    assert module.category == ModuleCategory.REPAIR
    assert len(module.actions) == 14


def test_m04_catalog_risk_distribution():
    module = load_module(CATALOG_PATH)
    by_risk = {}
    for action in module.actions:
        by_risk.setdefault(action.risk, []).append(action.id)
    assert len(by_risk[RiskLevel.SAFE]) == 7
    assert len(by_risk[RiskLevel.MODERATE]) == 5
    assert len(by_risk[RiskLevel.DESTRUCTIVE]) == 1
    assert len(by_risk[RiskLevel.REQUIRES_REBOOT]) == 1


def test_m04_catalog_covers_expected_ids():
    module = load_module(CATALOG_PATH)
    ids = {a.id for a in module.actions}
    assert ids == {
        "dism_checkhealth",
        "dism_scanhealth",
        "dism_restorehealth",
        "sfc_scannow",
        "sfc_verifyonly",
        "appx_reregister",
        "wmi_verify",
        "wmi_backup",
        "wmi_salvage",
        "search_index_rebuild",
        "store_cache_reset",
        "perf_counters_rebuild",
        "profile_list_report",
        "secpol_export_snapshot",
    }


def test_m04_catalog_new_moderate_repair_actions_have_no_undo():
    # None of these have a sensible undo (rebuild/cache-clear operations
    # with no better "previous state" to roll back to) - matches the
    # existing no-undo precedent of dism_scanhealth/disk_optimize_volume.
    module = load_module(CATALOG_PATH)
    by_id = {a.id: a for a in module.actions}
    for action_id in ("search_index_rebuild", "store_cache_reset", "perf_counters_rebuild"):
        assert by_id[action_id].risk == RiskLevel.MODERATE, action_id
        assert by_id[action_id].undo_command is None, action_id


def test_m04_catalog_wmi_salvage_undo_restores_from_wmi_backup_action():
    module = load_module(CATALOG_PATH)
    by_id = {a.id: a for a in module.actions}
    assert by_id["wmi_backup"].undo_command is None
    assert by_id["wmi_salvage"].undo_command is not None
    assert "wmi_backup.bin" in by_id["wmi_backup"].command
    assert "wmi_backup.bin" in by_id["wmi_salvage"].undo_command


def test_m04_catalog_dism_restorehealth_before_sfc_scannow():
    module = load_module(CATALOG_PATH)
    ids = [a.id for a in module.actions]
    assert ids.index("dism_restorehealth") < ids.index("sfc_scannow")
    assert ids.index("sfc_scannow") < ids.index("sfc_verifyonly")


def test_m04_catalog_no_preview_command_set():
    module = load_module(CATALOG_PATH)
    for action in module.actions:
        assert action.preview_command is None


def test_m04_catalog_profile_list_report_is_read_only_and_flags_temp_profile_causes():
    # Deliberately report-only: picking the wrong SID to "repair" on a
    # shared PC breaks another user's profile (research-repair-additions.md).
    module = load_module(CATALOG_PATH)
    by_id = {a.id: a for a in module.actions}
    action = by_id["profile_list_report"]
    assert action.risk == RiskLevel.SAFE
    assert action.undo_command is None
    command = action.command
    assert "ProfileList" in command
    for flag in ("BAK DUPLICATE", "TEMP PROFILE", "FOLDER MISSING"):
        assert flag in command, flag
    for verb in ("Set-ItemProperty", "Remove-Item", "Rename-Item", "New-ItemProperty"):
        assert verb not in command, verb


def test_m04_catalog_secpol_export_snapshot_only_writes_a_new_backup_file():
    # Backup-only stand-in for the rejected group-policy reset
    # (research-repair-additions.md): exports, never applies a policy.
    module = load_module(CATALOG_PATH)
    action = next(a for a in module.actions if a.id == "secpol_export_snapshot")
    assert action.risk == RiskLevel.SAFE
    assert action.undo_command is None and action.check_command is None
    command = action.command
    assert "secedit /export /cfg $bk /quiet" in command
    assert "$env:ProgramData\\PortableFix" in command
    # A timestamped name, so a second run never overwrites the pre-change snapshot.
    assert "Get-Date -Format 'yyyyMMdd_HHmmss'" in command
    for verb in ("/configure", "/import", "gpupdate", "Remove-Item"):
        assert verb not in command, verb
    # No file written = failure, not a silent "success".
    missing = command.index("if (-not (Test-Path -LiteralPath $bk))")
    assert "exit 1" in command[missing : command.index("}", missing)]
