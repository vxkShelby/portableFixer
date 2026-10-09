import os
import shutil
import subprocess
from pathlib import Path

import pytest

from portablefix.models import ModuleCategory, RiskLevel
from portablefix.module_engine import load_module

CATALOG_PATH = Path(__file__).resolve().parent.parent / "Modules" / "m03_disk" / "actions.yaml"


def test_m03_catalog_loads_9_actions_in_repair_category():
    module = load_module(CATALOG_PATH)
    assert module.module_id == "m03_disk"
    assert module.category == ModuleCategory.REPAIR
    assert len(module.actions) == 9


def test_m03_catalog_risk_distribution():
    module = load_module(CATALOG_PATH)
    by_risk = {}
    for action in module.actions:
        by_risk.setdefault(action.risk, []).append(action.id)
    assert len(by_risk[RiskLevel.SAFE]) == 6
    assert len(by_risk[RiskLevel.MODERATE]) == 2
    assert len(by_risk[RiskLevel.REQUIRES_REBOOT]) == 1
    assert RiskLevel.DESTRUCTIVE not in by_risk


def test_m03_catalog_scan_before_spotfix_before_full_scan_reboot():
    module = load_module(CATALOG_PATH)
    ids = [a.id for a in module.actions]
    assert ids.index("disk_scan_readonly") < ids.index("disk_spotfix")
    assert ids.index("disk_spotfix") < ids.index("disk_full_scan_reboot")
    assert ids.index("disk_full_scan_reboot") < ids.index("disk_check_scheduled")


def test_m03_catalog_no_action_has_undo_command():
    module = load_module(CATALOG_PATH)
    for action in module.actions:
        assert action.undo_command is None


def test_m03_catalog_disk_stressing_actions_are_flagged():
    # G13: the pre-flight asks the disk health probe for exactly these. A
    # chkdsk /r reads every sector, a defrag rewrites much of an HDD and an
    # online NTFS repair writes to a file system that may sit on bad
    # sectors. The read-only Repair-Volume -Scan and the diagnostics are not
    # flagged: they are what a technician runs to decide.
    module = load_module(CATALOG_PATH)
    flagged = {a.id for a in module.actions if a.stresses_disk}
    assert flagged == {"disk_spotfix", "disk_optimize_volume", "disk_full_scan_reboot"}
    for action in module.actions:
        if action.stresses_disk:
            assert action.risk != RiskLevel.SAFE, action.id


def test_m03_catalog_disk_health_verdict_is_safe_and_before_the_repairs():
    module = load_module(CATALOG_PATH)
    ids = [a.id for a in module.actions]
    verdict = next(a for a in module.actions if a.id == "disk_health_verdict")
    assert verdict.risk == RiskLevel.SAFE
    assert verdict.changes_system is None and not verdict.stresses_disk
    assert ids.index("disk_health_verdict") < ids.index("disk_spotfix")
    assert verdict.description_sk and verdict.description_en


# --- G03: the full check at restart reports success only when scheduled ---
#
# Success stops the batch for a restart (restart_before_next), so exit 0
# must mean the boot check really is queued. The command runs against
# stubs: chkdsk, Get-CimInstance, Get-ItemProperty and Set-ItemProperty are
# shadowed by functions, and the script exits 97 unless each name resolves
# to its stub, so a test run never touches the host's disk or registry.
# chkdsk is the Slovak one: it only schedules when answered 'A' - a command
# that pipes 'Y' into it schedules nothing. The BootExecute value lives in
# $global:PfBoot, shared by the registry stubs and the chkdsk stub.

STUB_GUARD_EXIT = 97
DEFAULT_BOOT = "autocheck autochk *"
C_ENTRY = "autocheck autochk /r \\??\\C:"


def _powershell_or_skip() -> str:
    exe = os.environ.get("PORTABLEFIX_TEST_PWSH") or shutil.which("powershell") or shutil.which("pwsh")
    if not exe:
        pytest.skip("no PowerShell available")
    return exe


