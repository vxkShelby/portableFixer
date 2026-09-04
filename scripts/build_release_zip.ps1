# scripts/build_release_zip.ps1
# Packages the portable folder (App/Data/Modules/PortableFix.cmd) into the
# release zip. This exact shape - one top-level "PortableFix" folder - is a
# contract portablefix/updater.py's build_swap_script() relies on when it
# expands this same zip on an existing install.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$outDir = "$root\Output"
New-Item -ItemType Directory -Force -Path $outDir | Out-Null

$stageRoot = "$env:TEMP\PortableFix_release_stage"
$stage = "$stageRoot\PortableFix"
if (Test-Path $stageRoot) { Remove-Item $stageRoot -Recurse -Force }
New-Item -ItemType Directory -Force -Path $stage | Out-Null

Copy-Item "$root\PortableFix.cmd" -Destination $stage
Copy-Item "$root\App" -Destination "$stage\App" -Recurse
Copy-Item "$root\Data" -Destination "$stage\Data" -Recurse
Copy-Item "$root\Modules" -Destination "$stage\Modules" -Recurse

$zip = "$outDir\PortableFix-Portable.zip"
if (Test-Path $zip) { Remove-Item $zip -Force }
Compress-Archive -Path $stage -DestinationPath $zip -CompressionLevel Optimal

$hash = (Get-FileHash -Path $zip -Algorithm SHA256).Hash.ToLower()
Set-Content -Path "$zip.sha256" -Value "$hash  PortableFix-Portable.zip" -Encoding ASCII

Remove-Item $stageRoot -Recurse -Force

Write-Host "Release zip written to $zip"
