@echo off
setlocal enabledelayedexpansion
rem Double-click to play. First run sets up everything on its own (private
rem Python environment in core\venv, dependencies installed into it); every
rem run after that skips straight to launching, unless requirements.txt has
rem changed since.
cd /d "%~dp0core"

where python >nul 2>nul
if errorlevel 1 (
  echo python was not found on PATH. Install it from https://www.python.org/downloads/
  echo IMPORTANT: check "Add python.exe to PATH" during install, then try again.
  pause
  exit /b 1
)

if not exist "venv\Scripts\python.exe" (
  echo First run: setting up a private Python environment in core\venv ...
  python -m venv venv
  if errorlevel 1 (
    echo Failed to create the virtual environment -- see above.
    pause
    exit /b 1
  )
)

set "HASH_FILE=venv\.requirements.sha256"
set "NEW_HASH="
for /f "skip=1 tokens=* usebackq" %%H in (`certutil -hashfile requirements.txt SHA256 2^>nul`) do (
  if not defined NEW_HASH set "NEW_HASH=%%H"
)
set "OLD_HASH="
if exist "%HASH_FILE%" set /p OLD_HASH=<"%HASH_FILE%"

if not "%NEW_HASH%"=="%OLD_HASH%" (
  echo Installing/updating dependencies ...
  venv\Scripts\python.exe -m pip install --quiet --upgrade pip
  venv\Scripts\python.exe -m pip install --quiet -r requirements.txt
  if errorlevel 1 (
    echo Dependency install failed -- see above.
    pause
    exit /b 1
  )
  if defined NEW_HASH (>"%HASH_FILE%" echo %NEW_HASH%)
)

venv\Scripts\python.exe dashboard.py %*
if errorlevel 1 (
  echo.
  echo Exited with an error -- see above.
  pause
)
