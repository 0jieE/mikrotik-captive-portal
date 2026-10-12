@echo off
rem Background sync: updates usage, enforces quota, closes vanished sessions (every 30 s).
cd /d "%~dp0.."
"env\Scripts\python.exe" manage.py sync_sessions --interval 30
pause
