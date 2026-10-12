@echo off
rem Start the web portal and the usage-sync loop, each in its own window.
cd /d "%~dp0.."
rem PORT must match PORTAL_BASE_URL in .env (80 = http://192.168.88.5, 8000 = http://192.168.88.5:8000)
set PORT=80
start "CDK Portal (web)" cmd /k ""env\Scripts\waitress-serve.exe" --listen=0.0.0.0:%PORT% config.wsgi:application"
start "CDK Usage sync" cmd /k ""env\Scripts\python.exe" manage.py sync_sessions --interval 30"
