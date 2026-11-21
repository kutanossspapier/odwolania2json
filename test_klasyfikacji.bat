@echo off
REM Uruchamia test klasyfikatora na danych treningowych UZP.
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1
".venv\Scripts\python.exe" -m odw.eval_classify
pause
