# Second-opinion scanners (research G28), shared by every action of
# Modules/m24_scanners/actions.yaml. The action command dot-sources this file
# and calls Invoke-PfScanner; PortableFix sets three variables first
# (portablefix/action_service.py scanner_variables):
#
#   $__pfScannersLib  this file
#   $__pfScannerCache <state dir>\ScannerCache - downloads, and copies a
#                     technician put there by hand
#   $__pfJobDir       <state dir>\Backups\<run id>\scanners\<action id> - the
#                     tool's working folder; logs\ under it goes to the
#                     client handoff ZIP
#
# Every run: find the tool in the cache or download it, copy it to a private
# temp folder, check the COPY's Authenticode signature against the pinned
# signer names below, run the copy, copy its log to logs\, print a PFJSON
# finding (research G02) when the log says clearly what was found.
#
# Exit codes: 0 ran, 3 the tool itself failed, 4 unsupported CPU,
# 5 signature check failed, 6 download failed. Windows PowerShell 5.1.

$PfScannerTools = @{
    adwcleaner = @{
        Name = 'Malwarebytes AdwCleaner'; File = 'adwcleaner.exe'
        Url = @{ any = 'https://adwcleaner.malwarebytes.com/adwcleaner?channel=release' }
        # ponytail: signer names pinned from the vendors' current releases;
        # a vendor re-brand needs a catalog update (the action then refuses).
        Signers = @('Malwarebytes Inc', 'Malwarebytes Inc.', 'Malwarebytes Corporation')
        Clean = 'adwcleaner_clean'
    }
    msert = @{
        Name = 'Microsoft Safety Scanner'; File = 'msert.exe'
        Url = @{ AMD64 = 'https://go.microsoft.com/fwlink/?LinkId=212732'; x86 = 'https://go.microsoft.com/fwlink/?LinkId=212733' }
        Signers = @('Microsoft Corporation')
        MaxAgeDays = 10
        Clean = 'msert_clean'
    }
    kvrt = @{
        Name = 'Kaspersky Virus Removal Tool'; File = 'KVRT.exe'
        Url = @{ any = 'https://devbuilds.s.kaspersky-labs.com/devbuilds/KVRT/latest/full/KVRT.exe' }
        Signers = @('AO Kaspersky Lab', 'Kaspersky Lab JSC', 'Kaspersky Lab')
        Clean = 'kvrt_clean'
    }
}

function Get-PfScannerArch {
    if ($env:PROCESSOR_ARCHITEW6432) { return [string]$env:PROCESSOR_ARCHITEW6432 }
    return [string]$env:PROCESSOR_ARCHITECTURE
}

function Get-PfScannerUrl($tool) {
    if ($tool.Url.ContainsKey('any')) { return $tool.Url['any'] }
    $arch = Get-PfScannerArch
    if ($tool.Url.ContainsKey($arch)) { return $tool.Url[$arch] }
    return $null
}

function Test-PfScannerSignature($tool, [string]$path) {
    # Status Valid = the file is unmodified and chains to a trusted root; the
    # signer's name must then be one of the pinned ones - a valid signature
    # of somebody else is refused the same as none.
    $sig = Get-AuthenticodeSignature -LiteralPath $path -EA Stop
    $status = [string]$sig.Status
    $signer = ''
    if ($sig.SignerCertificate) {
        $signer = [string]$sig.SignerCertificate.GetNameInfo([Security.Cryptography.X509Certificates.X509NameType]::SimpleName, $false)
    }
    $ok = ($status -eq 'Valid') -and ($tool.Signers -contains $signer)
    return [pscustomobject]@{ Ok = $ok; Status = $status; Signer = $signer }
}

function Test-PfScannerCached($key) {
    $tool = $PfScannerTools[$key]
    $cached = Join-Path $__pfScannerCache $tool.File
    if (-not (Test-Path -LiteralPath $cached -PathType Leaf)) { return $false }
    # Microsoft Safety Scanner stops working 10 days after it was downloaded.
    if ($tool.ContainsKey('MaxAgeDays') -and (Get-Item -LiteralPath $cached).LastWriteTime -lt (Get-Date).AddDays(-$tool.MaxAgeDays)) { return $false }
    return $true
}

