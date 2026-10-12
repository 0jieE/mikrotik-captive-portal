@echo off
rem One-shot: push policies/accounts to the router and disable users Django says are off.
cd /d "%~dp0.."
"env\Scripts\python.exe" manage.py sync_router
pause
