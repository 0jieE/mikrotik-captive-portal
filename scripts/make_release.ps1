# Builds a clean, ready-to-hand-over zip of the project in release\ .
# It deliberately EXCLUDES everything machine-specific or secret:
#   env\ (virtualenv - not portable), .env (passwords), db.sqlite3, logs\, backups\, __pycache__, .mock_router.json, release\
# Usage:  powershell -ExecutionPolicy Bypass -File scripts\make_release.ps1 [-Version 1.0]
param([string]$Version = (Get-Date -Format "yyyyMMdd"))
$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$outDir = Join-Path $root "release"
$stage = Join-Path $env:TEMP "cdk-release-stage"
$name = "cdk-wifi-portal-$Version"
$zip = Join-Path $outDir "$name.zip"

New-Item -ItemType Directory -Force $outDir | Out-Null
if (Test-Path $stage) { Remove-Item $stage -Recurse -Force }
New-Item -ItemType Directory -Force (Join-Path $stage $name) | Out-Null

$excludeDirs = @("env", "release", "logs", "backups", "staticfiles", "__pycache__", ".git", ".idea", ".vscode", ".claude", "tools")
$excludeFiles = @(".env", "db.sqlite3", ".mock_router.json", "SEEDED_CREDENTIALS.md", "*.pyc", "*.log")
# robocopy: /E subdirs, /XD exclude dirs, /XF exclude files; exit codes < 8 are success
& robocopy $root (Join-Path $stage $name) /E /XD $excludeDirs /XF $excludeFiles /NFL /NDL /NJH /NJS /NP | Out-Null
if ($LASTEXITCODE -ge 8) { throw "robocopy failed ($LASTEXITCODE)" }
# keep an empty staticfiles dir (silences a harmless WhiteNoise warning) and tools\ placeholder
New-Item -ItemType Directory -Force (Join-Path $stage "$name\staticfiles"), (Join-Path $stage "$name\tools") | Out-Null
Set-Content (Join-Path $stage "$name\tools\README.txt") "nssm.exe is downloaded automatically by scripts\install_services.ps1 (SHA-256 verified). If nssm.cc is down, copy nssm.exe (NSSM 2.24, win64) into this folder."

if (Test-Path $zip) { Remove-Item $zip -Force }
Compress-Archive -Path (Join-Path $stage $name) -DestinationPath $zip -CompressionLevel Optimal
Remove-Item $stage -Recurse -Force
$mb = [math]::Round((Get-Item $zip).Length / 1MB, 2)
Write-Host "Release built: $zip ($mb MB)"

# safety net: make sure no secrets slipped in
Add-Type -AssemblyName System.IO.Compression.FileSystem
$entries = [IO.Compression.ZipFile]::OpenRead($zip).Entries | ForEach-Object { $_.FullName }
$bad = $entries | Where-Object { $_ -match '(^|[\\/])(\.env|db\.sqlite3|\.mock_router\.json|SEEDED_CREDENTIALS\.md)$' -or $_ -match '[\\/]env[\\/]' }
if ($bad) { Write-Host "WARNING - secrets/venv found in the zip:`n$($bad -join "`n")" -ForegroundColor Red; exit 1 }
Write-Host "Checked: no .env, database or virtualenv inside. $($entries.Count) files."
