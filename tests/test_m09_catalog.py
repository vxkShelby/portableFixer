import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from portablefix.models import ModuleCategory, RiskLevel
from portablefix.module_engine import load_module

CATALOG_PATH = Path(__file__).resolve().parent.parent / "Modules" / "m09_tuning" / "actions.yaml"


def test_m09_catalog_loads_20_actions_in_repair_category():
    module = load_module(CATALOG_PATH)
    assert module.module_id == "m09_tuning"
    assert module.category == ModuleCategory.REPAIR
    assert len(module.actions) == 20


def test_m09_catalog_risk_distribution():
    module = load_module(CATALOG_PATH)
    by_risk = {}
    for action in module.actions:
        by_risk.setdefault(action.risk, []).append(action.id)
    assert len(by_risk[RiskLevel.SAFE]) == 6
    assert len(by_risk[RiskLevel.MODERATE]) == 13
    assert len(by_risk[RiskLevel.REQUIRES_REBOOT]) == 1
    assert RiskLevel.DESTRUCTIVE not in by_risk


def test_m09_catalog_covers_expected_ids():
    module = load_module(CATALOG_PATH)
    ids = {a.id for a in module.actions}
    assert ids == {
        "tune_power_plan_report",
        "tune_power_high_performance",
        "tune_ultimate_performance_power_plan",
        "tune_gpu_hardware_scheduling",
        "tune_foreground_priority",
        "tune_disable_game_dvr",
        "tune_startup_apps_report",
        "tune_visual_effects_performance",
        "tune_end_task_taskbar",
        "tune_sticky_keys_disable",
        "tune_classic_context_menu",
        "tune_pause_background_services",
        "tune_bluetooth_service_restart",
        "tune_audio_service_restart",
        "tune_camera_service_restart",
        "tune_memory_usage_report",
        "tune_clear_working_sets",
        "tune_path_sanity_report",
        "storage_sense_report",
        "storage_sense_enable",
    }


def test_m09_catalog_memory_actions_are_safe_and_undoless():
    # Both are non-destructive: the report only reads state, and the
    # working-set trim is a transient OS-level hint (processes reclaim
    # memory as needed) - neither persists anything worth undoing.
    module = load_module(CATALOG_PATH)
    by_id = {a.id: a for a in module.actions}
    for action_id in ("tune_memory_usage_report", "tune_clear_working_sets"):
        assert by_id[action_id].risk == RiskLevel.SAFE, action_id
        assert by_id[action_id].undo_command is None, action_id


def test_m09_catalog_new_service_restart_actions_have_no_undo_and_verify_success():
    # Stop-Service/Start-Service silently no-op without administrator - each
    # new service-restart action must check actual post-restart status
    # rather than claim success on a no-op (same pattern already required
    # of tune_pause_background_services). None have a meaningful undo -
    # "restart the service again" is its own undo.
    module = load_module(CATALOG_PATH)
    by_id = {a.id: a for a in module.actions}
    for action_id in (
        "tune_bluetooth_service_restart",
        "tune_audio_service_restart",
        "tune_camera_service_restart",
    ):
        action = by_id[action_id]
        assert action.risk == RiskLevel.MODERATE, action_id
        assert action.undo_command is None, action_id
        assert "exit 1" in action.command, action_id


def test_m09_catalog_undo_commands_on_all_moderate_and_reboot_actions():
    module = load_module(CATALOG_PATH)
    by_id = {a.id: a for a in module.actions}
    for undoable in (
        "tune_power_high_performance",
        "tune_ultimate_performance_power_plan",
        "tune_gpu_hardware_scheduling",
        "tune_foreground_priority",
        "tune_disable_game_dvr",
        "tune_visual_effects_performance",
        "tune_end_task_taskbar",
        "tune_sticky_keys_disable",
        "tune_classic_context_menu",
        "tune_pause_background_services",
        "storage_sense_enable",
    ):
        assert by_id[undoable].undo_command is not None, undoable
    assert by_id["tune_power_plan_report"].undo_command is None
    assert by_id["tune_startup_apps_report"].undo_command is None


def test_m09_catalog_new_tweaks_refresh_backup_on_every_run():
    # These three follow the "always overwrite" backup pattern (not the
    # older "only on first run" one that lost data between two runs) -
    # confirm none of them guard the backup write with a Test-Path check.
    module = load_module(CATALOG_PATH)
    by_id = {a.id: a for a in module.actions}
    for action_id in ("tune_gpu_hardware_scheduling", "tune_foreground_priority", "tune_disable_game_dvr"):
        command = by_id[action_id].command
        assert "if (-not (Test-Path $bk))" not in command, action_id


