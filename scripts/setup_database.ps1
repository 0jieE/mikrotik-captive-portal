# Create the portal's PostgreSQL user + database and write DATABASE_URL into .env  (run ONCE per PC).
#
#   powershell -ExecutionPolicy Bypass -File scripts\setup_database.ps1
#
# It asks for ONE thing: the password of the PostgreSQL "postgres" user (the one you chose when you installed
# PostgreSQL). Everything else is automatic:
#   1. finds psql.exe and the port PostgreSQL listens on
#   2. checks the postgres password works
#   3. creates the user "cdk_app" (random password, never shown) and the database "cdk_portal"
#   4. checks that the new login works
#   5. creates .env from .env.example if needed, and writes DATABASE_URL (and a SECRET_KEY) into it
# Safe to run again: existing user/database are kept (the app user's password is re-generated and .env updated).
param(
    [int]$Port = 0,                  # 0 = detect (tries 5432, 5433, 5434)
    [string]$DbName = "cdk_portal",
    [string]$AppUser = "cdk_app",
    [string]$PostgresPassword,       # normally typed when asked
    [string]$AppPassword,            # letters+digits only; default: random
    [string]$EnvFile                 # default: <project>\.env
)
$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
if (-not $EnvFile) { $EnvFile = Join-Path $root ".env" }

function Step($n, $text) { Write-Host ""; Write-Host "[$n/5] $text" -ForegroundColor Cyan }
function Fail($text)     { Write-Host ""; Write-Host "STOPPED: $text" -ForegroundColor Red; exit 1 }

# ---- 1. find psql and the port --------------------------------------------------------------
Step 1 "Looking for PostgreSQL"
$psqlFile = Get-ChildItem "C:\Program Files\PostgreSQL\*\bin\psql.exe" -ErrorAction SilentlyContinue |
    Sort-Object { [int]($_.FullName -replace '.*PostgreSQL\\(\d+)\\.*', '$1') } -Descending | Select-Object -First 1
if (-not $psqlFile) {
    Fail "PostgreSQL is not installed on this PC. Install it first (https://www.enterprisedb.com/downloads/postgres-postgresql-downloads, keep the defaults and remember the password you choose), then run this script again."
}
$psql = $psqlFile.FullName
Write-Host "  psql: $psql"

function Test-Port($p) {
    try {
        $c = New-Object Net.Sockets.TcpClient
        $iar = $c.BeginConnect("127.0.0.1", $p, $null, $null)
        $ok = $iar.AsyncWaitHandle.WaitOne(700) -and $c.Connected
        $c.Close(); return $ok
    } catch { return $false }
}
if ($Port -eq 0) {
    foreach ($p in 5432, 5433, 5434) { if (Test-Port $p) { $Port = $p; break } }
    if ($Port -eq 0) { Fail "PostgreSQL does not answer on port 5432, 5433 or 5434. Is the 'postgresql-x64-NN' service running (Get-Service *postgres*)? If it uses another port, run: scripts\setup_database.ps1 -Port <number>" }
}
Write-Host "  port: $Port"

# ---- helpers ------------------------------------------------------------------------------------
function Invoke-Psql([string]$Sql, [string]$User, [string]$Password, [string]$Db = "postgres") {
    $env:PGPASSWORD = $Password
    $ErrorActionPreference = "Continue"   # Windows PowerShell 5.1 turns psql's stderr text into errors otherwise
    try {
        $out = & $psql -U $User -h 127.0.0.1 -p $Port -d $Db -v ON_ERROR_STOP=1 -t -A -c $Sql 2>&1
        $code = $LASTEXITCODE
    } finally { $env:PGPASSWORD = $null }
    $text = (($out | ForEach-Object { if ($_ -is [Management.Automation.ErrorRecord]) { $_.Exception.Message } else { "$_" } }) -join "`n").Trim()
    if ($code -ne 0) { throw ("psql said: " + $text) }
    return $text
}
function New-RandomPassword([int]$len = 20) {
    $chars = "abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789".ToCharArray()
    $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
    $bytes = New-Object byte[] $len
    $rng.GetBytes($bytes)
    -join ($bytes | ForEach-Object { $chars[$_ % $chars.Length] })
}

# ---- 2. superuser password --------------------------------------------------------------------
Step 2 "Checking the postgres password"
if (-not $PostgresPassword) {
    $secure = Read-Host "  Password of the PostgreSQL user 'postgres'" -AsSecureString
    $PostgresPassword = [Runtime.InteropServices.Marshal]::PtrToStringBSTR([Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure))
}
try { Invoke-Psql "SELECT 1" "postgres" $PostgresPassword | Out-Null }
catch {
    Write-Host $_.Exception.Message -ForegroundColor DarkGray
    Fail "Could not log in as 'postgres'. The password is probably wrong. If you forgot it, run scripts\reset_postgres_password.ps1 from an Administrator PowerShell, then run this script again."
}
Write-Host "  OK - logged in as postgres"

