"""Run generated `ops:` scripts (portablefix/ops.py) in PowerShell against
an in-memory registry, service table and task list.

Every command the scripts use to touch the system - the registry provider
cmdlets, sc.exe and the ScheduledTasks cmdlets - is a stub function
(functions win over cmdlets and executables in command lookup) that loads
and saves the fake state from a JSON file, so a command and the undo run
after it see the same machine. The script refuses to run (exit 97) unless
each stubbed name resolves to the stub, so a test can never touch the host.
[Security.Principal.WindowsIdentity]::GetCurrent() is a .NET call no
function can shadow; the rig swaps exactly that text for a stub.

Used by tests/test_ops.py and tests/test_m09_catalog.py.
"""
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from portablefix import ops

STUB_GUARD_EXIT = 97
TECH = ("PC\\technik", "S-1-5-21-1000-2000-3000-1001")
OTHER = ("PC\\zakaznik", "S-1-5-21-1000-2000-3000-1002")
IDENTITY_CALL = "[Security.Principal.WindowsIdentity]::GetCurrent()"

# Keys are stored under their Registry:: provider path, lower-cased for
# lookup (the registry is case-insensitive) with the spelling kept inside.
STUBS = r"""
function Pf-Load { $global:PFM = [IO.File]::ReadAllText($env:PF_STATEFILE) | ConvertFrom-Json }
function Pf-Save { [IO.File]::WriteAllText($env:PF_STATEFILE, (ConvertTo-Json -InputObject $global:PFM -Depth 8)) }
function Pf-Log([string]$m) { [IO.File]::AppendAllText($env:PF_LOGFILE, $m + "`n") }
function Pf-Key([string]$p) { Pf-Load; $global:PFM.keys.PSObject.Properties[$p.ToLowerInvariant()] }
function Pf-Typed($kind, $val) { switch ($kind) { 'DWord' { [int]$val } 'QWord' { [int64]$val } 'Binary' { ,([byte[]]@($val)) } 'MultiString' { ,([string[]]@($val)) } default { [string]$val } } }
function Pf-SName([string]$n) { if ($n -eq '') { '(default)' } else { $n } }
function Test-Path { [CmdletBinding()] param([Parameter(Position = 0)] [string] $Path, [string] $LiteralPath) $p = $(if ($LiteralPath) { $LiteralPath } else { $Path }); if ($p -like 'Registry::*') { $null -ne (Pf-Key $p) } else { Microsoft.PowerShell.Management\Test-Path -LiteralPath $p } }
function Get-Item { [CmdletBinding()] param([string] $LiteralPath)
  $k = Pf-Key $LiteralPath; if ($null -eq $k) { throw [System.Management.Automation.ItemNotFoundException]::new('Nenájdené.') }
  $lower = $LiteralPath.ToLowerInvariant(); $sub = @($global:PFM.keys.PSObject.Properties | Where-Object { $_.Name.StartsWith($lower + '\') -and (-not $_.Name.Substring($lower.Length + 1).Contains('\')) }).Count
  $o = [pscustomobject]@{ KeyPath = $lower; SubKeyCount = $sub }
  $o | Add-Member ScriptMethod GetValueNames { ,([string[]]@((Pf-Key $this.KeyPath).Value.values.PSObject.Properties | ForEach-Object { if ($_.Name -eq '(default)') { '' } else { $_.Name } })) }
  $o | Add-Member ScriptMethod GetValue { param($n, $d, $opt) $e = (Pf-Key $this.KeyPath).Value.values.PSObject.Properties[(Pf-SName $n)]; if ($null -eq $e) { return $d }; Pf-Typed $e.Value.kind $e.Value.value }
  $o | Add-Member ScriptMethod GetValueKind { param($n) [string](Pf-Key $this.KeyPath).Value.values.PSObject.Properties[(Pf-SName $n)].Value.kind }
  $o }
function New-Item { [CmdletBinding()] param([Parameter(Position = 0)] [string] $Path, [string] $ItemType, [switch] $Force)
  if ($Path -notlike 'Registry::*') { return Microsoft.PowerShell.Management\New-Item -Path $Path -ItemType $ItemType -Force:$Force }
  Pf-Load; Pf-Log ('New-Item ' + $Path); if ($env:PF_FAIL_KEY -and ($Path -eq $env:PF_FAIL_KEY)) { throw [System.UnauthorizedAccessException]::new('Prístup odmietnutý.') }
  $parts = $Path.Split('\'); for ($i = 2; $i -le $parts.Count; $i++) { $p = ($parts[0..($i - 1)] -join '\'); $l = $p.ToLowerInvariant(); if ($null -eq $global:PFM.keys.PSObject.Properties[$l]) { $global:PFM.keys | Add-Member -NotePropertyName $l -NotePropertyValue ([pscustomobject]@{ path = $p; values = [pscustomobject]@{} }) } }
  Pf-Save; [pscustomobject]@{ Path = $Path } }
function New-ItemProperty { [CmdletBinding()] param([string] $LiteralPath, [string] $Name, $Value, [string] $PropertyType, [switch] $Force)
  Pf-Log ('New-ItemProperty ' + $LiteralPath + ' ' + $Name + ' ' + $PropertyType); if ($env:PF_FAIL_NAME -eq $Name) { throw [System.UnauthorizedAccessException]::new('Prístup odmietnutý.') }
  $k = Pf-Key $LiteralPath; if ($null -eq $k) { throw [System.Management.Automation.ItemNotFoundException]::new('Nenájdené.') }
  $v = $Value; if ($PropertyType -eq 'Binary') { $v = @(@($Value) | ForEach-Object { [int]$_ }) } elseif ($PropertyType -eq 'MultiString') { $v = @(@($Value) | ForEach-Object { [string]$_ }) } elseif ($PropertyType -eq 'DWord') { $v = [int]$Value } elseif ($PropertyType -eq 'QWord') { $v = [int64]$Value } else { $v = [string]$Value }
  $vn = $Name; $vals = $k.Value.values; if ($vals.PSObject.Properties[$vn]) { $vals.PSObject.Properties.Remove($vn) }; $vals | Add-Member -NotePropertyName $vn -NotePropertyValue ([pscustomobject]@{ kind = $PropertyType; value = $v })
  Pf-Save; [pscustomobject]@{ Name = $Name } }
function Remove-ItemProperty { [CmdletBinding()] param([string] $LiteralPath, [string] $Name)
  Pf-Log ('Remove-ItemProperty ' + $LiteralPath + ' ' + $Name); if ($env:PF_FAIL_NAME -eq $Name) { throw [System.UnauthorizedAccessException]::new('Prístup odmietnutý.') }
  $k = Pf-Key $LiteralPath; $vn = $Name; if (($null -eq $k) -or ($null -eq $k.Value.values.PSObject.Properties[$vn])) { throw [System.Management.Automation.PSArgumentException]::new('Vlastnosť neexistuje.') }; $k.Value.values.PSObject.Properties.Remove($vn); Pf-Save }
function Remove-Item { [CmdletBinding()] param([string] $LiteralPath, [switch] $Force, [switch] $Recurse)
  if ($LiteralPath -notlike 'Registry::*') { return Microsoft.PowerShell.Management\Remove-Item -LiteralPath $LiteralPath -Force:$Force -Recurse:$Recurse }
  Pf-Load; Pf-Log ('Remove-Item ' + $LiteralPath); $l = $LiteralPath.ToLowerInvariant(); if (@($global:PFM.keys.PSObject.Properties | Where-Object { $_.Name.StartsWith($l + '\') }).Count) { throw 'Kľúč má podkľúče.' }; $global:PFM.keys.PSObject.Properties.Remove($l); Pf-Save }
function sc.exe { $a = @($args); Pf-Log ('sc.exe ' + ($a -join ' ')); if ([int]$env:PF_SC_EXIT) { $global:LASTEXITCODE = [int]$env:PF_SC_EXIT; return 'Zlyhanie' }
  $p = ('Registry::HKEY_LOCAL_MACHINE\SYSTEM\CurrentControlSet\Services\' + $a[1]).ToLowerInvariant(); Pf-Load; $k = $global:PFM.keys.PSObject.Properties[$p]; if ($null -eq $k) { $global:LASTEXITCODE = 1060; return }
  $map = @{ 'boot' = 0; 'system' = 1; 'auto' = 2; 'delayed-auto' = 2; 'demand' = 3; 'disabled' = 4 }; $vals = $k.Value.values
  foreach ($n in 'Start', 'DelayedAutostart') { if ($vals.PSObject.Properties[$n] -and -not (($n -eq 'DelayedAutostart') -and $env:PF_SC_KEEP_DELAYED)) { $vals.PSObject.Properties.Remove($n) } }
  $vals | Add-Member -NotePropertyName 'Start' -NotePropertyValue ([pscustomobject]@{ kind = 'DWord'; value = $map[$a[3]] })
  if (($a[3] -eq 'delayed-auto') -and ($null -eq $vals.PSObject.Properties['DelayedAutostart'])) { $vals | Add-Member -NotePropertyName 'DelayedAutostart' -NotePropertyValue ([pscustomobject]@{ kind = 'DWord'; value = 1 }) }
  Pf-Save; 'Úspech'; $global:LASTEXITCODE = 0 }
function Get-ScheduledTask { [CmdletBinding()] param([string] $TaskPath, [string] $TaskName)
  Pf-Log ('Get-ScheduledTask ' + $TaskPath + $TaskName); if ($env:PF_TASK_BROKEN) { throw [System.Runtime.InteropServices.COMException]::new('Služba neodpovedá.') }
  Pf-Load; $t = $global:PFM.tasks.PSObject.Properties[$TaskPath + $TaskName]
  if ($null -eq $t) { $PSCmdlet.ThrowTerminatingError([System.Management.Automation.ErrorRecord]::new([System.Exception]::new('Nenašli sa žiadne objekty.'), 'CmdletizationQuery_NotFound_TaskName', 'ObjectNotFound', $TaskName)) }
  [pscustomobject]@{ TaskPath = $TaskPath; TaskName = $TaskName; State = [string]$t.Value } }
function Pf-SetTask($tp, $tn, $state) { Pf-Log ($state + ' ' + $tp + $tn); Pf-Load; $t = $global:PFM.tasks.PSObject.Properties[$tp + $tn]; if ($null -eq $t) { throw 'Nenájdené.' }; $t.Value = $state; Pf-Save; [pscustomobject]@{ State = $state } }
function Enable-ScheduledTask { [CmdletBinding()] param([string] $TaskPath, [string] $TaskName) Pf-SetTask $TaskPath $TaskName 'Ready' }
function Disable-ScheduledTask { [CmdletBinding()] param([string] $TaskPath, [string] $TaskName) Pf-SetTask $TaskPath $TaskName 'Disabled' }
function Pf-WindowsIdentity { if (-not $env:PF_WHO_NAME) { throw [System.Security.SecurityException]::new('Prístup odmietnutý.') }; [pscustomobject]@{ Name = $env:PF_WHO_NAME; User = [pscustomobject]@{ Value = $env:PF_WHO_SID } } }
foreach ($n in 'Test-Path', 'Get-Item', 'New-Item', 'New-ItemProperty', 'Remove-ItemProperty', 'Remove-Item', 'Set-ItemProperty', 'sc.exe', 'Get-ScheduledTask', 'Enable-ScheduledTask', 'Disable-ScheduledTask', 'Pf-WindowsIdentity') { if ((Get-Command $n -EA SilentlyContinue | Select-Object -First 1).CommandType -ne 'Function') { exit 97 } }
"""
# Set-ItemProperty is never used by generated scripts; the stub makes an
# accidental use fail loudly instead of reaching the host registry.
STUBS = STUBS.replace(
    "function Pf-WindowsIdentity",
    "function Set-ItemProperty { throw 'Set-ItemProperty is not stubbed' }\nfunction Pf-WindowsIdentity",
)


