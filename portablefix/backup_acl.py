r"""Lock down %ProgramData%\PortableFix before a command touches its backups.

Catalog actions keep their backups there and their undo_command re-imports
them elevated. A folder created under C:\ProgramData inherits an ACL that
lets standard users create files in it, so a local user could pre-plant a
backup that does not exist yet and have an administrator import it later.

The step below is prepended (executor.build_execution_plan, undo.py) to
every real run whose command mentions the folder. It is idempotent, uses
SIDs only (locale-free), is Windows PowerShell 5.1 compatible and fails
closed: any problem prints a message and exits 1 before the command runs.
"""

# S-1-5-18 SYSTEM, S-1-5-32-544 Administrators,
# S-1-5-80-956008885-... TrustedInstaller (may own files, never gets access).
_PS = r"""
$__pfDir = Join-Path $env:ProgramData 'PortableFix';
$__pfAdm = New-Object Security.Principal.SecurityIdentifier('S-1-5-32-544');
$__pfSys = New-Object Security.Principal.SecurityIdentifier('S-1-5-18');
$__pfTi = New-Object Security.Principal.SecurityIdentifier('S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464');
function __pfTrusted([string]$p) { try { $o = (Get-Acl -LiteralPath $p -ErrorAction Stop).GetOwner([Security.Principal.SecurityIdentifier]); return (($o -eq $__pfAdm) -or ($o -eq $__pfSys) -or ($o -eq $__pfTi)) } catch { return $false } };
function __pfAside([string]$p) { $b = $p -replace '\.untrusted\d*$', ''; $d = $b + '.untrusted'; $n = 0; while (Test-Path -LiteralPath $d) { $n++; $d = $b + '.untrusted' + $n }; Move-Item -LiteralPath $p -Destination $d -Force -ErrorAction Stop; Write-Output ('[PortableFix] Untrusted item in the backup folder set aside: ' + $p + ' -> ' + $d) };
function __pfScan([string]$dir) { foreach ($i in @(Get-ChildItem -LiteralPath $dir -Force -ErrorAction Stop)) { if (-not $i.PSIsContainer -and $i.Name -match '\.untrusted\d*$' -and -not ($i.Attributes -band [IO.FileAttributes]::ReparsePoint)) { continue }; if ((($i.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) -or -not (__pfTrusted $i.FullName)) { __pfAside $i.FullName } elseif ($i.PSIsContainer) { __pfScan $i.FullName } } };
try {
  if (Test-Path -LiteralPath $__pfDir) { $it = Get-Item -LiteralPath $__pfDir -Force -ErrorAction Stop; if (-not $it.PSIsContainer -or (($it.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0)) { __pfAside $__pfDir } };
  if (-not (Test-Path -LiteralPath $__pfDir)) { New-Item -ItemType Directory -Path $__pfDir -ErrorAction Stop | Out-Null };
  $acl = New-Object Security.AccessControl.DirectorySecurity;
  $acl.SetOwner($__pfAdm);
  $acl.SetAccessRuleProtection($true, $false);
  foreach ($s in @($__pfSys, $__pfAdm)) { $acl.AddAccessRule((New-Object Security.AccessControl.FileSystemAccessRule($s, 'FullControl', 'ContainerInherit,ObjectInherit', 'None', 'Allow'))) };
  Set-Acl -LiteralPath $__pfDir -AclObject $acl -ErrorAction Stop;
  $chk = Get-Acl -LiteralPath $__pfDir -ErrorAction Stop; $bad = @($chk.Access | Where-Object { $r = $_.IdentityReference; if ($r -isnot [Security.Principal.SecurityIdentifier]) { $r = $r.Translate([Security.Principal.SecurityIdentifier]) }; ($r -ne $__pfAdm) -and ($r -ne $__pfSys) });
  if (-not $chk.AreAccessRulesProtected -or $bad.Count -gt 0 -or ($chk.GetOwner([Security.Principal.SecurityIdentifier]) -ne $__pfAdm)) { throw 'the folder ACL did not take effect' };
  __pfScan $__pfDir
} catch { Write-Output ('[PortableFix] Could not secure the backup folder ' + $__pfDir + ' (administrator rights required) - the action was not run: ' + $_.Exception.Message); exit 1 };
"""

# Collapsed to one line: it travels inside a -Command argument and into
# undo.ps1 unchanged. Constant, so the generated plan text is stable.
HARDEN_PS = " ".join(line.strip() for line in _PS.strip().splitlines()) + " "


def references_backup_dir(text: str) -> bool:
    r"""True when the command can read or write the PortableFix ProgramData
    folder ($env:ProgramData\PortableFix, %ProgramData%\PortableFix, ...)."""
    low = text.lower()
    return "programdata" in low and "portablefix" in low