# ---- 3. create user + database ----------------------------------------------------------------
Step 3 "Creating the user '$AppUser' and the database '$DbName'"
if (-not $AppPassword) { $AppPassword = New-RandomPassword }
if ($AppPassword -notmatch '^[A-Za-z0-9]+$') { Fail "-AppPassword may contain letters and digits only." }
if ($DbName -notmatch '^[A-Za-z0-9_]+$' -or $AppUser -notmatch '^[A-Za-z0-9_]+$') { Fail "Database and user names may contain letters, digits and _ only." }
try {
    $roleExists = Invoke-Psql "SELECT 1 FROM pg_roles WHERE rolname='$AppUser'" "postgres" $PostgresPassword
    if ($roleExists -eq "1") {
        Invoke-Psql "ALTER ROLE $AppUser LOGIN PASSWORD '$AppPassword'" "postgres" $PostgresPassword | Out-Null
        Write-Host "  user '$AppUser' already existed - gave it a new password"
    } else {
        Invoke-Psql "CREATE ROLE $AppUser LOGIN PASSWORD '$AppPassword'" "postgres" $PostgresPassword | Out-Null
        Write-Host "  created user '$AppUser'"
    }
    $dbExists = Invoke-Psql "SELECT 1 FROM pg_database WHERE datname='$DbName'" "postgres" $PostgresPassword
    if ($dbExists -eq "1") {
        Invoke-Psql "ALTER DATABASE $DbName OWNER TO $AppUser" "postgres" $PostgresPassword | Out-Null
        Write-Host "  database '$DbName' already existed - kept it (data untouched)"
    } else {
        Invoke-Psql "CREATE DATABASE $DbName OWNER $AppUser ENCODING 'UTF8' TEMPLATE template0" "postgres" $PostgresPassword | Out-Null
        Write-Host "  created database '$DbName'"
    }
} catch { Fail $_.Exception.Message }

# ---- 4. test the new login --------------------------------------------------------------------
Step 4 "Testing the new login"
try { Invoke-Psql "SELECT current_user, current_database()" $AppUser $AppPassword $DbName | Out-Null }
catch { Fail ("The new user could not log in: " + $_.Exception.Message) }
Write-Host "  OK - '$AppUser' can log in to '$DbName'"

# ---- 5. write .env ----------------------------------------------------------------------------
Step 5 "Writing $EnvFile"
if (-not (Test-Path $EnvFile)) {
    $example = Join-Path $root ".env.example"
    if (-not (Test-Path $example)) { Fail ".env.example not found in $root - run this script from the project folder." }
    Copy-Item $example $EnvFile
    Write-Host "  created .env from .env.example"
} else {
    Copy-Item $EnvFile "$EnvFile.before-setup" -Force
    Write-Host "  .env already existed - old copy saved as .env.before-setup"
}
$url = "postgres://${AppUser}:${AppPassword}@127.0.0.1:${Port}/${DbName}"
$lines = [IO.File]::ReadAllLines($EnvFile)
$foundUrl = $false; $foundKey = $false
for ($i = 0; $i -lt $lines.Length; $i++) {
    if ($lines[$i] -match '^\s*DATABASE_URL\s*=') { $lines[$i] = "DATABASE_URL=$url"; $foundUrl = $true }
    elseif ($lines[$i] -match '^\s*SECRET_KEY\s*=\s*(change-me.*)?$') {
        $bytes = New-Object byte[] 48
        [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
        $lines[$i] = "SECRET_KEY=" + ([Convert]::ToBase64String($bytes) -replace '[+/=]', 'x')
        $foundKey = $true
        Write-Host "  generated a new SECRET_KEY"
    }
}
if (-not $foundUrl) { $lines += "DATABASE_URL=$url" }
[IO.File]::WriteAllLines($EnvFile, $lines, (New-Object Text.UTF8Encoding($false)))
Write-Host "  DATABASE_URL written (user $AppUser, port $Port, database $DbName)"

Write-Host ""
Write-Host "DONE. The database exists and .env points at it." -ForegroundColor Green
Write-Host "Next (still in the project folder):"
Write-Host "  env\Scripts\python.exe manage.py migrate"
Write-Host "  env\Scripts\python.exe manage.py create_admin"
Write-Host "Then edit .env for the router (MIKROTIK_*), PORTAL_BASE_URL and ALLOWED_HOSTS - see docs\DEPLOYMENT_GUIDE.md step 6."