def powershell_or_skip() -> str:
    exe = os.environ.get("PORTABLEFIX_TEST_PWSH") or shutil.which("powershell") or shutil.which("pwsh")
    if not exe:
        pytest.skip("no PowerShell available")
    return exe


def ps_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def provider(path: str) -> str:
    hive, _, rest = path.partition("\\")
    return f"Registry::{ops.HIVES[hive]}\\{rest}"


def with_identity_stub(script: str) -> str:
    stubbed = script.replace(IDENTITY_CALL, "(Pf-WindowsIdentity)")
    assert "WindowsIdentity]" not in stubbed
    return stubbed


class Machine:
    """The fake PC. registry: {"HKCU\\Software\\X": {name: (kind, value)}};
    "(default)" is the default value; every ancestor key of a listed key exists too, like in a real hive.
    services: {name: (start, delayed)}; tasks: {"\\Folder\\Name": "Ready"}."""

    def __init__(self, tmp_path: Path, registry=None, services=None, tasks=None, who=TECH):
        self.dir = tmp_path
        self.statefile = tmp_path / "machine.json"
        self.logfile = tmp_path / "calls.log"
        self.who = who
        keys = {}
        registry = dict(registry or {})
        for name, (start, delayed) in (services or {}).items():
            values = {"Start": ("DWord", start), "Type": ("DWord", 16)}
            if delayed:
                values["DelayedAutostart"] = ("DWord", 1)
            registry[f"HKLM\\SYSTEM\\CurrentControlSet\\Services\\{name}"] = values
        for path, values in registry.items():
            parts = path.split("\\")
            for end in range(2, len(parts) + 1):
                sub = "\\".join(parts[:end])
                keys.setdefault(provider(sub).lower(), {"path": provider(sub), "values": {}})
            # The default value is stored as "(default)", the provider's name
            # for it - Windows PowerShell's ConvertFrom-Json rejects "".
            keys[provider(path).lower()]["values"] = {n: {"kind": k, "value": v} for n, (k, v) in values.items()}
        self.statefile.write_text(json.dumps({"keys": keys, "tasks": dict(tasks or {})}), encoding="utf-8")
        self.logfile.write_text("", encoding="utf-8")

    def run(self, script: str, **env) -> subprocess.CompletedProcess:
        env_vars = {
            "PF_STATEFILE": str(self.statefile), "PF_LOGFILE": str(self.logfile),
            "PF_WHO_NAME": self.who[0] if self.who else "", "PF_WHO_SID": self.who[1] if self.who else "",
            "PF_FAIL_NAME": "", "PF_FAIL_KEY": "", "PF_SC_EXIT": "0", "PF_TASK_BROKEN": "",
            # sc.exe leaves an existing DelayedAutostart flag behind.
            "PF_SC_KEEP_DELAYED": "",
        }
        env_vars.update({k: str(v) for k, v in env.items()})
        # Set inside the script, not in the child's environment: Windows
        # PowerShell cannot start with some system variables redirected.
        prelude = "\n".join(f"$env:{k} = {ps_quote(v)}" for k, v in env_vars.items())
        full = "[Console]::OutputEncoding=[Text.Encoding]::UTF8\n" + prelude + "\n" + STUBS + "\n" + with_identity_stub(script)
        result = subprocess.run(
            [powershell_or_skip(), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", full],
            env=dict(os.environ), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        assert result.returncode != STUB_GUARD_EXIT, "a system command was not stubbed - refusing to run the real one"
        return result

    def registry(self) -> dict:
        """{"HKCU\\Software\\X": {name: (kind, value)}} for every key, with
        "(default)" for the default value."""
        raw = json.loads(self.statefile.read_text(encoding="utf-8-sig"))
        back = {v: k for k, v in ops.HIVES.items()}
        out = {}
        for key in raw["keys"].values():
            hive, _, rest = key["path"][len("Registry::"):].partition("\\")
            out[back[hive] + "\\" + rest] = {
                n: (e["kind"], e["value"]) for n, e in (key["values"] or {}).items()
            }
        return out

    def values(self, path: str) -> dict | None:
        found = {k.lower(): v for k, v in self.registry().items()}.get(path.lower())
        return found

    def service(self, name: str) -> tuple:
        values = self.values(f"HKLM\\SYSTEM\\CurrentControlSet\\Services\\{name}")
        return values["Start"][1], values.get("DelayedAutostart", ("DWord", 0))[1] == 1

    def tasks(self) -> dict:
        return json.loads(self.statefile.read_text(encoding="utf-8-sig"))["tasks"]

    def calls(self) -> list[str]:
        return self.logfile.read_text(encoding="utf-8").splitlines()


def command_with_state(command: str, state_path: Path) -> str:
    """The command exactly as the GUI runs it: executor's prefix defines the
    state file variable."""
    from portablefix.executor import build_execution_plan

    plan = build_execution_plan(command, dry_run=False, ops_state=state_path)
    return plan.argv[-1]
