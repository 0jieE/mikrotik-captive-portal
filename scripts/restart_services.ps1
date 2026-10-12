# Restarts CDKSync + CDKPortal RELIABLY and verifies the new code is actually running.
# (A plain "nssm restart" can fail silently - always use this script after changing code or .env.)
# Run from an ADMINISTRATOR PowerShell:  powershell -ExecutionPolicy Bypass -File scripts\restart_services.ps1
param([string]$LogPath = "")
$ErrorActionPreference = "Continue"
if ($LogPath) { Start-Transcript -Path $LogPath -Force | Out-Null }
$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$begin = Get-Date

# port to check = port of PORTAL_BASE_URL (80 if none)
$port = 80
$line = Select-String -Path (Join-Path $root ".env") -Pattern '^\s*PORTAL_BASE_URL\s*=\s*(\S+)' | Select-Object -First 1
if ($line) { $port = ([uri]$line.Matches[0].Groups[1].Value).Port }

function Svc($n) { Get-CimInstance Win32_Service -Filter "Name='$n'" -ErrorAction SilentlyContinue }
function Kill-Tree($procId) { if ($procId -and $procId -ne 0) { & taskkill.exe /PID $procId /T /F 2>&1 | Out-Null } }

$names = "CDKSync", "CDKPortal" | Where-Object { Svc $_ }
if (-not $names) { throw "CDKPortal / CDKSync are not installed. Run scripts\install_services.ps1 first." }

# 1. stop (dependents first) and make sure NOTHING is left over
foreach ($n in $names) {
    $svc = Svc $n
    if ($svc.State -ne "Stopped") { Stop-Service $n -Force -ErrorAction SilentlyContinue }
}
Start-Sleep 2
foreach ($n in $names) {
    $svc = Svc $n
    if ($svc.State -ne "Stopped" -or ($svc.ProcessId -and $svc.ProcessId -ne 0)) {
        Write-Host "$n did not stop cleanly - killing its process tree (pid $($svc.ProcessId))"
        Kill-Tree $svc.ProcessId
    }
}
# anything still holding the portal port (e.g. an orphaned worker) must go, or the new server cannot bind
Start-Sleep 1
Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue | Where-Object { $_.LocalAddress -in "0.0.0.0", "::" } | ForEach-Object {
    $p = Get-CimInstance Win32_Process -Filter "ProcessId=$($_.OwningProcess)"
    if ($p -and $p.Name -match "python|waitress") { Write-Host "Killing orphan $($p.Name) pid $($p.ProcessId) still on port $port"; Kill-Tree $p.ProcessId }
}

# 2. start (web first, then sync)
foreach ($n in "CDKPortal", "CDKSync") { if ($names -contains $n) { Start-Service $n; Write-Host "Started $n" } }
Start-Sleep 6

# 3. VERIFY: both services Running, their processes are NEWER than this script, the port answers
$ok = $true
foreach ($n in $names) {
    $svc = Svc $n
    $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$($svc.ProcessId)" -ErrorAction SilentlyContinue
    $fresh = $proc -and ($proc.CreationDate -ge $begin.AddSeconds(-2))
    Write-Host ("{0,-10} state={1,-8} pid={2,-6} started={3}  {4}" -f $n, $svc.State, $svc.ProcessId, $(if ($proc) { $proc.CreationDate.ToString('HH:mm:ss') } else { "?" }), $(if ($fresh) { "OK (fresh)" } else { "NOT RESTARTED" }))
    if ($svc.State -ne "Running" -or -not $fresh) { $ok = $false }
}
$listener = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue | Where-Object { $_.LocalAddress -eq "0.0.0.0" } | Select-Object -First 1
if ($listener) {
    $lp = Get-Process -Id $listener.OwningProcess
    $newer = $lp.StartTime -ge $begin.AddSeconds(-2)
    Write-Host ("port {0}: owned by pid {1} ({2}) started {3}  {4}" -f $port, $lp.Id, $lp.ProcessName, $lp.StartTime.ToString('HH:mm:ss'), $(if ($newer) { "OK (fresh)" } else { "STALE PROCESS" }))
    if (-not $newer) { $ok = $false }
} else { Write-Host "port ${port}: nobody is listening"; $ok = $false }
try { $r = Invoke-WebRequest "http://127.0.0.1:$port/portal/login/" -UseBasicParsing -TimeoutSec 20; Write-Host "HTTP check: portal login page -> $($r.StatusCode)"; if ($r.StatusCode -ne 200) { $ok = $false } } catch { Write-Host "HTTP check FAILED: $($_.Exception.Message)"; $ok = $false }

if ($ok) { Write-Host "`nRESTART OK - the new code is live." } else { Write-Host "`nRESTART PROBLEM - see lines above and logs\CDKPortal.err.log" }
if ($LogPath) { Stop-Transcript | Out-Null }
exit $(if ($ok) { 0 } else { 1 })
