import os
import shutil
import subprocess
from pathlib import Path

import pytest

from portablefix.models import ModuleCategory, RiskLevel
from portablefix.module_engine import load_module

CATALOG_PATH = Path(__file__).resolve().parent.parent / "Modules" / "m04_integrity" / "actions.yaml"
STUB_GUARD_EXIT = 97


def _action(action_id):
    return next(a for a in load_module(CATALOG_PATH).actions if a.id == action_id)


def _powershell_or_skip() -> str:
    exe = os.environ.get("PORTABLEFIX_TEST_PWSH") or shutil.which("powershell") or shutil.which("pwsh")
    if not exe:
        pytest.skip("no PowerShell available")
    return exe


def _ps_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _run_ps(tmp_path: Path, stubs: list, names: list, command: str, env: dict | None = None):
    """Runs `command` with every listed cmdlet shadowed by a stub (exit 97
    unless each name resolves to a function) and env variables set inside
    the script; returns (result, logged calls)."""
    log = tmp_path / "calls.log"
    log.write_text("", encoding="utf-8")
    guard = (
        "foreach ($n in " + ", ".join(_ps_quote(n) for n in names) + ") { "
        "if ((Get-Command $n -EA SilentlyContinue | Select-Object -First 1).CommandType -ne 'Function') "
        f"{{ exit {STUB_GUARD_EXIT} }} }}"
    )
    prelude = ["[Console]::OutputEncoding=[Text.Encoding]::UTF8", f"$global:PfLog = {_ps_quote(str(log))}"]
    prelude += [f"$env:{k} = {_ps_quote(str(v))}" for k, v in (env or {}).items()]
    result = subprocess.run(
        [_powershell_or_skip(), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command",
         "; ".join(prelude + stubs + [guard, command])],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert result.returncode != STUB_GUARD_EXIT, "a system tool was not stubbed - refusing to run the real one"
    return result, log.read_text(encoding="utf-8-sig").splitlines()


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


def test_appx_reregister_targets_the_signed_in_user_and_never_registers_other_users_packages():
    # Get-AppxPackage -AllUsers + Add-AppxPackage -Register put every user's
    # packages into the running account (the technician's, over the
    # shoulder). PS 5.1's Add-AppxPackage has no -User, so the listing is
    # the target user's and a different signed-in user is refused.
    action = _action("appx_reregister")
    command = action.command
    assert "-AllUsers" not in command
    assert "Get-AppxPackage -User $sid" in command
    assert "$__pfUserSid" in command
    assert "-not $_.IsFramework -and -not $_.IsResourcePackage" in command
    refusal = command.index("$__pfUserSid -ne $me")
    assert "exit 1" in command[refusal : command.index("}", refusal)]
    assert command.index("exit 1") < command.index("Add-AppxPackage -DisableDevelopmentMode")
    assert "signed-in user" in action.description_en and "prihláseného používateľa" in action.description_sk
    assert "all users" not in action.description_en


SEARCH_FILES = ("Windows.edb", "Windows.db", "Windows-gather.db", "Windows-usn.db")


def _search_stubs(set_fails=False):
    fail = "throw [System.UnauthorizedAccessException]::new('Prístup odmietnutý.')" if set_fails else ""
    return [
        "$global:PfSvc = 'Running'",
        "function Get-Service { [CmdletBinding()] param([string] $Name) "
        "$o = [pscustomobject]@{ Name = $Name; Status = $global:PfSvc }; "
        "$o | Add-Member -MemberType ScriptMethod -Name Refresh -Value { $this.Status = $global:PfSvc } -PassThru }",
        "function Stop-Service { [CmdletBinding()] param([string] $Name, [switch] $Force) "
        "Add-Content -Path $global:PfLog -Value ('stop ' + $Name); $global:PfSvc = 'Stopped' }",
        "function Start-Service { [CmdletBinding()] param([string] $Name) "
        "Add-Content -Path $global:PfLog -Value ('start ' + $Name); $global:PfSvc = 'Running' }",
        "function Set-ItemProperty { [CmdletBinding()] param([string] $Path, [string] $Name, $Value, [string] $Type) "
        f"Add-Content -Path $global:PfLog -Value ('set ' + $Path + ' ' + $Name + ' ' + $Value); {fail} }}",
    ]


SEARCH_NAMES = ["Get-Service", "Stop-Service", "Start-Service", "Set-ItemProperty"]


def _search_tree(tmp_path, *files):
    program_data = tmp_path / "ProgramData"
    folder = program_data / "Microsoft" / "Search" / "Data" / "Applications" / "Windows"
    folder.mkdir(parents=True)
    for name in files:
        (folder / name).write_bytes(b"\0" * 16)
    (folder / "GatherLogs").mkdir()  # not an index file - stays
    return program_data, folder


def test_search_index_rebuild_removes_the_windows_11_index_and_marks_the_rebuild(tmp_path):
    # Windows 11 keeps the index in Windows.db; deleting only the legacy
    # Windows.edb removed nothing while printing success.
    program_data, folder = _search_tree(tmp_path, "Windows.db", "Windows-gather.db")
    result, calls = _run_ps(tmp_path, _search_stubs(), SEARCH_NAMES, _action("search_index_rebuild").command,
                            {"ProgramData": program_data})
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Removed 2 index file(s): Windows.db, Windows-gather.db" in result.stdout
    assert sorted(p.name for p in folder.iterdir()) == ["GatherLogs"]
    assert calls == ["stop WSearch", "set HKLM:\\SOFTWARE\\Microsoft\\Windows Search SetupCompletedSuccessfully 0",
                     "start WSearch"]


def test_search_index_rebuild_fails_when_no_index_file_exists(tmp_path):
    program_data, _ = _search_tree(tmp_path)
    result, calls = _run_ps(tmp_path, _search_stubs(), SEARCH_NAMES, _action("search_index_rebuild").command,
                            {"ProgramData": program_data})
    assert result.returncode == 1, result.stdout + result.stderr
    assert "No index database found" in result.stdout
    assert calls[-1] == "start WSearch"  # the service is never left stopped


def test_search_index_rebuild_restarts_the_service_when_the_registry_write_fails(tmp_path):
    program_data, _ = _search_tree(tmp_path, "Windows.edb")
    result, calls = _run_ps(tmp_path, _search_stubs(set_fails=True), SEARCH_NAMES,
                            _action("search_index_rebuild").command, {"ProgramData": program_data})
    assert result.returncode == 1, result.stdout + result.stderr
    assert "needs administrator" in result.stdout
    assert calls[-1] == "start WSearch"


def test_search_index_rebuild_names_every_index_file():
    command = _action("search_index_rebuild").command
    for name in SEARCH_FILES:
        assert f"'{name}'" in command, name


