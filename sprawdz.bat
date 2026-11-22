@echo off
REM Wersja konsolowa: sprawdza dokument po dokumencie, co jest dobrze, a co zle. Opcje: --bad --fields --only odwolanie --llm
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1
".venv\Scripts\python.exe" -m odw.check %*
pause