def test_m09_catalog_power_undo_restores_balanced_plan():
    module = load_module(CATALOG_PATH)
    by_id = {a.id: a for a in module.actions}
    assert "381b4222-f694-41f0-9685-ff5bb260df2e" in by_id["tune_power_high_performance"].undo_command


def test_m09_catalog_pause_services_verifies_the_stop_actually_worked():
    # Stop-Service/Start-Service on WSearch/SysMain silently no-op without
    # administrator - the command must check the service's actual status
    # afterward and fail rather than claim success on a no-op.
    module = load_module(CATALOG_PATH)
    action = next(a for a in module.actions if a.id == "tune_pause_background_services")
    assert action.risk == RiskLevel.MODERATE
    assert "$svc.Refresh()" in action.command
    assert "-eq 'Stopped'" in action.command
    assert "exit 1" in action.command
    assert "$svc.Refresh()" in action.undo_command
    assert "exit 1" in action.undo_command


def test_m09_catalog_path_sanity_report_is_read_only():
    module = load_module(CATALOG_PATH)
    by_id = {a.id: a for a in module.actions}
    action = by_id["tune_path_sanity_report"]
    assert action.risk == RiskLevel.SAFE
    assert action.undo_command is None
    command = action.command
    assert "GetEnvironmentVariable('Path','Machine')" in command
    assert "GetEnvironmentVariable('Path','User')" in command
    assert "2047" in command
    assert "MISSING: " in command and "DUPLICATE: " in command
    assert "SetEnvironmentVariable" not in command


def test_m09_catalog_ultimate_plan_is_found_by_saved_guid_not_localized_name():
    # powercfg prints plan names in the Windows display language, so matching
    # 'Ultimate Performance' in "powercfg /list" never matched on e.g. Slovak
    # Windows and every run duplicated the plan again. The GUID of the plan
    # this action creates is remembered instead and reused while
    # "powercfg /query <guid>" still finds it.
    module = load_module(CATALOG_PATH)
    action = next(a for a in module.actions if a.id == "tune_ultimate_performance_power_plan")
    command = action.command
    assert "ultimate_plan_guid.txt" in command
    assert "Select-String 'Ultimate Performance'" not in command
    assert "powercfg /list" not in command
    assert "powercfg /query $saved" in command
    assert "-duplicatescheme e9a42b02-d5df-448d-aa00-03f14749eb61" in command
    # The template GUID itself must never be taken for the new plan's GUID.
    assert "Where-Object { $_ -ne 'e9a42b02-d5df-448d-aa00-03f14749eb61' }" in command
    assert "New-Item -ItemType Directory -Force -Path (Split-Path $gf)" in command
    assert command.index("powercfg /query") < command.index("-duplicatescheme") < command.index("powercfg /setactive")
    # Undo still just returns to Balanced; it leaves the saved plan in place
    # so a later run reuses it instead of creating yet another copy.
    assert action.undo_command == "powercfg /setactive 381b4222-f694-41f0-9685-ff5bb260df2e"


# --- G31 Storage Sense ---------------------------------------------------------
#
# storage_sense_report / storage_sense_enable (and its undo and preview) run
# for real in PowerShell against an in-memory registry: every registry
# cmdlet they use is a stub function (functions win over cmdlets in command
# lookup) that loads and saves the fake hive from a JSON file, so an enable
# and the following undo see the same state. File system paths go through to
# the real cmdlets - the backup lands in a temporary %ProgramData%. The
# script refuses to run (exit 97) unless every stubbed name resolves to the
# stub, so a test can never touch the host registry.

SS_KEY = "HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\StorageSense\\Parameters\\StoragePolicy"
SS_POLICY = "HKLM:\\SOFTWARE\\Policies\\Microsoft\\Windows\\StorageSense"
TECH = ("PC\\technik", "S-1-5-21-1000-2000-3000-1001")
STUB_GUARD_EXIT = 97
WANT = {"01": ("DWord", 1), "2048": ("DWord", 30), "04": ("DWord", 1), "08": ("DWord", 1),
        "256": ("DWord", 30), "32": ("DWord", 0)}

