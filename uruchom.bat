@echo off
REM Uruchamia stronę na http://localhost:8000 (i serwer Ollama dla LLM, jeśli nie działa).
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1
powershell -NoProfile -Command "if (-not (Get-NetTCPConnection -LocalPort 11434 -State Listen -ErrorAction SilentlyContinue)) { $o = Join-Path $env:LOCALAPPDATA 'Programs\Ollama\ollama.exe'; if (Test-Path $o) { Start-Process $o -ArgumentList 'serve' -WindowStyle Hidden; Start-Sleep 3 } }"
start "" "http://localhost:8000"
".venv\Scripts\python.exe" -m uvicorn odw.api:app --port 8000
pause
