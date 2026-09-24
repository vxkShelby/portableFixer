"""The PowerShell script that swaps a staged update into place once the app
has exited.

It is a constant, not generated: the script contains no path and does no
path arithmetic. Every path it touches is precomputed in Python
(update_swap.write_swap_job) and read from an ASCII JSON job file next to the
script - same name, .json instead of .ps1. The script generated before 1.12
pasted install/TEMP paths into its own text, so a path with a typographic
quote (O'Neil typed on a phone keyboard) or "$(" either broke parsing before
line 1 ran or executed as code. Neither can happen to a constant.

It must stay pure ASCII (Windows PowerShell 5.1 reads a BOM-less script in
the ANSI codepage) and parse on 5.1: no ??, ?., ternaries or pwsh-only
cmdlets and parameters. Kept as a Python constant so PyInstaller needs no
--add-data for it.

Exit codes: 0 finished (the outcome is in Data\\update_status.txt), 2 job file
unreadable, 3 blocked by the language mode, 4 the app never exited, 5 a
process to wait for was not visible.
"""

SWAP_SCRIPT = r"""# PortableFix update swap - static text; every path comes from the JSON job
# file next to this script. See portablefix/update_swap_script.py.
$ErrorActionPreference = 'SilentlyContinue'
$ProgressPreference = 'SilentlyContinue'

# Only cmdlets until the language-mode check below: ConstrainedLanguage
# (AppLocker/WDAC) refuses .NET method calls, and the app must still get the
# 'blocked' marker from such a machine instead of waiting for nothing.
$Cfg = $null
try {
    $Cfg = Get-Content -LiteralPath ($PSCommandPath -replace '\.ps1$', '.json') -Raw -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop
} catch {
    $Cfg = $null
}
if ((-not $Cfg) -or ($Cfg.Schema -ne 1)) { exit 2 }
# ConvertFrom-Json on 5.1 can hand back an empty or one-element array as
# $null or a bare value, and @($null) is a list holding one $null - so every
# list is normalised once, here.
$WaitPids = @($Cfg.Pids | Where-Object { $null -ne $_ })
$Folders = @($Cfg.Folders | Where-Object { $null -ne $_ })
$DataCopies = @($Cfg.DataCopies | Where-Object { $null -ne $_ })
$RootCopies = @($Cfg.RootCopies | Where-Object { $null -ne $_ })
# Without a process to wait for, nothing would stop the swap from renaming
# folders under a running app.
if (($WaitPids.Count -eq 0) -or ($Folders.Count -eq 0)) { exit 2 }

function Log([string]$Message) {
    Add-Content -LiteralPath $Cfg.LogFile -Value ((Get-Date -Format o) + ' ' + $Message) -Encoding UTF8
}

# The app polls for this file and quits only once it reads a complete line
# (Set-Content ends it with a newline), so a half-written marker never counts.
function Write-Marker([string]$Text) {
    Set-Content -LiteralPath $Cfg.MarkerFile -Value $Text -Encoding ASCII
}

# The app that started this script has quit by the time most of these are
# written - the next launch reads the file (updater.consume_update_status).
function Set-UpdateStatus([string]$Status) {
    Set-Content -LiteralPath $Cfg.StatusFile -Value $Status -Encoding ASCII
    Log ('status: ' + $Status)
}

$mode = [string]$ExecutionContext.SessionState.LanguageMode
Log ('update swap started: powershell ' + $PSVersionTable.PSVersion + ', language mode ' + $mode + ', waiting for pids ' + ($WaitPids -join ','))
if ($mode -ne 'FullLanguage') {
    Log 'ABORT: PowerShell runs in a restricted language mode here (AppLocker/WDAC policy) - nothing was changed'
    Write-Marker ('blocked ' + $mode)
    exit 3
}

# Lets a PortableFix started by hand during the swap tell the user to wait
# instead of locking the files that are being replaced.
$updateMutex = $null
try {
    $updateMutex = New-Object System.Threading.Mutex -ArgumentList $false, ([string]$Cfg.MutexName)
} catch {
    Log ('could not create the update mutex: ' + $_.Exception.Message)
}

# Both onefile processes are pinned while provably alive (the app is blocked
# on the marker right now): the bootloader parent keeps App\PortableFix.exe
# mapped while it deletes its _MEI folder, after the Python child is gone.
# Holding a handle also means a recycled PID can never be mistaken for them.
$procs = @()
foreach ($id in $WaitPids) {
    $p = Get-Process -Id ([int]$id) -ErrorAction SilentlyContinue
    if ($p) {
        try { $null = $p.Handle } catch { Log ('could not open pid ' + $id + ': ' + $_.Exception.Message) }
        $procs += $p
    } else {
        # The app is blocked on the marker, so its processes must be alive.
        # Not seeing one means this script cannot tell when it is safe to
        # start renaming - refuse and let the app report it, still running.
        Log ('ABORT: pid ' + $id + ' is not visible - nothing was changed')
        Write-Marker ('error pid ' + $id + ' is not visible to the updater')
        exit 5
    }
}
Write-Marker ('ready ' + $PID + ' ' + $PSVersionTable.PSVersion)

function Test-Exited($Proc) {
    try {
        return [bool]$Proc.HasExited
    } catch {
        return (-not (Get-Process -Id $Proc.Id -ErrorAction SilentlyContinue))
    }
}

# The app may legitimately take minutes to close (a winget update it waits
# for), far longer than the 30 s the generated script used to allow.
# Measured with a Stopwatch: wall-clock time can jump (DST, NTP) mid-wait.
$waited = [System.Diagnostics.Stopwatch]::StartNew()
$pending = @($procs)
while ($pending.Count -gt 0) {
    $still = @()
    foreach ($p in $pending) {
        if (Test-Exited $p) { Log ('pid ' + $p.Id + ' exited') } else { $still += $p }
    }
    $pending = $still
    if ($pending.Count -eq 0) { break }
    if ($waited.Elapsed.TotalSeconds -gt [int]$Cfg.MaxWaitSec) {
        # Nothing was touched yet, and the old app is still the one running:
        # relaunching would only start a second instance that loses to the
        # single-instance mutex and quits, so there is no relaunch either.
        Log ('ABORT: the app did not exit within ' + $Cfg.MaxWaitSec + ' s - nothing was changed')
        Set-UpdateStatus 'aborted'
        exit 4
    }
    Start-Sleep -Milliseconds ([int]$Cfg.PollMs)
}
Log 'the app exited, swapping'

# A rename, retried: AV scanning the exe that just exited (or the handle
# the bootloader just released) holds it for a moment. Never moves into an
# existing destination - that would nest the folder instead of replacing it.
# Success means the source is gone AND the destination exists.
function Move-WithRetry([string]$Source, [string]$Destination) {
    if (-not (Test-Path -LiteralPath $Source)) { Log ('move skipped, source missing: ' + $Source); return $false }
    if (Test-Entry $Destination) { Log ('move refused, destination exists: ' + $Destination); return $false }
    $lastError = ''
    for ($i = 0; $i -lt [int]$Cfg.RenameTries; $i++) {
        try {
            [System.IO.Directory]::Move($Source, $Destination)
        } catch {
            $lastError = $_.Exception.Message
        }
        if ((-not (Test-Path -LiteralPath $Source)) -and (Test-Path -LiteralPath $Destination)) { return $true }
        if (Test-Entry $Destination) { Log ('move left both ' + $Source + ' and ' + $Destination); return $false }
        Start-Sleep -Milliseconds ([int]$Cfg.RenameDelayMs)
    }
    Log ('move FAILED after ' + $Cfg.RenameTries + ' tries: ' + $Source + ' -> ' + $Destination + ': ' + $lastError)
    return $false
}

# True for anything at $Path, a dangling link included. Whether Test-Path
# reports a dangling link differs between PowerShell versions and link
# kinds (it resolves the path; pwsh 7 does report one), so every check
# that decides whether a stale entry is in the way uses this instead:
# GetAttributes reads the entry's own attributes.
function Test-Entry([string]$Path) {
    try {
        $null = [System.IO.File]::GetAttributes($Path)
        return $true
    } catch {
        return $false
    }
}

# Deletes a folder tree without ever following a link. Windows PowerShell
# 5.1's Remove-Item -Recurse walks INTO directory junctions and symlinks, so
# a link planted inside App.old (the install folder is often user-writable,
# e.g. on a USB stick) would have this updater delete whatever it points at.
# The same walk as Remove-PfSafe in Modules/m02_cleanup/actions.yaml (see
# tests/test_safe_delete.py): an explicit stack, not recursion (call depth);
# a reparse point is deleted as the link only (non-recursive Delete removes
# the link, never the target) and never descended into; read-only is
# cleared only on real entries. Returns how many entries could not be
# deleted; a vanished entry is not a failure.
function Remove-TreeNoFollow([string]$Path) {
    $n = 0
    $ro = [System.IO.FileAttributes]::ReadOnly
    $rp = [System.IO.FileAttributes]::ReparsePoint
    $dir = [System.IO.FileAttributes]::Directory
    $dirs = New-Object System.Collections.Generic.List[string]
    $todo = New-Object System.Collections.Generic.Stack[string]
    $todo.Push($Path)
    while ($todo.Count -gt 0) {
        $p = $todo.Pop()
        try {
            $a = [System.IO.File]::GetAttributes($p)
        } catch [System.IO.FileNotFoundException], [System.IO.DirectoryNotFoundException] {
            continue
        } catch {
            $n++
            continue
        }
        if (($a -band $dir) -and -not ($a -band $rp)) {
            $dirs.Add($p)
            try {
                foreach ($c in [System.IO.Directory]::GetFileSystemEntries($p)) { $todo.Push($c) }
            } catch {
                $n++
            }
            continue
        }
        try {
            if ($a -band $rp) {
                if ($a -band $dir) { [System.IO.Directory]::Delete($p) } else { [System.IO.File]::Delete($p) }
            } else {
                if ($a -band $ro) { [System.IO.File]::SetAttributes($p, ($a -bxor $ro)) }
                [System.IO.File]::Delete($p)
            }
        } catch {
            $n++
        }
    }
    # Deepest first: a folder is only empty once its children are gone.
    for ($k = $dirs.Count - 1; $k -ge 0; $k--) {
        $d = $dirs[$k]
        try {
            $a = [System.IO.File]::GetAttributes($d)
            if (($a -band $ro) -and -not ($a -band $rp)) { [System.IO.File]::SetAttributes($d, ($a -bxor $ro)) }
            [System.IO.Directory]::Delete($d)
        } catch [System.IO.FileNotFoundException], [System.IO.DirectoryNotFoundException] {
        } catch {
            # Only "not empty because a child failed" - already counted.
            $left = 1
            try { $left = @([System.IO.Directory]::GetFileSystemEntries($d)).Count } catch { }
            if ($left -eq 0) { $n++ }
        }
    }
    return $n
}

function Remove-WithRetry([string]$Target) {
    $left = 0
    for ($i = 0; $i -lt [int]$Cfg.RenameTries; $i++) {
        if (-not (Test-Entry $Target)) { return $true }
        $left = Remove-TreeNoFollow $Target
        if (-not (Test-Entry $Target)) { return $true }
        Start-Sleep -Milliseconds ([int]$Cfg.RenameDelayMs)
    }
    Log ('could not remove ' + $Target + ' (' + $left + ' item(s) left)')
    return $false
}

# Puts back any X.old whose live X is missing: a swap interrupted between
# "move old away" and "move new in" (USB stick pulled), or a backup that
# only got part of the way before a locked folder stopped it.
function Restore-Backups {
    foreach ($f in $Folders) {
        if ((Test-Path -LiteralPath $f.Backup) -and (-not (Test-Path -LiteralPath $f.Live))) {
            $restored = Move-WithRetry -Source $f.Backup -Destination $f.Live
            Log ('restored ' + $f.Live + ' from backup: ' + $restored)
        }
    }
}

function Get-Sha256([string]$Path) {
    try {
        $sha = [System.Security.Cryptography.SHA256]::Create()
        try {
            return [System.BitConverter]::ToString($sha.ComputeHash([System.IO.File]::ReadAllBytes($Path)))
        } finally {
            $sha.Dispose()
        }
    } catch {
        Log ('hash of ' + $Path + ' failed: ' + $_.Exception.Message)
        return $null
    }
}

function Copy-File([string]$Source, [string]$Destination) {
    if (-not (Test-Path -LiteralPath $Source -PathType Leaf)) { return }
    try {
        [System.IO.File]::Copy($Source, $Destination, $true)
    } catch {
        Log ('copy FAILED: ' + $Source + ' -> ' + $Destination + ': ' + $_.Exception.Message)
    }
}

# A stale SHA256SUMS next to a new exe is a false tamper warning on every
# launch, so this copy is verified and retried (AV often holds a file that
# was just written) instead of trusted.
function Update-Sums {
    if (-not (Test-Path -LiteralPath $Cfg.SumsSrc -PathType Leaf)) { Log 'the package has no SHA256SUMS'; return $false }
    $want = Get-Sha256 $Cfg.SumsSrc
    # Check 0 is the DataCopies copy; each of the SumsTries re-copies is
    # checked too, the last one included.
    for ($i = 0; $i -le [int]$Cfg.SumsTries; $i++) {
        if ($i -gt 0) {
            Start-Sleep -Milliseconds ([int]$Cfg.SumsDelayMs)
            Copy-File -Source $Cfg.SumsSrc -Destination $Cfg.SumsDst
        }
        $got = Get-Sha256 $Cfg.SumsDst
        if ($want -and ($got -eq $want)) { return $true }
        Log ('SHA256SUMS check ' + $i + ': want=' + $want + ' got=' + $got)
    }
    return $false
}

# Leftovers of an earlier interrupted swap: restore what is missing, then
# drop stale backups whose live folder exists.
Restore-Backups
foreach ($f in $Folders) {
    if ((Test-Entry $f.Backup) -and (Test-Path -LiteralPath $f.Live)) {
        $null = Remove-WithRetry $f.Backup
    }
    $null = Remove-WithRetry $f.Discard
}
$inTheWay = @($Folders | Where-Object { Test-Entry $_.Backup })
# The app verified the stage before handing off; this only catches it having
# vanished since (deleted by hand, stick swapped), before anything is moved.
$stageMissing = @($Folders | Where-Object { -not (Test-Path -LiteralPath $_.Staged) })

$outcome = 'aborted'
# Set only once the new folders are in place: until then an X.old may be
# the only good copy of X.
$dropBackups = $false
if ($inTheWay.Count -gt 0) {
    Log 'ABORT: a stale backup folder could not be removed - nothing was changed'
} elseif ($stageMissing.Count -gt 0) {
    Log 'ABORT: the staged update is gone - nothing was changed'
} else {
    # Written before the first rename: if the stick is pulled mid-swap, the
    # next launch finds 'in_progress' and main.py/PortableFix.cmd put any
    # stranded X.old back.
    Set-UpdateStatus 'in_progress'
    $backupOk = $true
    foreach ($f in $Folders) {
        if (Test-Path -LiteralPath $f.Live) {
            if (-not (Move-WithRetry -Source $f.Live -Destination $f.Backup)) { $backupOk = $false; break }
        }
    }
    if (-not $backupOk) {
        Log 'ABORT: an old folder could not be moved aside (locked?) - putting back what was moved'
        Restore-Backups
    } else {
        $moveOk = $true
        foreach ($f in $Folders) {
            if (-not (Move-WithRetry -Source $f.Staged -Destination $f.Live)) { $moveOk = $false; break }
        }
        $verified = $moveOk -and (Test-Path -LiteralPath $Cfg.AppExe -PathType Leaf)
        foreach ($f in $Folders) {
            if (-not (Get-ChildItem -LiteralPath $f.Live -Force | Select-Object -First 1)) { $verified = $false }
        }
        if ($verified) {
            Log 'new folders in place, installing Data files and the launcher'
            # Only once the new folders are verified: copied earlier, a
            # rollback put the OLD exe next to the NEW manifest. An allowlist,
            # never the whole Data folder: settings.json and the status file
            # belong to the user's install, not to the package.
            foreach ($c in $DataCopies) { Copy-File -Source $c.Src -Destination $c.Dst }
            foreach ($c in $RootCopies) { Copy-File -Source $c.Src -Destination $c.Dst }
            $sumsOk = Update-Sums
            $dropBackups = $true
            if ($sumsOk) {
                $outcome = 'ok'
            } else {
                Log 'Data\SHA256SUMS could not be updated - the integrity check will flag the new files'
                $outcome = 'ok_sums_stale'
            }
        } else {
            Log 'swap FAILED verification, rolling back'
            $restoredAll = $true
            foreach ($f in $Folders) {
                if (Test-Path -LiteralPath $f.Backup) {
                    # Renamed aside, not deleted in place: a recursive delete
                    # that stops halfway (AV holding the new exe) would leave
                    # a half-empty folder where the old one must go back.
                    if ((Test-Path -LiteralPath $f.Live) -and (-not (Move-WithRetry -Source $f.Live -Destination $f.Discard))) {
                        $null = Remove-WithRetry $f.Live
                    }
                    $back = Move-WithRetry -Source $f.Backup -Destination $f.Live
                    Log ('rolled back ' + $f.Live + ': ' + $back)
                    if (-not $back) { $restoredAll = $false }
                }
            }
            # 'rolled_back' tells the user the old version was kept - not
            # true while a new folder is stuck where an old one belongs.
            if ($restoredAll) {
                $outcome = 'rolled_back'
            } else {
                Log 'ROLLBACK INCOMPLETE: the install now mixes old and new folders; the old ones are left as *.old'
                $outcome = 'rollback_failed'
            }
        }
    }
}
# The final status goes out before any cleanup: a backup AV keeps open can
# cost RenameTries x RenameDelayMs each, and an updater killed during that
# (logoff) must not leave 'in_progress' behind for a finished update.
Set-UpdateStatus $outcome

# The old process has exited in every outcome that gets here, so the app is
# relaunched even after an abort or a rollback - otherwise the user is left
# with nothing running at all.
if ($updateMutex) {
    try { $updateMutex.Close() } catch { }
}
# A relaunched onefile exe that inherits the dead app's _PYI_* variables
# reuses its deleted _MEI folder and dies with "Failed to load Python DLL".
foreach ($name in @([System.Environment]::GetEnvironmentVariables().Keys)) {
    if ((([string]$name) -like '_PYI_*') -or (([string]$name) -eq '_MEIPASS2')) {
        [System.Environment]::SetEnvironmentVariable([string]$name, $null)
    }
}
[System.Environment]::SetEnvironmentVariable('PYINSTALLER_RESET_ENVIRONMENT', '1')
# ProcessStartInfo, not Start-Process: -FilePath treats [ ] in the path as
# wildcards on 5.1. The exe directly, not PortableFix.cmd, so no console
# window; WorkingDirectory is the install root, never App\ (which must stay
# renameable for the next update).
try {
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = [string]$Cfg.AppExe
    $psi.Arguments = '--post-update'
    $psi.WorkingDirectory = [string]$Cfg.InstallDir
    $psi.UseShellExecute = $true
    $started = [System.Diagnostics.Process]::Start($psi)
    if ($started) { Log ('relaunched ' + $Cfg.AppExe + ' as pid ' + $started.Id) } else { Log ('relaunched ' + $Cfg.AppExe) }
} catch {
    Log ('relaunch FAILED for ' + $Cfg.AppExe + ': ' + $_.Exception.Message)
}
# Cleanup that must not delay the relaunch. An X.old that survives it is
# harmless: the next swap drops stale backups before it starts.
if ($dropBackups) {
    foreach ($f in $Folders) { $null = Remove-WithRetry $f.Backup }
}
foreach ($f in $Folders) { $null = Remove-WithRetry $f.Discard }
$null = Remove-WithRetry $Cfg.StageDir
Log 'update swap finished'
exit 0
"""
