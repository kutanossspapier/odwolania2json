@echo off
REM Liczy F1 (ogolnie, per typ dokumentu, per pole) na danych treningowych UZP.
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1
".venv\Scripts\python.exe" -m odw.evaluate --errors 2
echo.
echo Testy jednostkowe:
".venv\Scripts\python.exe" -m pytest -q
pause
