# Installs the CDK portal as two Windows services using NSSM:
#   CDKPortal  - Waitress web server  (serves the instructor portal + admin panel)
#   CDKSync    - `manage.py sync_sessions` loop (usage, quota enforcement)
# Both start automatically at boot (before anyone logs in) and restart if they crash.
#
# Run from an ADMINISTRATOR PowerShell:
#   powershell -ExecutionPolicy Bypass -File scripts\install_services.ps1 [-Port 80] [-LogPath out.log]
param(
    [int]$Port = 0,
    [string]$LogPath = ""
)
$ErrorActionPreference = "Stop"
if ($LogPath) { Start-Transcript -Path $LogPath -Force | Out-Null }

$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$nssm = Join-Path $root "tools\nssm.exe"
$py = Join-Path $root "env\Scripts\python.exe"
$waitress = Join-Path $root "env\Scripts\waitress-serve.exe"
$logs = Join-Path $root "logs"
foreach ($f in $py, $waitress) {
    if (-not (Test-Path $f)) { throw "Missing $f - create the virtual environment first (see docs\DEPLOYMENT_GUIDE.md, step 5)." }
}
if (-not (Test-Path $nssm)) {
    Write-Host "NSSM not found - downloading nssm 2.24 ..."
    $zip = Join-Path $env:TEMP "nssm-2.24.zip"
    $expected = "727D1E42275C605E0F04ABA98095C38A8E1E46DEF453CDFFCE42869428AA6743"
    Invoke-WebRequest -Uri "https://nssm.cc/release/nssm-2.24.zip" -OutFile $zip -UseBasicParsing -TimeoutSec 60
    if ((Get-FileHash $zip -Algorithm SHA256).Hash -ne $expected) { throw "Downloaded NSSM failed its checksum - aborting. Download nssm 2.24 manually and put win64\nssm.exe in tools\." }
    $tmp = Join-Path $env:TEMP "nssm-extract"
    Expand-Archive $zip -DestinationPath $tmp -Force
    New-Item -ItemType Directory -Force (Split-Path $nssm) | Out-Null
    Copy-Item (Join-Path $tmp "nssm-2.24\win64\nssm.exe") $nssm -Force
}
New-Item -ItemType Directory -Force $logs | Out-Null

# --- port: -Port, else taken from PORTAL_BASE_URL in .env (no port in the URL = 80) ----------------
if ($Port -eq 0) {
    $Port = 80
    $line = Select-String -Path (Join-Path $root ".env") -Pattern '^\s*PORTAL_BASE_URL\s*=\s*(\S+)' | Select-Object -First 1
    if ($line) { $uri = [uri]$line.Matches[0].Groups[1].Value; $Port = $uri.Port }
}
Write-Host "Project : $root"
Write-Host "Port    : $Port"

# --- database service dependency (so the web app does not start before PostgreSQL) --------------
$dbUrl = (Select-String -Path (Join-Path $root ".env") -Pattern '^\s*DATABASE_URL\s*=\s*(\S+)' | Select-Object -First 1).Matches[0].Groups[1].Value
$dep = $null
if ($dbUrl -match '^postgres') {
    $dbPort = ([uri]($dbUrl -replace '^postgres(ql)?://', 'http://')).Port
    foreach ($svc in Get-Service -Name 'postgresql*') {
        $dataDir = ((Get-CimInstance Win32_Service -Filter "Name='$($svc.Name)'").PathName -replace '.*-D\s+"([^"]+)".*', '$1')
        $conf = Join-Path $dataDir "postgresql.conf"
        if ((Test-Path $conf) -and ((Select-String -Path $conf -Pattern '^\s*port\s*=\s*(\d+)' | Select-Object -First 1).Matches[0].Groups[1].Value -eq "$dbPort")) { $dep = $svc.Name }
    }
    Write-Host "Database: PostgreSQL on port $dbPort -> service '$dep'"
}

