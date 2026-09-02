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

Write-Host "Build complete. Run PortableFix.cmd from $root to launch."