def _ps_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _run_full_scan(tmp_path, chkdsk_exit=0, dirty=False, boot=(DEFAULT_BOOT,), volume=True, write_fails=False,
                   write_lands=True):
    """Returns (result, calls, final BootExecute list). write_fails: the
    registry write throws (not elevated); write_lands: a successful write
    really changes the value."""
    command = next(a for a in load_module(CATALOG_PATH).actions if a.id == "disk_full_scan_reboot").command
    log = tmp_path / "calls.log"
    log.write_text("", encoding="utf-8")
    lg = _ps_quote(str(log))
    names = ["chkdsk", "Get-CimInstance", "Get-ItemProperty", "Set-ItemProperty"]
    boot_value = "@(" + ", ".join(_ps_quote(v) for v in boot) + ")"
    volume_body = (
        f"[pscustomobject]@{{ DriveLetter = 'C:'; DirtyBitSet = ${str(dirty).lower()} }}" if volume else "$null"
    )
    fail = "throw [System.UnauthorizedAccessException]::new('Prístup odmietnutý.')" if write_fails else ""
    land = "$global:PfBoot = @($Value)" if write_lands else ""
    stubs = [
        f"$global:PfBoot = {boot_value}",
        # Slovak chkdsk: schedules only when answered 'A'.
        "function chkdsk { $answer = @($input) -join ''; "
        f"Add-Content -Path {lg} -Value ('chkdsk ' + ($args -join ' ') + ' <- ' + $answer); "
        "Write-Output 'Chcete naplánovať kontrolu zväzku? (A/N)'; "
        f"if ($answer -eq 'A') {{ $global:PfBoot = @($global:PfBoot) + {_ps_quote(C_ENTRY)} }}; "
        f"$global:LASTEXITCODE = {chkdsk_exit} }}",
        "function Get-CimInstance { [CmdletBinding()] param([string] $ClassName, [string] $Filter) "
        f"if ($ClassName -ne 'Win32_Volume' -or $Filter -ne \"DriveLetter='C:'\") {{ exit 98 }}; {volume_body} }}",
        "function Get-ItemProperty { [CmdletBinding()] param([string] $LiteralPath, [string] $Name) "
        "[pscustomobject]@{ BootExecute = $global:PfBoot } }",
        "function Set-ItemProperty { [CmdletBinding()] param([string] $LiteralPath, [string] $Name, $Value, [string] $Type) "
        f"Add-Content -Path {lg} -Value ('set ' + $LiteralPath + ' ' + $Name + ' ' + $Type + ' = ' + ($Value -join ' | ')); "
        f"{fail}; {land} }}",
        f"function Write-PfBoot {{ Set-Content -LiteralPath {_ps_quote(str(tmp_path / 'boot.json'))} "
        "-Value (ConvertTo-Json -InputObject @($global:PfBoot)) }",
    ]
    guard = (
        "foreach ($n in " + ", ".join(_ps_quote(n) for n in names) + ") { "
        "if ((Get-Command $n -EA SilentlyContinue | Select-Object -First 1).CommandType -ne 'Function') "
        f"{{ exit {STUB_GUARD_EXIT} }} }}"
    )
    # Set inside the script, not in the child's environment (see
    # test_m15_catalog._run_ps). The command may exit early, so the final
    # BootExecute is written by a trap-free wrapper: try/finally runs on exit.
    script = "; ".join(
        ["[Console]::OutputEncoding=[Text.Encoding]::UTF8", "$env:SystemDrive = 'C:'"] + stubs
        + [guard, "try { " + command + " } finally { Write-PfBoot }"]
    )
    result = subprocess.run(
        [_powershell_or_skip(), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script],
        env=dict(os.environ), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert result.returncode != STUB_GUARD_EXIT, "a system tool was not stubbed - refusing to run the real one"
    import json

    final = json.loads((tmp_path / "boot.json").read_text(encoding="utf-8-sig"))
    return result, log.read_text(encoding="utf-8-sig").splitlines(), final


def test_full_scan_reboot_is_one_line_and_restart_before_next():
    action = next(a for a in load_module(CATALOG_PATH).actions if a.id == "disk_full_scan_reboot")
    assert action.restart_before_next
    assert "\n" not in action.command
    # The old "is it dirty" check parsed fsutil's localized sentence; the
    # old scheduling answered chkdsk's localized prompt.
    assert "fsutil" not in action.command
    assert "chkdsk" not in action.command
    assert "-Type MultiString" in action.command


def test_full_scan_reboot_schedules_through_bootexecute_without_the_localized_prompt(tmp_path):
    # Piping 'Y' into the Slovak chkdsk ("A/N") never scheduled anything; the
    # BootExecute entry chkdsk would have written is written directly.
    result, calls, final = _run_full_scan(tmp_path, chkdsk_exit=3, dirty=False)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "scheduled for the next restart" in result.stdout
    assert not [c for c in calls if c.startswith("chkdsk")]
    assert final == [DEFAULT_BOOT, C_ENTRY]


def test_full_scan_reboot_fails_when_the_write_is_refused(tmp_path):
    result, _, final = _run_full_scan(tmp_path, write_fails=True)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "needs administrator" in result.stdout
    assert final == [DEFAULT_BOOT]


def test_full_scan_reboot_fails_when_nothing_was_scheduled(tmp_path):
    result, _, _ = _run_full_scan(tmp_path, write_lands=False, dirty=False)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "NOT scheduled" in result.stdout


def test_full_scan_reboot_fails_without_the_volume_or_boot_value(tmp_path):
    result, _, _ = _run_full_scan(tmp_path, volume=False, boot=(), write_lands=False)
    assert result.returncode == 1, result.stdout + result.stderr


def test_full_scan_reboot_succeeds_on_the_dirty_bit(tmp_path):
    result, _, _ = _run_full_scan(tmp_path, dirty=True, write_lands=False)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "scheduled for the next restart" in result.stdout


def test_full_scan_reboot_does_not_append_an_entry_that_is_already_there(tmp_path):
    result, calls, final = _run_full_scan(tmp_path, boot=(DEFAULT_BOOT, "autocheck autochk /r \\??\\c:"))
    assert result.returncode == 0, result.stdout + result.stderr
    assert not [c for c in calls if c.startswith("set ")]
    assert final == [DEFAULT_BOOT, "autocheck autochk /r \\??\\c:"]


def test_full_scan_reboot_keeps_another_drives_bootexecute_entry(tmp_path):
    d_entry = "autocheck autochk /r \\??\\D:"
    result, _, final = _run_full_scan(tmp_path, boot=(DEFAULT_BOOT, d_entry))
    assert result.returncode == 0, result.stdout + result.stderr
    assert final == [DEFAULT_BOOT, d_entry, C_ENTRY]


def test_full_scan_reboot_ignores_another_drives_bootexecute_entry(tmp_path):
    result, _, _ = _run_full_scan(tmp_path, boot=(DEFAULT_BOOT, "autocheck autochk /r \\??\\D:"), write_lands=False)
    assert result.returncode == 1, result.stdout + result.stderr


def test_full_scan_reboot_writes_an_entry_even_when_bootexecute_is_empty(tmp_path):
    result, _, final = _run_full_scan(tmp_path, boot=())
    assert result.returncode == 0, result.stdout + result.stderr
    assert final == [C_ENTRY]
