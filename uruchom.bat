@echo off
REM Uruchamia stronę podglądu na http://localhost:8000
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1
start "" "http://localhost:8000"
".venv\Scripts\python.exe" -m uvicorn odw.api:app --port 8000
pause
