@echo off
REM ===========================================================================
REM  Instalacja Odwolania2json (Windows). Uruchom raz: setup.bat
REM  Co robi: sprawdza Python 3.13+, tworzy srodowisko .venv, instaluje pakiety
REM  z requirements.txt, sprawdza Tesseract OCR, tworzy plik .env i testuje LLM.
REM ===========================================================================
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1
echo.
echo === Instalacja Odwolania2json ===
echo.

REM --- 1. Python 3.13+ -------------------------------------------------------
set PYEXE=
py -3.13 --version >nul 2>&1
if not errorlevel 1 set PYEXE=py -3.13
if not defined PYEXE (
  py -3 -c "import sys; sys.exit(0 if sys.version_info>=(3,13) else 1)" >nul 2>&1
  if not errorlevel 1 set PYEXE=py -3
)
if not defined PYEXE (
  python -c "import sys; sys.exit(0 if sys.version_info>=(3,13) else 1)" >nul 2>&1
  if not errorlevel 1 set PYEXE=python
)
if not defined PYEXE (
  echo BLAD: potrzebny jest Python 3.13 lub nowszy.
  echo Pobierz go z https://www.python.org/downloads/ ^(zaznacz "Add python.exe to PATH"^) i uruchom setup.bat ponownie.
  pause
  exit /b 1
)
echo [1/5] Python: %PYEXE%

REM --- 2. Srodowisko wirtualne ----------------------------------------------
if not exist ".venv\Scripts\python.exe" (
  echo [2/5] Tworze srodowisko .venv ...
  %PYEXE% -m venv .venv
  if errorlevel 1 ( echo BLAD: nie udalo sie utworzyc .venv & pause & exit /b 1 )
) else (
  echo [2/5] Srodowisko .venv juz istnieje.
)

REM --- 3. Pakiety -------------------------------------------------------------
echo [3/5] Instaluje pakiety z requirements.txt ...
".venv\Scripts\python.exe" -m pip install --upgrade pip --quiet
".venv\Scripts\python.exe" -m pip install -r requirements.txt --quiet
if errorlevel 1 ( echo BLAD: instalacja pakietow nie powiodla sie ^(sprawdz internet^). & pause & exit /b 1 )

REM --- 4. Tesseract OCR (tylko do skanow; polski pakiet jest w folderze tessdata) ---
set TESS=
where tesseract >nul 2>&1
if not errorlevel 1 set TESS=1
if exist "C:\Program Files\Tesseract-OCR\tesseract.exe" set TESS=1
if defined TESS (
  echo [4/5] Tesseract OCR: znaleziony.
) else (
  echo [4/5] Tesseract OCR nie jest zainstalowany ^(potrzebny tylko do skanowanych PDF-ow^).
  set /p ANS="      Zainstalowac go teraz przez winget? [T/N]: "
  if /i "%ANS%"=="T" (
    winget install --id UB-Mannheim.TesseractOCR -e --silent --accept-package-agreements --accept-source-agreements
  ) else (
    echo      Pomijam. PDF-y z warstwa tekstowa beda dzialac bez Tesseracta.
  )
)

REM --- 5. Plik .env i test LLM -------------------------------------------------
if not exist ".env" (
  copy ".env.example" ".env" >nul
  echo [5/5] Utworzono plik .env z .env.example.
  echo      WAZNE: otworz plik .env i wpisz klucz API w linii LLM_API_KEY=
) else (
  echo [5/5] Plik .env juz istnieje.
)
echo.
echo --- Test polaczenia z LLM ---
".venv\Scripts\python.exe" -m odw.check_llm
echo.
echo === Gotowe ===
echo Uruchomienie aplikacji:  uruchom.bat   ^(strona: http://localhost:8000^)
echo Test jakosci i testy:    test_f1.bat
echo.
pause
