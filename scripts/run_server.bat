@echo off
rem Run the CDK portal (Waitress). Uses the project's own virtual environment, no activation needed.
cd /d "%~dp0.."
rem PORT must match PORTAL_BASE_URL in .env (80 = http://192.168.88.5, 8000 = http://192.168.88.5:8000)
set PORT=80
"env\Scripts\waitress-serve.exe" --listen=0.0.0.0:%PORT% config.wsgi:application
pause
