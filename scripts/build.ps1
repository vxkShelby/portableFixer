# scripts/build.ps1
# The whole release build as one ordered pipeline. Every step stops the
# build when it fails (exit codes are checked), so a half-built release can
# never sit in Output\ looking finished:
#    1. portablefix/version.py, installer/PortableFix.iss and -Tag agree
#    2. the PyInstaller in use is the one pinned in requirements-build.txt
#    3. PyInstaller -> App\PortableFix.exe
#    4. optional: sign App\PortableFix.exe
#    5. Data\SHA256SUMS - after signing, which changes the exe's hash
#    6. verify_release.py --tree
#    7. build_release_zip.ps1 -> Output\PortableFix-Portable.zip (+ .sha256)
#    8. verify_release.py --zip - stages the zip exactly like the clients do
#    9. ISCC -> Output\PortableFix-Setup.exe
#   10. optional: sign PortableFix-Setup.exe
#
# Release build (from a PowerShell prompt in the repo root):
#   .\scripts\build.ps1 -Tag v1.12.0
# With signing - the script block gets the file to sign:
#   .\scripts\build.ps1 -Tag v1.12.0 -SignCommand { param($File) signtool sign /fd SHA256 /a /tr http://timestamp.digicert.com /td SHA256 $File }
# -Python picks the interpreter that has requirements-build.txt installed
# (default: python).
param(
    [string]$Tag = "",
    [scriptblock]$SignCommand = $null,
    [string]$Python = "python"
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$distStage = "$root\App"
$zipPath = "$root\Output\PortableFix-Portable.zip"

function Invoke-Step([string]$Name, [scriptblock]$Command) {
    Write-Host "==> $Name"
    # Reset first: a step made only of cmdlets never sets it, and must not
    # inherit the previous native command's code.
    $global:LASTEXITCODE = 0
    & $Command
    if ($LASTEXITCODE -ne 0) {
        throw "$Name failed with exit code $LASTEXITCODE"
    }
}

function Invoke-Signing([string]$File) {
    if (-not $SignCommand) { return }
    Invoke-Step "Sign $File" { & $SignCommand $File }
    $signature = Get-AuthenticodeSignature -LiteralPath $File
    if (-not $signature.SignerCertificate) {
        throw "$File carries no signature after the sign command ran"
    }
}

# 1. One version everywhere. A release whose exe reports an older version
# than its tag is offered to its own users as an update forever.
$versionText = Get-Content -LiteralPath "$root\portablefix\version.py" -Raw
if (-not ($versionText -match 'APP_VERSION\s*=\s*"([^"]+)"')) {
    throw "APP_VERSION not found in portablefix\version.py"
}
$appVersion = $Matches[1]
$issText = Get-Content -LiteralPath "$root\installer\PortableFix.iss" -Raw
if (-not ($issText -match '#define\s+MyAppVersion\s+"([^"]+)"')) {
    throw "MyAppVersion not found in installer\PortableFix.iss"
}
$issVersion = $Matches[1]
if ($issVersion -ne $appVersion) {
    throw "Version mismatch: portablefix\version.py says $appVersion, installer\PortableFix.iss says $issVersion"
}
if ($Tag) {
    $tagVersion = $Tag -replace '^v', ''
    if ($tagVersion -ne $appVersion) {
        throw "Version mismatch: tag $Tag, but portablefix\version.py says $appVersion"
    }
} else {
    Write-Warning "No -Tag given: a development build, not checked against a release tag."
}
Write-Host "Building PortableFix $appVersion"

# 2. The pinned PyInstaller: its bootloader is part of what the frozen
# update test (tests.yml, frozen-update-e2e) proved to work.
$pinLine = @(Get-Content -LiteralPath "$root\requirements-build.txt" | Where-Object { $_ -match '^\s*pyinstaller\s*==' })
if ($pinLine.Count -ne 1) {
    throw "requirements-build.txt must pin pyinstaller==<version> exactly once"
}
$pinned = ($pinLine[0] -replace '^\s*pyinstaller\s*==\s*', '').Trim()
$global:LASTEXITCODE = 0
$installed = ""
try {
    $installed = (& $Python -m PyInstaller --version | Out-String).Trim()
} catch {
    $global:LASTEXITCODE = 1
}
if ($LASTEXITCODE -ne 0) {
    throw "Could not run PyInstaller through '$Python' - pip install -r requirements-build.txt, or pass -Python"
}
if ($installed -ne $pinned) {
    throw "PyInstaller $installed is installed, but requirements-build.txt pins $pinned - pip install -r requirements-build.txt"
}

$isccPath = (Get-Command ISCC.exe -EA SilentlyContinue).Source
if (-not $isccPath) {
    foreach ($candidate in @(
        "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
        "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
    )) {
        if (Test-Path $candidate) { $isccPath = $candidate; break }
    }
}
if ((-not $isccPath) -and $Tag) {
    # Checked before anything is built: a release is published with its
    # installer, and a stale Setup.exe left in Output\ by an earlier build
    # must not be uploaded in its place.
    throw "Inno Setup (ISCC.exe) not found - a release build needs it. Install it from https://jrsoftware.org/isinfo.php"
}

# 3. --onefile bundles everything (bootloader + all Python bytecode + deps)
# into a single .exe. This matters for auto-update: swapping just the
# .exe is a complete, correct update. With the old --onedir layout the
# actual app code lived in a separate _internal/PYZ-00.pyz next to a
# thin bootloader .exe, so swapping only the .exe would have left stale
# code running.
# Modules/ and Data/ are deliberately NOT bundled: the app reads them from
# the drive next to App/ (paths.get_base_dir, never sys._MEIPASS), so an
# embedded copy was only extracted to %TEMP% on every launch and never
# read - and Data/ would have baked the build machine's settings.json in.
Invoke-Step "PyInstaller" {
    & $Python -m PyInstaller --onefile --noconsole --noconfirm --distpath $distStage --workpath "$root\build" --specpath "$root\build" `
      --add-data "$root\portablefix.ico;." `
      --icon "$root\portablefix.ico" `
      --name PortableFix `
      "$root\main.py"
}

# 4.
Invoke-Signing "$distStage\PortableFix.exe"

# 5-6. The manifest comes after the last change to App\ - generated before
# signing, it flagged every signed copy as tampered.
Invoke-Step "SHA256SUMS" { & $Python "$root\scripts\generate_sha256sums.py" "$root" }
Invoke-Step "Verify the built tree" { & $Python "$root\scripts\verify_release.py" --tree "$root" }

# 7-8.
Invoke-Step "Release zip" { & "$root\scripts\build_release_zip.ps1" }
Invoke-Step "Verify the release zip" { & $Python "$root\scripts\verify_release.py" --zip "$zipPath" }

# 9-10.
if ($isccPath) {
    Invoke-Step "Inno Setup" { & $isccPath "/DMyAppVersion=$appVersion" "$root\installer\PortableFix.iss" }
    Invoke-Signing "$root\Output\PortableFix-Setup.exe"
    Write-Host "Installer written to $root\Output\PortableFix-Setup.exe"
} else {
    Write-Host "Inno Setup (ISCC.exe) not found - skipped building PortableFix-Setup.exe. Install it from https://jrsoftware.org/isinfo.php to also produce the installer."
}

Write-Host "Build of $appVersion complete. Run PortableFix.cmd from $root to launch."