SS_STUBS = r"""
function Load-PfReg { $global:PFR = @{}; $j = Microsoft.PowerShell.Management\Get-Content -Raw -LiteralPath $env:PF_REGFILE | ConvertFrom-Json; foreach ($k in $j.PSObject.Properties) { $vals = [ordered]@{}; foreach ($v in $k.Value.values.PSObject.Properties) { $vals[$v.Name] = @([string]$v.Value.kind, $v.Value.value) }; $global:PFR[$k.Name] = @{ values = $vals; sub = [int]$k.Value.sub } } }
function Save-PfReg { $o = [ordered]@{}; foreach ($k in $global:PFR.Keys) { $vals = [ordered]@{}; foreach ($n in $global:PFR[$k].values.Keys) { $vals[$n] = @{ kind = $global:PFR[$k].values[$n][0]; value = $global:PFR[$k].values[$n][1] } }; $o[$k] = @{ values = $vals; sub = $global:PFR[$k].sub } }; ConvertTo-Json -InputObject $o -Depth 6 | Microsoft.PowerShell.Management\Set-Content -LiteralPath $env:PF_REGFILE -Encoding UTF8 }
function Typed-PfVal($kind, $val) { switch ($kind) { 'DWord' { [int]$val } 'QWord' { [int64]$val } 'Binary' { ,([byte[]]@($val)) } 'MultiString' { ,([string[]]@($val)) } default { $val } } }
function Log-Pf([string]$m) { Microsoft.PowerShell.Management\Add-Content -LiteralPath $env:PF_LOGFILE -Value $m }
function Test-Path { [CmdletBinding()] param([Parameter(Position = 0)] [string] $Path, [string] $LiteralPath) $p = $(if ($LiteralPath) { $LiteralPath } else { $Path }); if ($p -like 'HK*:*') { Load-PfReg; $global:PFR.ContainsKey($p) } else { Microsoft.PowerShell.Management\Test-Path -LiteralPath $p } }
function Get-Item { [CmdletBinding()] param([string] $LiteralPath) Load-PfReg; if (-not $global:PFR.ContainsKey($LiteralPath)) { throw [System.Management.Automation.ItemNotFoundException]::new('Nenájdené.') }; $o = [pscustomobject]@{ KeyPath = $LiteralPath; SubKeyCount = $global:PFR[$LiteralPath].sub }; $o | Add-Member ScriptMethod GetValueNames { Load-PfReg; ,([string[]]@($global:PFR[$this.KeyPath].values.Keys)) }; $o | Add-Member ScriptMethod GetValue { param($n, $d, $opt) Load-PfReg; $e = $global:PFR[$this.KeyPath].values[$n]; if ($null -eq $e) { return $d }; Typed-PfVal $e[0] $e[1] }; $o | Add-Member ScriptMethod GetValueKind { param($n) Load-PfReg; $global:PFR[$this.KeyPath].values[$n][0] }; $o }
function Get-ItemProperty { [CmdletBinding()] param([string] $LiteralPath) Load-PfReg; if (-not $global:PFR.ContainsKey($LiteralPath)) { return }; $h = [ordered]@{}; foreach ($n in $global:PFR[$LiteralPath].values.Keys) { $h[$n] = Typed-PfVal $global:PFR[$LiteralPath].values[$n][0] $global:PFR[$LiteralPath].values[$n][1] }; [pscustomobject]$h }
function New-Item { [CmdletBinding()] param([Parameter(Position = 0)] [string] $Path, [string] $ItemType, [switch] $Force) if ($Path -like 'HK*:*') { Load-PfReg; Log-Pf ('New-Item ' + $Path); if (-not $global:PFR.ContainsKey($Path)) { $global:PFR[$Path] = @{ values = [ordered]@{}; sub = 0 } }; Save-PfReg; [pscustomobject]@{ Path = $Path } } else { Microsoft.PowerShell.Management\New-Item -Path $Path -ItemType $ItemType -Force:$Force } }
function New-ItemProperty { [CmdletBinding()] param([string] $LiteralPath, [string] $Name, $Value, [string] $PropertyType, [switch] $Force) Load-PfReg; Log-Pf ('New-ItemProperty ' + $Name + ' ' + $PropertyType); if ($env:PF_FAIL_NAME -eq $Name) { throw [System.UnauthorizedAccessException]::new('Prístup odmietnutý.') }; if ($env:PF_IGNORE_NAME -eq $Name) { return }; if (-not $global:PFR.ContainsKey($LiteralPath)) { throw [System.Management.Automation.ItemNotFoundException]::new('Nenájdené.') }; $v = $Value; if ($v -is [array]) { $v = @($v | ForEach-Object { $_ }) }; $global:PFR[$LiteralPath].values[$Name] = @($PropertyType, $v); Save-PfReg; [pscustomobject]@{ Name = $Name } }
function Remove-ItemProperty { [CmdletBinding()] param([string] $LiteralPath, [string] $Name) Load-PfReg; Log-Pf ('Remove-ItemProperty ' + $Name); $global:PFR[$LiteralPath].values.Remove($Name); Save-PfReg }
function Remove-Item { [CmdletBinding()] param([string] $LiteralPath, [switch] $Force) if ($LiteralPath -like 'HK*:*') { Load-PfReg; Log-Pf ('Remove-Item ' + $LiteralPath); $global:PFR.Remove($LiteralPath); Save-PfReg } else { Microsoft.PowerShell.Management\Remove-Item -LiteralPath $LiteralPath -Force:$Force } }
function icacls { Log-Pf ('icacls ' + ($args -join ' ')); $global:LASTEXITCODE = 0 }
function whoami { if ($env:PF_WHOAMI) { $env:PF_WHOAMI } else { $global:LASTEXITCODE = 1 } }
function Get-CimInstance { [CmdletBinding()] param([string] $ClassName) [pscustomobject]@{ UserName = $env:PF_CONSOLE_USER } }
foreach ($n in 'Test-Path', 'Get-Item', 'Get-ItemProperty', 'New-Item', 'New-ItemProperty', 'Remove-ItemProperty', 'Remove-Item', 'icacls', 'whoami', 'Get-CimInstance') { if ((Get-Command $n -EA SilentlyContinue | Select-Object -First 1).CommandType -ne 'Function') { exit 97 } }
"""


