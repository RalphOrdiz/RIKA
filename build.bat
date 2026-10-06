@echo off
REM Build Rika -> dist\Rika\Rika.exe  (PyInstaller one-folder build)
REM Usage: double-click, or run "build.bat" from a Command Prompt in this folder.
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [1/6] Creating virtual environment...
    py -3 -m venv .venv 2>nul || python -m venv .venv || goto :error
)
set PY=.venv\Scripts\python.exe

echo [2/6] Installing packages...
%PY% -m pip install --upgrade pip -q || goto :error
%PY% -m pip install -q -r requirements.txt -r requirements-dev.txt || goto :error

echo [3/6] Downloading speech models and SumatraPDF (first time only)...
%PY% tools\download_models.py || goto :error

echo [4/6] Making assets...
if not exist "rika\assets\chime.wav" %PY% tools\make_assets.py || goto :error

echo [5/6] Running tests...
%PY% -m pytest -q || goto :error

echo [6/6] Running PyInstaller...
%PY% -m PyInstaller --noconfirm --clean Rika.spec || goto :error

copy /y SETUP.md dist\Rika\ >nul
copy /y config.example.json dist\Rika\ >nul

echo.
echo ============================================================
echo  Done!  dist\Rika\Rika.exe
echo  Copy the whole dist\Rika folder to the shop PC.
echo ============================================================
exit /b 0

:error
echo.
echo BUILD FAILED - see the messages above.
exit /b 1
