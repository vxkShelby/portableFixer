from pathlib import Path

from portablefix.models import ModuleCategory, RiskLevel
from portablefix.module_engine import load_module

CATALOG_PATH = Path(__file__).resolve().parent.parent / "Modules" / "m01_diagnostics" / "actions.yaml"


def test_m01_catalog_loads_18_actions_in_diagnostics_category():
    module = load_module(CATALOG_PATH)
    assert module.module_id == "m01_diagnostics"
    assert module.category == ModuleCategory.DIAGNOSTICS
    assert len(module.actions) == 18


def test_m01_catalog_all_actions_safe_readonly():
    module = load_module(CATALOG_PATH)
    for action in module.actions:
        assert action.risk == RiskLevel.SAFE
        assert action.undo_command is None


def test_m01_catalog_action_ids_are_unique():
    module = load_module(CATALOG_PATH)
    ids = [a.id for a in module.actions]
    assert len(ids) == len(set(ids))


def test_m01_catalog_covers_core_system_info_actions():
    module = load_module(CATALOG_PATH)
    ids = {a.id for a in module.actions}
    assert {
        "os_info", "computer_info", "bios_info", "cpu_info", "memory_info",
        "volumes", "physical_disks", "recent_hotfixes", "defender_status",
        "top_cpu_processes", "pending_reboot", "eventlog_critical_7d",
        "bsod_summary", "disk_reliability_counters", "installed_software",
        "boot_time_breakdown", "battery_health_report", "eventlog_full_export",
    }.issubset(ids)


def test_m01_catalog_diagnostic_exports_lock_down_their_output_folder():
    # battery_health_report and eventlog_full_export write potentially
    # sensitive data (event logs, battery telemetry) under a shared
    # ProgramData folder - they must restrict it to the current user,
    # SYSTEM, and Administrators so other local accounts can't read it.
    module = load_module(CATALOG_PATH)
    by_id = {a.id: a for a in module.actions}
    for action_id in ("battery_health_report", "eventlog_full_export"):
        command = by_id[action_id].command
        assert "icacls" in command, action_id
        assert "S-1-5-32-544" in command, action_id
        assert "S-1-5-18" in command, action_id
        assert "$env:USERDOMAIN" in command, action_id
        # The shared $env:ProgramData\PortableFix parent must also get
        # locked down the first time it's ever created, not just each
        # action's own per-run subfolder.
        assert "if (-not (Test-Path $root))" in command, action_id


def test_m01_catalog_eventlog_export_exit_code_reflects_actual_result():
    module = load_module(CATALOG_PATH)
    action = next(a for a in module.actions if a.id == "eventlog_full_export")
    assert "exit 1" in action.command
    assert "exit 0" in action.command


def test_m01_catalog_every_action_has_both_language_labels_and_descriptions():
    module = load_module(CATALOG_PATH)
    for action in module.actions:
        assert action.label_sk
        assert action.label_en
        assert action.description_sk
        assert action.description_en
