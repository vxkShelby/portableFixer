# scripts/build.ps1
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$distStage = "$root\App"

# --onefile bundles everything (bootloader + all Python bytecode + deps)
# into a single .exe. This matters for auto-update: swapping just the
# .exe is a complete, correct update. With the old --onedir layout the
# actual app code lived in a separate _internal/PYZ-00.pyz next to a
# thin bootloader .exe, so swapping only the .exe would have left stale
# code running.
pyinstaller --onefile --noconsole --noconfirm --distpath $distStage --workpath "$root\build" --specpath "$root\build" `
  --add-data "$root\Modules;Modules" `
  --add-data "$root\Data;Data" `
  --add-data "$root\portablefix.ico;." `
  --icon "$root\portablefix.ico" `
  --name PortableFix `
  "$root\main.py"

if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller failed with exit code $LASTEXITCODE"
}

py "$root\scripts\generate_sha256sums.py" "$root"

& "$root\scripts\build_release_zip.ps1"

$iscc = Get-Command ISCC.exe -EA SilentlyContinue
if (-not $iscc) {
    foreach ($candidate in @(
        "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
        "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
    )) {
        if (Test-Path $candidate) { $iscc = Get-Item $candidate; break }
    }
}
if ($iscc) {
    & $iscc.Path "$root\installer\PortableFix.iss"
    if ($LASTEXITCODE -ne 0) {
        throw "Inno Setup compiler failed with exit code $LASTEXITCODE"
    }
    Write-Host "Installer written to $root\Output\PortableFix-Setup.exe"
} else {
    Write-Host "Inno Setup (ISCC.exe) not found - skipped building PortableFix-Setup.exe. Install it from https://jrsoftware.org/isinfo.php to also produce the installer."
}

Write-Host "Build complete. Run PortableFix.cmd from $root to launch."
