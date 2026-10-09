"""Research G25 across catalogs: every per-user action follows the signed-in
user's hive ($__pfUserHive from the prelude), never the HKCU: of the account
PortableFix runs as (the technician's, over the shoulder).

The real catalog commands run in PowerShell against an in-memory registry:
every registry cmdlet is a stub function (functions win over cmdlets in
command lookup) that prints "REG <cmdlet> <path>" and keeps values in a
hashtable; file, network and process tools are stubbed too, so nothing on
the host changes. The script refuses to run (exit 97) unless every stubbed
name resolves to the stub.
"""
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from portablefix import target_user
from portablefix.executor import build_execution_plan
from portablefix.module_engine import load_module

MODULES_DIR = Path(__file__).resolve().parent.parent / "Modules"
STUB_GUARD_EXIT = 97
PROCESS_SID = "S-1-5-21-4444-5555-6666-1001"
CLIENT_SID = "S-1-5-21-4444-5555-6666-1002"
CLIENT_HIVE = target_user.hive_path(CLIENT_SID)
IDENTITY_CALL = "[Security.Principal.WindowsIdentity]::GetCurrent()"

STUBS = r"""
$global:__pfVals = @{}; $global:__pfGone = @{}
function Pf-Reg([string]$c, [string]$p) { [Console]::Out.WriteLine('REG ' + $c + ' ' + $p) }
function Pf-Path($Path, $LiteralPath) { if ($LiteralPath) { [string]$LiteralPath } else { [string]$Path } }
function Pf-IsReg([string]$p) { ($p -like 'HK*:*') -or ($p -like 'Registry::*') -or ($p -like 'Microsoft.PowerShell.Core\Registry::*') }
function Test-Path { [CmdletBinding()] param([Parameter(Position=0)] $Path, $LiteralPath, $PathType) $p = Pf-Path $Path $LiteralPath; if ($p -like '*.json') { return [bool]$env:PF_BACKUP_JSON }; if (-not (Pf-IsReg $p)) { return (Microsoft.PowerShell.Management\Test-Path -LiteralPath $p) }; if ($p -eq $env:PF_HIVE) { return $true }; if ($p -like 'HKCU:*') { [Console]::Out.WriteLine('REG Test-Path ' + $p) }; -not $global:__pfGone.ContainsKey($p) }
function Get-Item { [CmdletBinding()] param([Parameter(Position=0)] $Path, $LiteralPath, [switch] $Force) $p = Pf-Path $Path $LiteralPath; if (-not (Pf-IsReg $p)) { return (Microsoft.PowerShell.Management\Get-Item -LiteralPath $p -Force:$Force) }; Pf-Reg 'Get-Item' $p; $o = [pscustomobject]@{ KeyPath = $p; SubKeyCount = 0 }; $o | Add-Member ScriptMethod GetValueNames { ,([string[]]@($global:__pfVals.Keys | Where-Object { $_ -like ($this.KeyPath + '\*') } | ForEach-Object { $_.Substring($this.KeyPath.Length + 1) })) }; $o | Add-Member ScriptMethod GetValue { param($n, $d, $opt) $v = $global:__pfVals[$this.KeyPath + '\' + $n]; if ($null -eq $v) { $d } else { $v } }; $o | Add-Member ScriptMethod GetValueKind { param($n) 'DWord' }; $o }
function Get-ItemProperty { [CmdletBinding()] param([Parameter(Position=0)] $Path, $LiteralPath, $Name) $p = Pf-Path $Path $LiteralPath; Pf-Reg 'Get-ItemProperty' $p; $h = [ordered]@{}; foreach ($k in @($global:__pfVals.Keys)) { if ($k -like ($p + '\*')) { $h[$k.Substring($p.Length + 1)] = $global:__pfVals[$k] } }; if ($h.Count) { [pscustomobject]$h } }
function Set-ItemProperty { [CmdletBinding()] param([Parameter(Position=0)] $Path, $LiteralPath, $Name, $Value, $Type) $p = Pf-Path $Path $LiteralPath; Pf-Reg 'Set-ItemProperty' $p; $global:__pfVals[$p + '\' + $Name] = $Value }
function New-ItemProperty { [CmdletBinding()] param([Parameter(Position=0)] $Path, $LiteralPath, $Name, $Value, $PropertyType, [switch] $Force) $p = Pf-Path $Path $LiteralPath; Pf-Reg 'New-ItemProperty' $p; $global:__pfVals[$p + '\' + $Name] = $Value }
function Remove-ItemProperty { [CmdletBinding()] param([Parameter(Position=0)] $Path, $LiteralPath, $Name) $p = Pf-Path $Path $LiteralPath; Pf-Reg 'Remove-ItemProperty' $p; $global:__pfVals.Remove($p + '\' + $Name) }
function New-Item { [CmdletBinding()] param([Parameter(Position=0)] $Path, $ItemType, [switch] $Force) if ($ItemType -eq 'Directory') { return (Microsoft.PowerShell.Management\New-Item -Path $Path -ItemType Directory -Force:$Force) }; Pf-Reg 'New-Item' $Path }
function Remove-Item { [CmdletBinding()] param([Parameter(Position=0)] $Path, $LiteralPath, [switch] $Recurse, [switch] $Force) $p = Pf-Path $Path $LiteralPath; if (-not (Pf-IsReg $p)) { return (Microsoft.PowerShell.Management\Remove-Item -LiteralPath $p -Force:$Force -Recurse:$Recurse -EA SilentlyContinue) }; Pf-Reg 'Remove-Item' $p; $global:__pfGone[$p] = $true }
function Get-ChildItem { [CmdletBinding()] param([Parameter(Position=0)] $Path, $LiteralPath, $Filter, [switch] $Directory, [switch] $Force) $p = Pf-Path $Path $LiteralPath; if (-not (Pf-IsReg $p)) { return (Microsoft.PowerShell.Management\Get-ChildItem -LiteralPath $p -Force:$Force) }; Pf-Reg 'Get-ChildItem' $p; foreach ($n in 'Microsoft.VbaAddin', 'Acme.Addin') { [pscustomobject]@{ PSChildName = $n; PSPath = ($p + '\' + $n) } } }
function Get-Content { [CmdletBinding()] param([Parameter(Position=0)] $Path, $LiteralPath, [switch] $Raw, $Encoding) $env:PF_BACKUP_JSON }
function Set-Content { [CmdletBinding()] param([Parameter(ValueFromPipeline=$true)] $Value, $Path, $LiteralPath, $Encoding) begin { [Console]::Out.WriteLine('FILE ' + (Pf-Path $Path $LiteralPath)) } process { } }
function netsh { $global:LASTEXITCODE = 0 }
function dsregcmd { 'AzureAdJoined : NO'; $global:LASTEXITCODE = 0 }
function icacls { $global:LASTEXITCODE = 0 }
function reg { $a = @($args); if ($a[0] -eq 'export') { [IO.File]::WriteAllText($a[2], 'REGEDIT4') }; [Console]::Out.WriteLine('REGEXE ' + ($a -join ' ')); $global:LASTEXITCODE = 0 }
function Get-CimInstance { [CmdletBinding()] param($ClassName) [pscustomobject]@{ PartOfDomain = $false; UserName = 'PC\klient' } }
function Get-Process { [CmdletBinding()] param($Name) }
function Pf-WindowsIdentity { [pscustomobject]@{ Name = 'PC\technik'; User = [pscustomobject]@{ Value = $env:PF_PROCESS_SID } } }
foreach ($n in 'Test-Path', 'Get-Item', 'Get-ItemProperty', 'Set-ItemProperty', 'New-ItemProperty', 'Remove-ItemProperty', 'New-Item', 'Remove-Item', 'Get-ChildItem', 'Get-Content', 'Set-Content', 'netsh', 'dsregcmd', 'icacls', 'reg', 'Get-CimInstance', 'Get-Process', 'Pf-WindowsIdentity') { if ((Get-Command $n -EA SilentlyContinue | Select-Object -First 1).CommandType -ne 'Function') { exit 97 } }
"""


