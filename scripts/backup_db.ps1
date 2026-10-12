# Backs up the portal database to backups\ and keeps only the newest N backups.
# Works for PostgreSQL (pg_dump, custom format) and SQLite (file copy), based on DATABASE_URL in .env.
# Run manually, or schedule nightly (see docs\DEPLOYMENT_GUIDE.md, "Backups").
#   powershell -ExecutionPolicy Bypass -File scripts\backup_db.ps1 [-Keep 14]
# Restore a PostgreSQL backup:  pg_restore -U cdk_app -h 127.0.0.1 -p <port> -d cdk_portal --clean --if-exists --no-owner backups\db-XXXX.dump
param([int]$Keep = 14)
$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$backups = Join-Path $root "backups"
New-Item -ItemType Directory -Force $backups | Out-Null
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$dbUrl = (Select-String -Path (Join-Path $root ".env") -Pattern '^\s*DATABASE_URL\s*=\s*(\S+)' | Select-Object -First 1).Matches[0].Groups[1].Value

if ($dbUrl -match '^sqlite:///(.+)$') {
    $file = if ([IO.Path]::IsPathRooted($Matches[1])) { $Matches[1] } else { Join-Path $root $Matches[1] }
    $out = Join-Path $backups "db-$stamp.sqlite3"
    Copy-Item $file $out
}
elseif ($dbUrl -match '^postgres(?:ql)?://([^:]+):([^@]*)@([^:/]+):(\d+)/(.+)$') {
    $user, $pw, $dbHost, $port, $db = $Matches[1..5]
    $pgdump = Get-ChildItem "C:\Program Files\PostgreSQL\*\bin\pg_dump.exe" |
        Sort-Object { [int]($_.FullName -replace '.*PostgreSQL\\(\d+)\\.*', '$1') } -Descending | Select-Object -First 1
    if (-not $pgdump) { throw "pg_dump.exe not found under C:\Program Files\PostgreSQL" }
    $out = Join-Path $backups "db-$stamp.dump"
    $env:PGPASSWORD = [uri]::UnescapeDataString($pw)
    try { & $pgdump.FullName -U $user -h $dbHost -p $port -Fc -f $out $db; $code = $LASTEXITCODE } finally { $env:PGPASSWORD = $null }
    if ($code -ne 0) { if (Test-Path $out) { Remove-Item $out -Force }; throw "pg_dump failed (exit $code)" }
}
else { throw "Unsupported DATABASE_URL" }

$size = [math]::Round((Get-Item $out).Length / 1KB)
Write-Host "Backup OK: $out ($size KB)"
# retention: keep the newest $Keep files
Get-ChildItem $backups -File -Include "db-*.dump", "db-*.sqlite3" -Recurse | Sort-Object LastWriteTime -Descending | Select-Object -Skip $Keep | ForEach-Object {
    Remove-Item $_.FullName -Force; Write-Host "Removed old backup $($_.Name)"
}
