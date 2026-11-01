@echo off
cd /d "%~dp0"
".venv\Scripts\python.exe" -m snakekb.web --open --proxy http://127.0.0.1:65532
pause
