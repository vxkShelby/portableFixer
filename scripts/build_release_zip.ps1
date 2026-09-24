# scripts/build_release_zip.ps1
# Packages the portable folder (App/Data/Modules/PortableFix.cmd) into the
# release zip. This exact shape - one top-level "PortableFix" folder - is a
# contract portablefix/update_swap.py's stage_update() checks when it
# unpacks this same zip next to an existing install (and so do the clients
# of every released version, so it must never change).
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
# Data\ is copied by allowlist, never wholesale: on the build machine it also
# holds that machine's runtime state (settings.json with the technician name
# and custom presets, update_status.txt, ...), which would otherwise ship to
# every user - and the updater copies the zip's Data\ over each install.
New-Item -ItemType Directory -Force -Path "$stage\Data" | Out-Null
foreach ($name in @("SHA256SUMS", "PortableFix-SelfSigned.cer", ".gitkeep")) {
    $src = Join-Path "$root\Data" $name
    if (Test-Path -LiteralPath $src) { Copy-Item -LiteralPath $src -Destination "$stage\Data" }
}
if (-not (Test-Path -LiteralPath "$stage\Data\SHA256SUMS")) {
    Write-Warning "Data\SHA256SUMS missing - run scripts\generate_sha256sums.py first, or the integrity check will flag every file."
}
Copy-Item "$root\Modules" -Destination "$stage\Modules" -Recurse
Copy-Item "$root\Vendor" -Destination "$stage\Vendor" -Recurse

$zip = "$outDir\PortableFix-Portable.zip"
if (Test-Path $zip) { Remove-Item $zip -Force }
Compress-Archive -Path $stage -DestinationPath $zip -CompressionLevel Optimal

$hash = (Get-FileHash -Path $zip -Algorithm SHA256).Hash.ToLower()
Set-Content -Path "$zip.sha256" -Value "$hash  PortableFix-Portable.zip" -Encoding ASCII

Remove-Item $stageRoot -Recurse -Force

Write-Host "Release zip written to $zip"
