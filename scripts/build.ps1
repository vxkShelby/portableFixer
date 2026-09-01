# scripts/build.ps1
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$distStage = "$root\App"

# PyInstaller onedir always creates <distpath>/<name>/<name>.exe (plus _internal/)
# -- it never drops the exe directly into distpath. With --distpath "$root\App"
# and --name PortableFix that lands the build at App/PortableFix/PortableFix.exe,
# one level deeper than the project's directory-layout spec (App/PortableFix.exe
# directly). We flatten that nested "PortableFix" folder up into App/ afterward.
#
# Note: distpath must NOT be "$root" with --name PortableFix -- Windows paths are
# case-insensitive, so a "PortableFix" output folder at the project root collides
# with the existing "portablefix" source package folder and PyInstaller refuses
# to write into it (non-empty dir error).
pyinstaller --onedir --noconsole --noconfirm --distpath $distStage --workpath "$root\build" --specpath "$root\build" `
  --add-data "$root\Modules;Modules" `
  --add-data "$root\Data;Data" `
  --add-data "$root\portablefix.ico;." `
  --icon "$root\portablefix.ico" `
  --name PortableFix `
  "$root\main.py"

if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller failed with exit code $LASTEXITCODE"
}

$nested = "$distStage\PortableFix"
Get-ChildItem $nested | ForEach-Object {
    $dest = Join-Path $distStage $_.Name
    if (Test-Path $dest) {
        Remove-Item -Recurse -Force $dest
    }
    Move-Item $_.FullName $dest
}
Remove-Item $nested -Force

py "$root\scripts\generate_sha256sums.py" "$root"

Write-Host "Build complete. Run PortableFix.cmd from $root to launch."