function Get-PfScannerCopy($key) {
    # Sets $global:PfScannerExe to the verified copy to run, in a fresh temp
    # folder, or exits. (A global, not a return value: everything a function
    # writes would become part of what it returns.)
    $tool = $PfScannerTools[$key]
    $cached = Join-Path $__pfScannerCache $tool.File
    $work = Join-Path $env:TEMP ('PortableFix-' + $key + '-' + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Force -Path $work -EA Stop | Out-Null
    $copy = Join-Path $work $tool.File
    $fromCache = Test-PfScannerCached $key
    if ($fromCache) {
        Write-Output ('Using the copy in the scanner cache: ' + $cached)
        Copy-Item -LiteralPath $cached -Destination $copy -Force -EA Stop
    } else {
        $url = Get-PfScannerUrl $tool
        if (-not $url) { Write-Output ($tool.Name + ' has no download for this CPU (' + (Get-PfScannerArch) + '). Nothing was run.'); exit 4 }
        Write-Output ('Downloading ' + $tool.Name + ' from ' + $url + ' ...')
        try {
            [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
            $ProgressPreference = 'SilentlyContinue'
            Invoke-WebRequest -Uri $url -OutFile $copy -UseBasicParsing -EA Stop
        } catch {
            Write-Output ('Download failed (' + $_.FullyQualifiedErrorId + '): ' + $_.Exception.Message + ' - no internet? Put ' + $tool.File + ' into ' + $__pfScannerCache + ' by hand and run the action again. Nothing was run.')
            Remove-Item -LiteralPath $work -Recurse -Force -EA SilentlyContinue
            exit 6
        }
    }
    $check = Test-PfScannerSignature $tool $copy
    if (-not $check.Ok) {
        Write-Output ('REFUSED: the Authenticode signature of ' + $tool.File + ' is not the expected one (status: ' + $check.Status + ', signer: "' + $check.Signer + '", expected one of: ' + ($tool.Signers -join ', ') + '). The file was not run.')
        if ($fromCache) { Write-Output ('Replace or delete ' + $cached + ' - it is not the genuine tool.') }
        Remove-Item -LiteralPath $work -Recurse -Force -EA SilentlyContinue
        exit 5
    }
    Write-Output ('Signature OK: ' + $check.Signer + '.')
    if (-not $fromCache) {
        # Best effort: a read-only stick just downloads again next time.
        try {
            New-Item -ItemType Directory -Force -Path $__pfScannerCache -EA Stop | Out-Null
            Copy-Item -LiteralPath $copy -Destination $cached -Force -EA Stop
        } catch { Write-Output ('(Could not keep a copy in ' + $__pfScannerCache + ': ' + $_.Exception.Message + ')') }
    }
    $global:PfScannerExe = $copy
}

function Invoke-PfScannerProcess([string]$exe, [string[]]$arguments, [string]$name, [switch]$Interactive) {
    # Runs the tool and prints a heartbeat every minute, so the executor's
    # inactivity watchdog knows a long scan is alive. Sets $global:PfScannerExitCode.
    Write-Output ('Running: ' + $name + ' ' + ($arguments -join ' '))
    $start = Get-Date
    if ($Interactive) {
        $p = Start-Process -FilePath $exe -ArgumentList $arguments -PassThru -EA Stop
    } else {
        $p = Start-Process -FilePath $exe -ArgumentList $arguments -PassThru -WindowStyle Hidden -EA Stop
    }
    while (-not $p.WaitForExit(60000)) {
        Write-Output ($name + ' still running - ' + [int]((Get-Date) - $start).TotalMinutes + ' min elapsed...')
    }
    $global:PfScannerExitCode = [int]$p.ExitCode
}

function Write-PfScannerFinding($key, [string]$severity, [string]$sk, [string]$en) {
    $tool = $PfScannerTools[$key]
    $fix = @()
    if ($severity -ne 'ok') { $fix = @($tool.Clean) }
    Write-Output ('PFJSON:' + (ConvertTo-Json -Compress -Depth 4 -InputObject @{ findings = @([ordered]@{
        id = ('security.scanner.' + $key); severity = $severity; area = 'security'; msg_sk = $sk; msg_en = $en; fix = $fix
    }) }))
}

# --- log parsers: text in, @{ State = ok|attention|unknown; Count; Names } ---
# The logs these read are the tools' own English report files, not
# localized console text.

function Read-PfAdwCleanerLog([string]$text) {
    $m = [regex]::Match($text, '(?im)^#\s*Detected:\s*(\d+)')
    if (-not $m.Success) { return @{ State = 'unknown'; Count = 0; Names = @() } }
    $count = [int]$m.Groups[1].Value
    $names = @([regex]::Matches($text, '(?i)\b((?:PUP|PUM|Adware|Trojan|Malware|Hijack|Rootkit)\.[\w.\-]+)') | ForEach-Object { $_.Groups[1].Value } | Select-Object -Unique)
    return @{ State = $(if ($count -gt 0) { 'attention' } else { 'ok' }); Count = $count; Names = $names }
}

function Read-PfMsertLog([string]$text) {
    $names = @([regex]::Matches($text, '(?im)^\s*Threat Detected:\s*([^,\r\n]+)') | ForEach-Object { $_.Groups[1].Value.Trim() } | Select-Object -Unique)
    if ($names.Count -gt 0) { return @{ State = 'attention'; Count = $names.Count; Names = $names } }
    if ([regex]::IsMatch($text, '(?im)^\s*No infection found')) { return @{ State = 'ok'; Count = 0; Names = @() } }
    return @{ State = 'unknown'; Count = 0; Names = @() }
}

function Read-PfKvrtLog([string]$text) {
    # ponytail: KVRT's report layout is not documented; only an explicit
    # "Detected: N" / "Threats found: N" counts, anything else is "unknown"
    # and the technician reads the raw report in the handoff ZIP.
    $m = [regex]::Match($text, '(?im)^\s*(?:Detected|Threats?\s+(?:found|detected))\s*:\s*(\d+)')
    if (-not $m.Success) { return @{ State = 'unknown'; Count = 0; Names = @() } }
    $count = [int]$m.Groups[1].Value
    return @{ State = $(if ($count -gt 0) { 'attention' } else { 'ok' }); Count = $count; Names = @() }
}

function Save-PfScannerLogs([string[]]$files, [string]$logs) {
    New-Item -ItemType Directory -Force -Path $logs -EA Stop | Out-Null
    $text = ''
    foreach ($f in @($files | Where-Object { $_ -and (Test-Path -LiteralPath $_ -PathType Leaf) })) {
        Copy-Item -LiteralPath $f -Destination (Join-Path $logs (Split-Path $f -Leaf)) -Force -EA SilentlyContinue
        $text += (Get-Content -LiteralPath $f -Raw -EA SilentlyContinue) + "`n"
    }
    return $text
}

function Get-PfScannerArgs([string]$Key, [string]$Mode) {
    if ($Key -eq 'adwcleaner') {
        return @('/eula', $(if ($Mode -eq 'clean') { '/clean' } else { '/scan' }), '/noreboot', '/path', ('"' + $__pfJobDir + '"'))
    }
    if ($Key -eq 'msert') {
        # /N = detect only. Without it, /Q removes what it finds.
        return $(if ($Mode -eq 'clean') { @('/Q') } else { @('/N', '/Q') })
    }
    $kvrtArgs = @('-accepteula', '-dontencrypt', '-d', ('"' + (Join-Path $__pfJobDir 'kvrt_data') + '"'))
    if ($Mode -eq 'clean') { $kvrtArgs += @('-silent', '-processlevel', '2') }
    return $kvrtArgs
}

function Show-PfScannerPlan([string]$Key, [ValidateSet('scan', 'clean')][string]$Mode) {
    # The preview: reads nothing but the cache folder, downloads and runs nothing.
    $tool = $PfScannerTools[$Key]
    $cached = Join-Path $__pfScannerCache $tool.File
    if (Test-PfScannerCached $Key) {
        Write-Output ('Would use the copy in the scanner cache: ' + $cached)
    } else {
        $url = Get-PfScannerUrl $tool
        if (-not $url -or ($Key -eq 'msert' -and (Get-PfScannerArch) -eq 'ARM64')) { Write-Output ('Would refuse: ' + $tool.Name + ' has no build for this CPU (' + (Get-PfScannerArch) + ').'); return }
        Write-Output ('Would download ' + $tool.Name + ' from ' + $url + ' and keep a copy in ' + $__pfScannerCache)
    }
    Write-Output ('Would run it only if its Authenticode signature is valid and signed by: ' + ($tool.Signers -join ' / '))
    Write-Output ('Would run: ' + $tool.File + ' ' + ((Get-PfScannerArgs $Key $Mode) -join ' '))
    if ($Mode -eq 'clean') { Write-Output 'This REMOVES what the tool detects (quarantined where the tool supports it).' } else { Write-Output 'Scan only - nothing is removed.' }
}

function Invoke-PfScanner([string]$Key, [ValidateSet('scan', 'clean')][string]$Mode) {
    if (-not $__pfScannerCache -or -not $__pfJobDir) {
        Write-Output 'PortableFix did not pass the scanner folders to this command - nothing was run.'; exit 3
    }
    $tool = $PfScannerTools[$Key]
    if ($Key -eq 'msert' -and (Get-PfScannerArch) -eq 'ARM64') {
        Write-Output 'Microsoft Safety Scanner has no ARM64 build. Nothing was run.'; exit 4
    }
    New-Item -ItemType Directory -Force -Path $__pfJobDir -EA Stop | Out-Null
    $logs = Join-Path $__pfJobDir 'logs'
    Get-PfScannerCopy $Key
    $exe = $global:PfScannerExe
    $code = 0
    $text = ''
    $toolArgs = Get-PfScannerArgs $Key $Mode
    try {
        if ($Key -eq 'adwcleaner') {
            Invoke-PfScannerProcess $exe $toolArgs $tool.Name
            $files = @(Get-ChildItem -LiteralPath (Join-Path $__pfJobDir 'AdwCleaner\Logs') -Filter '*.txt' -EA SilentlyContinue | Sort-Object LastWriteTime | ForEach-Object { $_.FullName })
            $text = Save-PfScannerLogs @($files | Select-Object -Last 1) $logs
            $parsed = Read-PfAdwCleanerLog $text
        } elseif ($Key -eq 'msert') {
            # msert.log is appended by every run - only this run's part counts.
            $log = $(if ($__pfMsertLog) { $__pfMsertLog } else { Join-Path $env:SystemRoot 'debug\msert.log' })
            $before = 0
            if (Test-Path -LiteralPath $log) { $before = ([string](Get-Content -LiteralPath $log -Raw -EA SilentlyContinue)).Length }
            Invoke-PfScannerProcess $exe $toolArgs $tool.Name
            $all = [string](Get-Content -LiteralPath $log -Raw -EA SilentlyContinue)
            if ($all.Length -lt $before) { $before = 0 }
            $text = $all.Substring($before)
            New-Item -ItemType Directory -Force -Path $logs -EA Stop | Out-Null
            Set-Content -LiteralPath (Join-Path $logs 'msert.log') -Value $text -Encoding UTF8 -EA SilentlyContinue
            $parsed = Read-PfMsertLog $text
        } else {
            # Scan: KVRT's own window - PortableFix never tells it to
            # neutralize anything; the technician chooses Skip there.
            Invoke-PfScannerProcess $exe $toolArgs $tool.Name -Interactive:($Mode -eq 'scan')
            $files = @(Get-ChildItem -LiteralPath (Join-Path $__pfJobDir 'kvrt_data\Reports') -File -Recurse -EA SilentlyContinue | ForEach-Object { $_.FullName })
            $text = Save-PfScannerLogs $files $logs
            $parsed = Read-PfKvrtLog $text
        }
        $code = $global:PfScannerExitCode
    } finally {
        Remove-Item -LiteralPath (Split-Path $exe) -Recurse -Force -EA SilentlyContinue
    }
    Write-Output ($tool.Name + ' finished with exit code ' + $code + '. Raw log: ' + $logs)
    if (-not ([string]$text).Trim()) {
        Write-Output ('No log from ' + $tool.Name + ' was found - it did not finish its ' + $Mode + '.')
        exit 3
    }
    if ($Mode -eq 'clean') {
        # No finding: whether the removal worked is what the next scan says.
        Write-Output ('Cleaning done. Run the "' + $tool.Name + '" scan again to confirm; a restart may be needed to finish the removal.')
        return
    }
    $list = $(if ($parsed.Names.Count) { ': ' + (($parsed.Names | Select-Object -First 5) -join ', ') } else { '' })
    if ($parsed.State -eq 'attention') {
        Write-Output ('RESULT: ' + $parsed.Count + ' detection(s)' + $list)
        Write-PfScannerFinding $Key 'attention' ($tool.Name + ' našiel ' + $parsed.Count + ' hrozb(y)' + $list + '.') ($tool.Name + ' found ' + $parsed.Count + ' detection(s)' + $list + '.')
    } elseif ($parsed.State -eq 'ok') {
        Write-Output 'RESULT: nothing found.'
        Write-PfScannerFinding $Key 'ok' ($tool.Name + ' nič nenašiel.') ($tool.Name + ' found nothing.')
    } else {
        Write-Output ('RESULT: could not read the result from the log - open it: ' + $logs)
    }
}
