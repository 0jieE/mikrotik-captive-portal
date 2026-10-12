# Update a deployed copy: back up the database, install packages, migrate, restart the services.
# Run from an ADMINISTRATOR PowerShell after you have replaced/edited the code:
#   powershell -ExecutionPolicy Bypass -File scripts\update.ps1 [-SkipBackup] [-SkipPip]
param([switch]$SkipBackup, [switch]$SkipPip)
$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $root
$py = Join-Path $root "env\Scripts\python.exe"
$nssm = Join-Path $root "tools\nssm.exe"
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$backups = Join-Path $root "backups"
New-Item -ItemType Directory -Force $backups | Out-Null

$dbUrl = (Select-String -Path ".env" -Pattern '^\s*DATABASE_URL\s*=\s*(\S+)' | Select-Object -First 1).Matches[0].Groups[1].Value

if (-not $SkipBackup) {
    if ($dbUrl -match '^sqlite:///(.+)$') {
        $file = if ([IO.Path]::IsPathRooted($Matches[1])) { $Matches[1] } else { Join-Path $root $Matches[1] }
        Copy-Item $file (Join-Path $backups "db-$stamp.sqlite3")
        Write-Host "Backed up SQLite -> backups\db-$stamp.sqlite3"
    }
    elseif ($dbUrl -match '^postgres(?:ql)?://([^:]+):([^@]*)@([^:/]+):(\d+)/(.+)$') {
        $user, $pw, $dbHost, $port, $db = $Matches[1..5]
        $pgdump = Get-ChildItem "C:\Program Files\PostgreSQL\*\bin\pg_dump.exe" | Sort-Object { [int]($_.FullName -replace '.*PostgreSQL\\(\d+)\\.*', '$1') } -Descending | Select-Object -First 1
        $out = Join-Path $backups "db-$stamp.dump"
        $env:PGPASSWORD = [uri]::UnescapeDataString($pw)
        & $pgdump.FullName -U $user -h $dbHost -p $port -Fc -f $out $db
        $code = $LASTEXITCODE
        $env:PGPASSWORD = $null
        if ($code -ne 0) { throw "Database backup failed - NOT updating. Fix this or use -SkipBackup." }
        Write-Host "Backed up PostgreSQL -> backups\db-$stamp.dump  (restore with pg_restore)"
    }
}

if (-not $SkipPip) { & $py -m pip install --quiet -r requirements.txt; Write-Host "Packages up to date" }

Write-Host "Applying migrations..."
& $py manage.py migrate --noinput
if ($LASTEXITCODE -ne 0) { throw "migrate failed - services NOT restarted (they keep running the old code). Restore from backups\ if needed." }
& $py manage.py check
if ($LASTEXITCODE -ne 0) { throw "System check failed - services NOT restarted." }

& powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "restart_services.ps1")
if ($LASTEXITCODE -ne 0) { throw "Services did not restart cleanly - the OLD code may still be running. See the output above." }
Write-Host "Update complete."
