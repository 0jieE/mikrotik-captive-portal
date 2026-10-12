# Resets the 'postgres' superuser password on local PostgreSQL servers.
# MUST run as Administrator. Usage:
#   powershell -ExecutionPolicy Bypass -File scripts\reset_postgres_password.ps1 -Password "NewPass123" [-Versions 13,17] [-LogPath out.log]
#
# How: backs up pg_hba.conf -> temporarily allows passwordless login from 127.0.0.1 only -> restarts the
# service -> ALTER USER -> ALWAYS restores the original pg_hba.conf (try/finally) -> restarts -> verifies
# the new password works over a normal password-authenticated connection.
param(
    [Parameter(Mandatory = $true)][string]$Password,
    [int[]]$Versions = @(13, 17),
    [string]$LogPath = ""
)

if ($Password -notmatch '^[A-Za-z0-9_\-]{8,}$') { throw "Use at least 8 letters/digits/_/- (keeps SQL and URLs simple)." }
if ($LogPath) { Start-Transcript -Path $LogPath -Force | Out-Null }

function Wait-Pg($bin, $port) {
    for ($i = 0; $i -lt 40; $i++) {
        & "$bin\pg_isready.exe" -h 127.0.0.1 -p $port 2>$null | Out-Null
        if ($LASTEXITCODE -eq 0) { return $true }
        Start-Sleep -Milliseconds 500
    }
    return $false
}

$results = @()
foreach ($v in $Versions) {
    $root = "C:\Program Files\PostgreSQL\$v"
    $bin = "$root\bin"
    $data = "$root\data"
    $svc = "postgresql-x64-$v"
    $hba = "$data\pg_hba.conf"
    $bak = "$hba.cdk-backup"
    Write-Host "=== PostgreSQL $v ==="
    if (-not (Test-Path $hba)) { Write-Host "pg_hba.conf not found, skipping"; $results += "v${v}: SKIPPED (not found)"; continue }
    $port = [int]((Select-String -Path "$data\postgresql.conf" -Pattern '^\s*port\s*=\s*(\d+)').Matches[0].Groups[1].Value)

    Copy-Item $hba $bak -Force
    $ok = $false
    try {
        Set-Content -Path $hba -Encoding ascii -Value @("host all all 127.0.0.1/32 trust", "host all all ::1/128 trust")
        Restart-Service $svc -Force
        if (-not (Wait-Pg $bin $port)) { throw "server did not come back up" }
        & "$bin\psql.exe" -U postgres -h 127.0.0.1 -p $port -w -v ON_ERROR_STOP=1 -c "ALTER USER postgres WITH PASSWORD '$Password';"
        if ($LASTEXITCODE -ne 0) { throw "ALTER USER failed" }
        $ok = $true
    }
    catch { Write-Host "ERROR: $_" }
    finally {
        Copy-Item $bak $hba -Force      # always put the original security rules back
        Restart-Service $svc -Force
        Wait-Pg $bin $port | Out-Null
        Remove-Item $bak -Force -ErrorAction SilentlyContinue
    }

    # verify with normal authentication (original pg_hba.conf is back in force)
    $env:PGPASSWORD = $Password
    $who = & "$bin\psql.exe" -U postgres -h 127.0.0.1 -p $port -tAc "select 'login ok as ' || current_user" 2>&1
    Remove-Item Env:\PGPASSWORD -ErrorAction SilentlyContinue
    $verified = ($LASTEXITCODE -eq 0)
    $line = "v${v} (port $port): " + $(if ($ok -and $verified) { "PASSWORD CHANGED - verified ($who)" } else { "FAILED ($who)" })
    Write-Host $line
    $results += $line
}
Write-Host ""
Write-Host "SUMMARY"
$results | ForEach-Object { Write-Host $_ }
if ($LogPath) { Stop-Transcript | Out-Null }
