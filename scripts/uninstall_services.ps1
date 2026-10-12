# Removes the CDKPortal and CDKSync Windows services (the project, database and data are NOT touched).
# Run from an ADMINISTRATOR PowerShell:  powershell -ExecutionPolicy Bypass -File scripts\uninstall_services.ps1
$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$nssm = Join-Path $root "tools\nssm.exe"
foreach ($name in "CDKSync", "CDKPortal") {
    if (Get-Service $name -ErrorAction SilentlyContinue) {
        & $nssm stop $name 2>&1 | Out-Null
        & $nssm remove $name confirm | Out-Null
        Write-Host "Removed $name"
    } else { Write-Host "$name not installed" }
}