def _powershell_or_skip() -> str:
    exe = os.environ.get("PORTABLEFIX_TEST_PWSH") or shutil.which("powershell") or shutil.which("pwsh")
    if not exe:
        pytest.skip("no PowerShell available")
    return exe


def _ps_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _action(action_id):
    return next(a for a in load_module(CATALOG_PATH).actions if a.id == action_id)


def _reg(user=None, user_sub=0, policy=None) -> dict:
    """user/policy: {name: (kind, value)} or None = key absent."""
    reg = {}
    if user is not None:
        reg[SS_KEY] = {"values": {n: {"kind": k, "value": v} for n, (k, v) in user.items()}, "sub": user_sub}
    if policy is not None:
        reg[SS_POLICY] = {"values": {n: {"kind": k, "value": v} for n, (k, v) in policy.items()}, "sub": 0}
    return reg


def _as_state(values: dict) -> dict:
    return {n: [k, v] for n, (k, v) in values.items()}


class _StorageSenseRig:
    def __init__(self, tmp_path, reg, who=TECH, console=None):
        self.regfile = tmp_path / "reg.json"
        self.logfile = tmp_path / "calls.log"
        self.program_data = tmp_path / "ProgramData"
        self.program_data.mkdir(exist_ok=True)
        self.regfile.write_text(json.dumps(reg), encoding="utf-8")
        self.logfile.write_text("", encoding="utf-8")
        self.who = who
        self.console = console if console is not None else (who[0] if who else "")

    @property
    def backup(self) -> Path:
        return self.program_data / "PortableFix" / "storage_sense_backup.json"

    def run(self, command, fail_name="", ignore_name=""):
        env_vars = {
            "ProgramData": str(self.program_data), "PF_REGFILE": str(self.regfile), "PF_LOGFILE": str(self.logfile),
            "PF_WHOAMI": f'"{self.who[0]}","{self.who[1]}"' if self.who else "",
            "PF_CONSOLE_USER": self.console, "PF_FAIL_NAME": fail_name, "PF_IGNORE_NAME": ignore_name,
            "USERDOMAIN": "PC", "USERNAME": "technik",
        }
        # Set inside the script, not in the child's environment: Windows
        # PowerShell cannot start with some system variables redirected.
        prelude = "\n".join(f"$env:{k} = {_ps_quote(v)}" for k, v in env_vars.items())
        script = "[Console]::OutputEncoding=[Text.Encoding]::UTF8\n" + prelude + "\n" + SS_STUBS + "\n" + command
        result = subprocess.run(
            [_powershell_or_skip(), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script],
            env=dict(os.environ), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        assert result.returncode != STUB_GUARD_EXIT, "a registry cmdlet was not stubbed - refusing to run the real one"
        return result

    def state(self) -> dict:
        raw = json.loads(self.regfile.read_text(encoding="utf-8-sig"))
        return {k: {n: [e["kind"], e["value"]] for n, e in v["values"].items()} for k, v in raw.items()}

    def calls(self) -> list:
        return self.logfile.read_text(encoding="utf-8-sig").splitlines()


def _verdict(stdout: str) -> str:
    lines = [line for line in stdout.splitlines() if line.startswith("VERDICT: ")]
    assert len(lines) == 1, stdout
    return lines[0]


def test_m09_storage_sense_actions_tiers_and_undo():
    report = _action("storage_sense_report")
    assert report.risk == RiskLevel.SAFE
    assert report.undo_command is None and report.preview_command is None
    enable = _action("storage_sense_enable")
    assert enable.risk == RiskLevel.MODERATE
    assert enable.undo_command and enable.preview_command


def test_m09_storage_sense_report_and_preview_are_read_only():
    for script in (_action("storage_sense_report").command, _action("storage_sense_enable").preview_command):
        for verb in ("Set-", "New-Item", "Remove-", "reg add", "reg delete", "icacls"):
            assert verb not in script, verb


def test_m09_storage_sense_enable_locks_down_the_backup_folder_before_writing():
    # Same unconditional icacls lock-down as m08/m10, so a standard user
    # cannot plant a backup the elevated undo would replay.
    command = _action("storage_sense_enable").command
    assert "icacls $root /inheritance:r" in command
    assert "S-1-5-32-544" in command and "S-1-5-18" in command
    assert "$bk = \"$root\\storage_sense_backup.json\"" in command
    assert command.index("icacls") < command.index("Set-Content -LiteralPath $bk") < command.index("New-ItemProperty")


def test_m09_storage_sense_never_matches_text_and_leaves_downloads_off():
    for action_id in ("storage_sense_report", "storage_sense_enable"):
        action = _action(action_id)
        for script in (action.command, action.undo_command or "", action.preview_command or ""):
            for forbidden in ("Select-String", "-match", "-imatch", "-replace", "Get-HotFix", "$_.Exception.Message -"):
                assert forbidden not in script, f"{action_id}: {forbidden}"
    enable = _action("storage_sense_enable").command
    assert "'32' = 0" in enable
    assert "'512'" not in enable


def test_storage_sense_report_not_configured(tmp_path):
    rig = _StorageSenseRig(tmp_path, _reg())
    result = rig.run(_action("storage_sense_report").command)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "HKCU = registry hive of PC\\technik (S-1-5-21-1000-2000-3000-1001)" in result.stdout
    assert "never configured for this user" in result.stdout
    assert _verdict(result.stdout).startswith("VERDICT: NOT CONFIGURED")
    assert "WARNING" not in result.stdout


def test_storage_sense_report_decodes_known_values_and_flags_unknown_ones(tmp_path):
    user = {"01": ("DWord", 1), "2048": ("DWord", 30), "04": ("DWord", 1), "08": ("DWord", 0),
            "256": ("DWord", 0), "32": ("DWord", 1), "512": ("DWord", 60), "StoragePoliciesNotified": ("DWord", 1),
            "1024": ("DWord", 7)}
    rig = _StorageSenseRig(tmp_path, _reg(user, user_sub=2))
    result = rig.run(_action("storage_sense_report").command)
    assert result.returncode == 0, result.stdout + result.stderr
    out = result.stdout
    assert "01 Storage Sense: on" in out
    assert "2048 Runs: every month" in out
    assert "04 Delete temporary files apps are not using: on" in out
    assert "08 Delete Recycle Bin files: off" in out
    assert "256 Recycle Bin files older than: never" in out
    assert "32 Delete Downloads files: on" in out
    assert "512 Downloads files older than: 60 days" in out
    assert "StoragePoliciesNotified = 1 - unknown (not publicly documented)" in out
    assert "1024 = 7 - unknown (not publicly documented)" in out
    assert "Subkeys (per cloud provider, e.g. OneDrive): 2 - not decoded." in out
    assert _verdict(out) == (
        "VERDICT: ON - runs every month; temporary files: on; Recycle Bin: off (older than never); "
        "Downloads: on (older than 60 days)."
    )


@pytest.mark.parametrize("cadence,text", [(0, "when free disk space is low"), (1, "every day"), (7, "every week"),
                                           (5, "unknown (value 5)")])
def test_storage_sense_report_cadence(tmp_path, cadence, text):
    rig = _StorageSenseRig(tmp_path, _reg({"01": ("DWord", 1), "2048": ("DWord", cadence)}))
    result = rig.run(_action("storage_sense_report").command)
    assert f"2048 Runs: {text}" in result.stdout
    assert "04 Delete temporary files apps are not using: not set (Windows default)" in result.stdout


def test_storage_sense_report_off_and_undecodable_switch(tmp_path):
    result = _StorageSenseRig(tmp_path, _reg({"01": ("DWord", 0)})).run(_action("storage_sense_report").command)
    assert _verdict(result.stdout).startswith("VERDICT: OFF - ")
    result = _StorageSenseRig(tmp_path, _reg({"01": ("String", "abc"), "256": ("String", "x")})).run(
        _action("storage_sense_report").command)
    assert "01 Storage Sense: unknown (value abc)" in result.stdout
    # A value that does not parse is unknown, never "0 = never".
    assert "256 Recycle Bin files older than: unknown (value x)" in result.stdout
    assert _verdict(result.stdout) == "VERDICT: UNKNOWN - value 01 = abc"


@pytest.mark.parametrize("allow,verdict", [(0, "VERDICT: OFF BY POLICY"), (1, "VERDICT: ON BY POLICY")])
def test_storage_sense_report_policy_wins(tmp_path, allow, verdict):
    reg = _reg({"01": ("DWord", 1)}, policy={"AllowStorageSenseGlobal": ("DWord", allow),
                                              "ConfigStorageSenseGlobalCadence": ("DWord", 7)})
    result = _StorageSenseRig(tmp_path, reg).run(_action("storage_sense_report").command)
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"{SS_POLICY}\\AllowStorageSenseGlobal = {allow} (overrides the user setting)" in result.stdout
    assert f"{SS_POLICY}\\ConfigStorageSenseGlobalCadence = 7" in result.stdout
    assert _verdict(result.stdout).startswith(verdict)


def test_storage_sense_report_warns_about_over_the_shoulder_elevation(tmp_path):
    rig = _StorageSenseRig(tmp_path, _reg(), console="PC\\zakaznik")
    result = rig.run(_action("storage_sense_report").command)
    assert "WARNING: the signed-in user is PC\\zakaznik, but PortableFix runs as PC\\technik" in result.stdout
    # The same account in another letter case is not a different user.
    result = _StorageSenseRig(tmp_path, _reg(), console="pc\\TECHNIK").run(_action("storage_sense_report").command)
    assert "WARNING" not in result.stdout


def test_storage_sense_enable_and_undo_round_trip_from_nothing(tmp_path):
    rig = _StorageSenseRig(tmp_path, _reg())
    action = _action("storage_sense_enable")
    result = rig.run(action.command)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "HKCU = registry hive of PC\\technik (S-1-5-21-1000-2000-3000-1001)" in result.stdout
    assert "Storage Sense turned on for PC\\technik" in result.stdout
    assert "  01: absent -> 1" in result.stdout
    assert rig.state()[SS_KEY] == _as_state(WANT)
    backup = json.loads(rig.backup.read_text(encoding="utf-8-sig"))
    assert backup["Sid"] == TECH[1] and backup["User"] == TECH[0]
    assert backup["KeyExisted"] is False
    assert [(v["Name"], v["Existed"]) for v in backup["Values"]] == [(n, False) for n in WANT]
    assert any(c.startswith("icacls ") for c in rig.calls())

    result = rig.run(action.undo_command)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "empty StoragePolicy key removed" in result.stdout
    assert rig.state() == {}
    assert not rig.backup.exists()


def test_storage_sense_undo_restores_exact_previous_values_types_and_absence(tmp_path):
    before = {"01": ("DWord", 0), "2048": ("DWord", 7), "04": ("String", "1"), "08": ("Binary", [1, 0, 0, 0]),
              "256": ("QWord", 14), "512": ("DWord", 60), "StoragePoliciesNotified": ("DWord", 1),
              "CloudfilePolicyConsent": ("MultiString", ["a", "b"])}
    rig = _StorageSenseRig(tmp_path, _reg(before, user_sub=1, policy={"ConfigStorageSenseGlobalCadence": ("DWord", 7)}))
    action = _action("storage_sense_enable")
    result = rig.run(action.command)
    assert result.returncode == 0, result.stdout + result.stderr
    after = rig.state()[SS_KEY]
    for name, (kind, value) in WANT.items():
        assert after[name] == [kind, value], name
    # Values the action does not own are left alone.
    assert after["512"] == ["DWord", 60]
    assert after["StoragePoliciesNotified"] == ["DWord", 1]
    assert "  04: 1 (String) -> 1" in result.stdout
    assert "  32: absent -> 0" in result.stdout

    result = rig.run(action.undo_command)
    assert result.returncode == 0, result.stdout + result.stderr
    restored = rig.state()
    assert restored[SS_KEY] == _as_state(before)
    assert restored[SS_POLICY] == {"ConfigStorageSenseGlobalCadence": ["DWord", 7]}
    assert "32 removed" in result.stdout
    assert "Remove-Item " + SS_KEY not in rig.calls()
    assert not rig.backup.exists()


def test_storage_sense_undo_refuses_without_backup(tmp_path):
    rig = _StorageSenseRig(tmp_path, _reg({"01": ("DWord", 1)}))
    result = rig.run(_action("storage_sense_enable").undo_command)
    assert result.returncode == 1
    assert "Cannot undo: the backup" in result.stdout and "is missing" in result.stdout
    assert rig.state()[SS_KEY] == {"01": ["DWord", 1]}
    assert rig.calls() == []


def test_storage_sense_undo_refuses_a_backup_of_another_user(tmp_path):
    rig = _StorageSenseRig(tmp_path, _reg())
    action = _action("storage_sense_enable")
    assert rig.run(action.command).returncode == 0
    rig.who = ("PC\\zakaznik", "S-1-5-21-1000-2000-3000-1002")
    result = rig.run(action.undo_command)
    assert result.returncode == 1
    assert "the backup belongs to PC\\technik (S-1-5-21-1000-2000-3000-1001)" in result.stdout
    assert "Nothing was changed" in result.stdout
    assert rig.state()[SS_KEY] == _as_state(WANT)
    assert rig.backup.exists()


@pytest.mark.parametrize("tamper", ["foreign_name", "foreign_key", "bad_kind", "garbage"])
def test_storage_sense_undo_refuses_a_tampered_backup(tmp_path, tamper):
    rig = _StorageSenseRig(tmp_path, _reg())
    action = _action("storage_sense_enable")
    assert rig.run(action.command).returncode == 0
    if tamper == "garbage":
        rig.backup.write_text("{not json", encoding="utf-8")
    else:
        b = json.loads(rig.backup.read_text(encoding="utf-8-sig"))
        if tamper == "foreign_name":
            b["Values"][0]["Name"] = "Run"
        elif tamper == "foreign_key":
            b["Key"] = "HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Run"
        else:
            b["Values"][0].update(Existed=True, Kind="Link", Value=1)
        rig.backup.write_text(json.dumps(b), encoding="utf-8")
    calls_before = len(rig.calls())
    result = rig.run(action.undo_command)
    assert result.returncode == 1
    assert "Cannot undo" in result.stdout and "Nothing was changed" in result.stdout
    assert len(rig.calls()) == calls_before
    assert rig.state()[SS_KEY] == _as_state(WANT)


def test_storage_sense_enable_refuses_when_policy_turns_it_off(tmp_path):
    rig = _StorageSenseRig(tmp_path, _reg(policy={"AllowStorageSenseGlobal": ("DWord", 0)}))
    action = _action("storage_sense_enable")
    result = rig.run(action.command)
    assert result.returncode == 1
    assert "turned off by policy" in result.stdout and "Nothing was changed" in result.stdout
    assert SS_KEY not in rig.state()
    assert not rig.backup.exists()
    result = rig.run(action.preview_command)
    assert result.returncode == 0
    assert "Would refuse: Storage Sense is turned off by policy" in result.stdout


def test_storage_sense_enable_refuses_without_whoami(tmp_path):
    rig = _StorageSenseRig(tmp_path, _reg(), who=None)
    result = rig.run(_action("storage_sense_enable").command)
    assert result.returncode == 1
    assert "whoami failed" in result.stdout and "Nothing was changed" in result.stdout
    assert rig.state() == {} and not rig.backup.exists()


def test_storage_sense_enable_refuses_a_value_type_it_could_not_restore(tmp_path):
    rig = _StorageSenseRig(tmp_path, _reg({"01": ("Unknown", 1)}))
    result = rig.run(_action("storage_sense_enable").command)
    assert result.returncode == 1
    assert "Value 01 has registry type Unknown" in result.stdout
    assert rig.state()[SS_KEY] == {"01": ["Unknown", 1]}


def test_storage_sense_enable_warns_about_over_the_shoulder_elevation(tmp_path):
    rig = _StorageSenseRig(tmp_path, _reg(), console="PC\\zakaznik")
    result = rig.run(_action("storage_sense_enable").command)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "WARNING: the signed-in user is PC\\zakaznik, but PortableFix runs as PC\\technik" in result.stdout


def test_storage_sense_enable_failed_write_keeps_backup_so_undo_works(tmp_path):
    rig = _StorageSenseRig(tmp_path, _reg({"01": ("DWord", 0)}))
    action = _action("storage_sense_enable")
    result = rig.run(action.command, fail_name="2048")
    assert result.returncode == 1
    assert "Could not configure Storage Sense" in result.stdout
    assert rig.state()[SS_KEY]["01"] == ["DWord", 1]
    result = rig.run(action.undo_command)
    assert result.returncode == 0, result.stdout + result.stderr
    assert rig.state()[SS_KEY] == {"01": ["DWord", 0]}


def test_storage_sense_enable_fails_when_a_value_does_not_stick(tmp_path):
    rig = _StorageSenseRig(tmp_path, _reg())
    result = rig.run(_action("storage_sense_enable").command, ignore_name="01")
    assert result.returncode == 1
    assert "Storage Sense values did not stick: 01." in result.stdout


def test_storage_sense_preview_lists_changes_and_changes_nothing(tmp_path):
    rig = _StorageSenseRig(tmp_path, _reg({"01": ("DWord", 0), "2048": ("DWord", 1)}))
    result = rig.run(_action("storage_sense_enable").preview_command)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "  01: 0 -> 1" in result.stdout
    assert "  2048: 1 -> 30" in result.stdout
    assert "  32: absent -> 0" in result.stdout
    assert "HKCU = registry hive of PC\\technik" in result.stdout
    assert rig.state()[SS_KEY] == {"01": ["DWord", 0], "2048": ["DWord", 1]}
    assert rig.calls() == []
    assert not rig.backup.exists()


# Target is Windows PowerShell 5.1: the scripts must parse, and none may use
# PS7-only syntax (??, ?., ternary, &&/||) that pwsh accepts but 5.1 rejects.
PARSE_CHECKER = (
    "$s = Get-Content -Raw -Encoding UTF8 -LiteralPath $env:PFSCRIPTS_FILE | ConvertFrom-Json; "
    "foreach ($p in $s.PSObject.Properties) { $t = $null; $e = $null; "
    "$ast = [System.Management.Automation.Language.Parser]::ParseInput($p.Value, [ref]$t, [ref]$e); "
    "foreach ($x in @($e)) { if ($x) { Write-Output ('ERR ' + $p.Name + ': ' + $x.Message) } }; "
    "$ps7 = @($t | Where-Object { [string]$_.Kind -in @('QuestionQuestion','QuestionQuestionEquals','QuestionDot','QuestionLBracket','AndAnd','OrOr') }); "
    "$ps7 += @($ast.FindAll({ param($n) $n.GetType().Name -in @('TernaryExpressionAst','PipelineChainAst') }, $true)); "
    "if ($ps7.Count) { Write-Output ('ERR ' + $p.Name + ': PowerShell 7-only syntax') } }; "
    "Write-Output PARSE_DONE"
)


def test_m09_catalog_scripts_parse_without_powershell_7_only_syntax(tmp_path):
    module = load_module(CATALOG_PATH)
    scripts = {
        f"{a.id}.{field}": getattr(a, field)
        for a in module.actions
        for field in ("command", "undo_command", "preview_command")
        if getattr(a, field)
    }
    scripts_file = tmp_path / "scripts.json"
    scripts_file.write_text(json.dumps(scripts), encoding="utf-8")
    result = subprocess.run(
        [_powershell_or_skip(), "-NoProfile", "-NonInteractive", "-Command", PARSE_CHECKER],
        env=dict(os.environ, PFSCRIPTS_FILE=str(scripts_file)), capture_output=True, text=True, timeout=120,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert "PARSE_DONE" in result.stdout, result.stdout + result.stderr
    assert [line for line in result.stdout.splitlines() if line.startswith("ERR")] == []
