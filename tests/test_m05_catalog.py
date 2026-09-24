from pathlib import Path

from portablefix.models import ModuleCategory, RiskLevel
from portablefix.module_engine import load_module

CATALOG_PATH = Path(__file__).resolve().parent.parent / "Modules" / "m05_windows_update" / "actions.yaml"


def test_m05_catalog_loads_8_actions_in_repair_category():
    module = load_module(CATALOG_PATH)
    assert module.module_id == "m05_windows_update"
    assert module.category == ModuleCategory.REPAIR
    assert len(module.actions) == 8


def test_m05_catalog_risk_distribution():
    module = load_module(CATALOG_PATH)
    by_risk = {}
    for action in module.actions:
        by_risk.setdefault(action.risk, []).append(action.id)
    assert set(by_risk[RiskLevel.SAFE]) == {
        "wu_check_services",
        "wu_restart_services",
        "wu_trigger_detection",
        "wu_driver_updates_report",
    }
    assert set(by_risk[RiskLevel.MODERATE]) == {
        "wu_stop_services",
        "wu_reset_cache",
        "wu_reregister_dlls",
    }
    assert set(by_risk[RiskLevel.REQUIRES_REBOOT]) == {"wu_uninstall_last_update"}
    assert RiskLevel.DESTRUCTIVE not in by_risk


def test_m05_catalog_covers_expected_ids():
    module = load_module(CATALOG_PATH)
    ids = {a.id for a in module.actions}
    assert ids == {
        "wu_check_services",
        "wu_stop_services",
        "wu_reset_cache",
        "wu_restart_services",
        "wu_reregister_dlls",
        "wu_trigger_detection",
        "wu_driver_updates_report",
        "wu_uninstall_last_update",
    }


def test_m05_catalog_stop_services_before_reset_cache_before_restart_services():
    module = load_module(CATALOG_PATH)
    ids = [a.id for a in module.actions]
    assert ids.index("wu_stop_services") < ids.index("wu_reset_cache")
    assert ids.index("wu_reset_cache") < ids.index("wu_restart_services")


def test_m05_catalog_undo_commands_present_only_on_stop_services_and_reset_cache():
    module = load_module(CATALOG_PATH)
    by_id = {a.id: a for a in module.actions}
    assert by_id["wu_stop_services"].undo_command is not None
    assert by_id["wu_reset_cache"].undo_command is not None
    assert by_id["wu_check_services"].undo_command is None
    assert by_id["wu_restart_services"].undo_command is None
    assert by_id["wu_reregister_dlls"].undo_command is None
    assert by_id["wu_trigger_detection"].undo_command is None
    assert by_id["wu_driver_updates_report"].undo_command is None
    assert by_id["wu_uninstall_last_update"].undo_command is None


def test_m05_catalog_driver_updates_report_uses_wua_com_api_not_trigger_detection():
    module = load_module(CATALOG_PATH)
    action = next(a for a in module.actions if a.id == "wu_driver_updates_report")
    assert action.risk == RiskLevel.SAFE
    assert "Microsoft.Update.Session" in action.command
    assert "UsoClient" not in action.command
    # A live WUA search against Microsoft Update can take several minutes -
    # the default 300s inactivity timeout is too tight for a single silent
    # long-running COM call with no interim output.
    assert action.inactivity_timeout_sec == 600


def test_m05_catalog_uninstall_last_update_requires_reboot():
    module = load_module(CATALOG_PATH)
    action = next(a for a in module.actions if a.id == "wu_uninstall_last_update")
    assert action.risk == RiskLevel.REQUIRES_REBOOT
    assert action.undo_command is None
    assert "wusa.exe" in action.command


def test_m05_catalog_uninstall_last_update_uses_wua_history_not_gethotfix():
    # Get-HotFix's InstalledOn field is unreliable (frequently null on real
    # machines), so sorting by it doesn't reliably surface the actual most
    # recent update. The WUA COM API's update history is more consistent.
    module = load_module(CATALOG_PATH)
    action = next(a for a in module.actions if a.id == "wu_uninstall_last_update")
    assert "Get-HotFix" not in action.command
    assert "Microsoft.Update.Session" in action.command
    assert "QueryHistory" in action.command


def test_m05_reset_cache_in_use_warning_fails_and_points_at_stop_services():
    # A folder still locked by a running service used to print a WARNING and
    # exit 0 - the report/history showed success and an undo was recorded for
    # a reset that never happened. Name the real step that fixes it.
    module = load_module(CATALOG_PATH)
    by_id = {a.id: a for a in module.actions}
    command = by_id["wu_reset_cache"].command
    warning_branch = command[command.index("still in use"):]
    assert "exit 1" in warning_branch
    assert f'"{by_id["wu_stop_services"].label_en}"' in warning_branch


def test_m05_reset_cache_removes_stale_bak_before_rename_so_a_rerun_works():
    # Rename-Item fails when <folder>.bak is left over from an earlier reset,
    # so a second run on the same machine could never succeed.
    module = load_module(CATALOG_PATH)
    command = next(a for a in module.actions if a.id == "wu_reset_cache").command
    for folder in ("SoftwareDistribution", "System32\\catroot2"):
        bak_removal = f'Remove-Item -Path "$env:WINDIR\\{folder}.bak"'
        rename = f'Rename-Item -Path "$env:WINDIR\\{folder}"'
        assert bak_removal in command, folder
        assert command.index(bak_removal) < command.index(rename), folder


def test_m05_reregister_dlls_checks_each_regsvr32_exit_code():
    # regsvr32 is a GUI-subsystem exe: a bare call returns before it finishes
    # and its exit code was never looked at, so the action always "worked".
    # Start-Process -Wait -PassThru gives a real ExitCode per dll; dlls not
    # present on this Windows build are skipped, not counted as failures.
    module = load_module(CATALOG_PATH)
    command = next(a for a in module.actions if a.id == "wu_reregister_dlls").command
    for token in ("Start-Process", "-Wait", "-PassThru", "Test-Path", "$r.ExitCode"):
        assert token in command, token
    assert "regsvr32.exe /s" not in command
    assert command.index("Test-Path") < command.index("Start-Process")
    assert "exit 1" in command[command.rindex("if ($failed.Count -gt 0)"):]