# --- stop development servers that would hold the port / duplicate the sync -----------------------
Get-CimInstance Win32_Process | Where-Object {
    $_.CommandLine -and $_.CommandLine -like "*$root*" -and ($_.CommandLine -match 'runserver|waitress-serve|sync_sessions')
} | ForEach-Object { Write-Host "Stopping old process $($_.ProcessId): $($_.Name)"; Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'runserver 0\.0\.0\.0:80|runserver .*:80\b' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Sleep 1

function Install-Svc($name, $exe, $arguments, $display, $description) {
    if (Get-Service $name -ErrorAction SilentlyContinue) {
        Write-Host "Service $name exists - reconfiguring"
        & $nssm stop $name 2>&1 | Out-Null
        & $nssm remove $name confirm 2>&1 | Out-Null
        Start-Sleep 1
    }
    & $nssm install $name $exe $arguments | Out-Null
    & $nssm set $name DisplayName $display | Out-Null
    & $nssm set $name Description $description | Out-Null
    & $nssm set $name AppDirectory $root | Out-Null
    & $nssm set $name Start SERVICE_DELAYED_AUTO_START | Out-Null
    & $nssm set $name AppExit Default Restart | Out-Null     # restart on any exit
    & $nssm set $name AppRestartDelay 5000 | Out-Null
    & $nssm set $name AppThrottle 10000 | Out-Null
    & $nssm set $name AppStdout (Join-Path $logs "$name.out.log") | Out-Null
    & $nssm set $name AppStderr (Join-Path $logs "$name.err.log") | Out-Null
    & $nssm set $name AppRotateFiles 1 | Out-Null
    & $nssm set $name AppRotateOnline 1 | Out-Null
    & $nssm set $name AppRotateBytes 5242880 | Out-Null     # rotate at 5 MB
    & $nssm set $name AppStopMethodConsole 3000 | Out-Null
    & $nssm set $name AppEnvironmentExtra "PYTHONUNBUFFERED=1" "PYTHONIOENCODING=utf-8" | Out-Null
    if ($dep) { & $nssm set $name DependOnService $dep | Out-Null }
    Write-Host "Installed $name"
}

Install-Svc "CDKPortal" $waitress "--listen=0.0.0.0:$Port --threads=8 config.wsgi:application" `
    "CDK Wi-Fi Portal (web)" "Colegio de Kidapawan instructor internet portal and admin panel (Waitress)."
Install-Svc "CDKSync" $py "manage.py sync_sessions --interval 30" `
    "CDK Wi-Fi Usage Sync" "Updates instructor data usage from the MikroTik router and enforces quotas every 30 s."
# CDKSync must NOT depend on CDKPortal: Windows refuses to stop/restart a service that other services depend on.
if ($dep) { & $nssm set CDKSync DependOnService $dep | Out-Null }

# --- firewall -------------------------------------------------------------------------------------
$rule = "CDK Portal $Port"
# remove firewall rules left over from an earlier port (e.g. "CDK Portal 80") so no unused port stays open
Get-NetFirewallRule -DisplayName "CDK Portal *" -ErrorAction SilentlyContinue | Where-Object { $_.DisplayName -ne $rule } | ForEach-Object {
    Write-Host "Removing old firewall rule '$($_.DisplayName)'"; $_ | Remove-NetFirewallRule
}
if (-not (Get-NetFirewallRule -DisplayName $rule -ErrorAction SilentlyContinue)) {
    New-NetFirewallRule -DisplayName $rule -Direction Inbound -Protocol TCP -LocalPort $Port -Action Allow -Profile Any | Out-Null
    Write-Host "Firewall rule '$rule' added"
} else { Write-Host "Firewall rule '$rule' already present" }

# --- start ----------------------------------------------------------------------------------------
Start-Service CDKPortal
Start-Sleep 4
Start-Service CDKSync
Start-Sleep 4
Get-Service CDKPortal, CDKSync | Format-Table Name, Status, StartType -AutoSize | Out-String | Write-Host

try {
    $r = Invoke-WebRequest -Uri "http://127.0.0.1:$Port/portal/login/" -UseBasicParsing -TimeoutSec 15
    Write-Host "HTTP check: portal login page -> $($r.StatusCode)"
} catch { Write-Host "HTTP check FAILED: $($_.Exception.Message)  (see logs\CDKPortal.err.log)" }

if ($LogPath) { Stop-Transcript | Out-Null }