def _pwsh_or_skip() -> str:
    exe = os.environ.get("PORTABLEFIX_TEST_PWSH") or shutil.which("powershell") or shutil.which("pwsh")
    if not exe:
        pytest.skip("no PowerShell available")
    return exe


def _ps_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _action(module_dir: str, action_id: str):
    module = load_module(MODULES_DIR / module_dir / "actions.yaml")
    return next(a for a in module.actions if a.id == action_id)


def _client():
    return target_user.TargetUser(
        status=target_user.DIFFERENT, process_sid=PROCESS_SID, process_user="PC\\technik",
        target_sid=CLIENT_SID, target_user="PC\\klient", session_id=1,
    )


def run_in_client_hive(tmp_path: Path, script: str, backup: dict | list | None = None):
    """Runs `script` the way the executor does for the over-the-shoulder
    case (prelude names the client's hive) against the stubs above.
    Returns (result, registry paths touched)."""
    script = script.replace(IDENTITY_CALL, "(Pf-WindowsIdentity)")
    assert "WindowsIdentity]" not in script
    program_data = tmp_path / "ProgramData"
    program_data.mkdir(exist_ok=True)
    env_vars = {
        "ProgramData": str(program_data), "PF_HIVE": CLIENT_HIVE, "PF_PROCESS_SID": PROCESS_SID,
        "PF_BACKUP_JSON": json.dumps(backup) if backup is not None else "",
        "LOCALAPPDATA": str(tmp_path / "Local"), "APPDATA": str(tmp_path / "Roaming"),
    }
    plan = build_execution_plan(script, dry_run=False, target_user=_client())
    full = "\n".join(
        ["[Console]::OutputEncoding=[Text.Encoding]::UTF8"]
        + [f"$env:{k} = {_ps_quote(v)}" for k, v in env_vars.items()]
        + [STUBS, plan.argv[-1]]
    )
    result = subprocess.run(
        [_pwsh_or_skip(), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", full],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert result.returncode != STUB_GUARD_EXIT, "a system cmdlet was not stubbed - refusing to run the real one"
    registry = [line.split(" ", 2)[2] for line in result.stdout.splitlines() if line.startswith("REG ")]
    return result, registry


# (module dir, action id, fields that touch the user's registry, backup the
# undo reads). Every m13 user-hive action is covered by tests/test_m13_catalog.
TASKMGR_BACKUP = [
    {"Path": "HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Policies\\System", "DisableTaskMgr": 1, "DisableRegistryTools": None},
    {"Path": "HKLM:\\Software\\Microsoft\\Windows\\CurrentVersion\\Policies\\System", "DisableTaskMgr": None, "DisableRegistryTools": None},
]
PROXY_BACKUP = {"ProxyEnable": 1, "ProxyServer": "127.0.0.1:8080", "AutoConfigURL": None}
ADDINS_BACKUP = [{"Key": "Acme.Addin", "LoadBehavior": 3}]

CASES = [
    ("m08_security", "sec_restore_taskmgr_regedit", ("command", "undo_command"), TASKMGR_BACKUP),
    ("m12_online", "online_proxy_check", ("command",), None),
    ("m12_online", "online_proxy_reset", ("command", "undo_command"), PROXY_BACKUP),
    ("m16_office_repair", "office_com_addin_disable_all_thirdparty", ("command", "undo_command"), ADDINS_BACKUP),
    # The clear action's undo is reg import of .reg files - no registry cmdlet.
    ("m17_browser_deep", "browser_policy_report", ("command",), None),
    ("m17_browser_deep", "browser_clear_policy_keys", ("command",), None),
    ("m09_tuning", "storage_sense_enable", ("command", "undo_command", "preview_command"), {
        "User": "PC\\klient", "Sid": CLIENT_SID, "KeyExisted": True,
        "Key": CLIENT_HIVE + "\\Software\\Microsoft\\Windows\\CurrentVersion\\StorageSense\\Parameters\\StoragePolicy",
        "Values": [{"Name": n, "Existed": False, "Kind": None, "Value": None} for n in ("01", "2048", "04", "08", "256", "32")],
    }),
]


def _cases():
    for module_dir, action_id, fields, backup in CASES:
        for field in fields:
            yield pytest.param(module_dir, action_id, field, backup, id=f"{action_id}.{field}")


@pytest.mark.parametrize("module_dir, action_id, field, backup", list(_cases()))
def test_per_user_actions_follow_the_signed_in_users_hive(tmp_path, module_dir, action_id, field, backup):
    # README: "HKCU in other modules still means the account PortableFix runs
    # as" - for malware remediation that left the hijack in place.
    action = _action(module_dir, action_id)
    script = getattr(action, field)
    assert "HKCU:\\" not in script, f"{action_id}.{field} hard-codes HKCU:"
    result, registry = run_in_client_hive(tmp_path, script, backup)
    assert result.returncode == 0, result.stdout + result.stderr
    assert registry, result.stdout
    for path in registry:
        assert not path.startswith("HKCU:"), (path, result.stdout)
        assert path.startswith(CLIENT_HIVE + "\\") or path.startswith("HKLM:\\"), path
    assert any(path.startswith(CLIENT_HIVE + "\\") for path in registry), registry


@pytest.mark.parametrize("module_dir, action_id, field, backup", list(_cases()))
def test_per_user_actions_fall_back_to_hkcu_without_the_prelude(tmp_path, module_dir, action_id, field, backup):
    script = getattr(_action(module_dir, action_id), field)
    # No prelude: the executor emits nothing and $__pfUserHive is undefined.
    script = script.replace(IDENTITY_CALL, "(Pf-WindowsIdentity)")
    program_data = tmp_path / "ProgramData"
    program_data.mkdir(exist_ok=True)
    # Without the prelude the backup is the process user's own.
    backup_json = json.dumps(backup).replace(CLIENT_HIVE, "HKCU:").replace(CLIENT_SID, PROCESS_SID) if backup is not None else ""
    full = "\n".join([
        f"$env:ProgramData = {_ps_quote(str(program_data))}", "$env:PF_HIVE = 'HKCU:'",
        f"$env:PF_PROCESS_SID = '{PROCESS_SID}'",
        f"$env:PF_BACKUP_JSON = {_ps_quote(backup_json)}",
        STUBS, "[Console]::OutputEncoding=[Text.Encoding]::UTF8; " + script,
    ])
    result = subprocess.run(
        [_pwsh_or_skip(), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", full],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    registry = [line.split(" ", 2)[2] for line in result.stdout.splitlines() if line.startswith("REG ")]
    assert any(path.startswith("HKCU:\\") for path in registry), registry
    assert not any("HKEY_USERS" in path for path in registry)
