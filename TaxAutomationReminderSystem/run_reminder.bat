@echo off
setlocal EnableExtensions
chcp 65001 >nul 2>&1
title Tax Reminder System - ExampleCo Group
color 07

rem Always enter the batch file's own folder.  For a UNC folder, pushd
rem temporarily maps it to a drive letter so CMD never uses its default folder.
set "SCRIPT_ROOT=%~dp0"
pushd "%SCRIPT_ROOT%." >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Could not access script folder:
    echo   %SCRIPT_ROOT%
    echo.
    pause
    exit /b 1
)
set "SCRIPT_DIR=%CD%"

echo ============================================================
echo   Tax Reminder System
echo   ExampleCo Group / Example CPA Firm P.C. Inc.
echo ============================================================
echo.

rem --- Step 1: Find Python ---
set "PYTHON_EXE="

rem Prefer the current user's real Python install before falling back to PATH.
for %%P in (
    "%LOCALAPPDATA%\Python\bin\python.exe"
    "%LOCALAPPDATA%\Python\pythoncore-3.14-64\python.exe"
) do (
    if exist %%~P if not defined PYTHON_EXE set "PYTHON_EXE=%%~P"
)

if not defined PYTHON_EXE (
    python --version >nul 2>&1
    if not errorlevel 1 set "PYTHON_EXE=python"
)

if not defined PYTHON_EXE (
    py --version >nul 2>&1
    if not errorlevel 1 set "PYTHON_EXE=py"
)

if not defined PYTHON_EXE (
    echo [ERROR] Python not found.
    echo.
    echo   Please install Python 3.8 or later from:
    echo     https://www.python.org/downloads/
    echo.
    echo   IMPORTANT: During installation, check the box:
    echo     "Add Python to PATH"
    echo.
    popd
    pause
    exit /b 1
)

for /f "tokens=*" %%v in ('"%PYTHON_EXE%" --version 2^>^&1') do echo   Found: %%v
echo.

rem --- Step 2: Check / install required packages ---
echo Checking required packages...

"%PYTHON_EXE%" -m pip install -r "%SCRIPT_DIR%\requirements.txt" -q --disable-pip-version-check
if errorlevel 1 (
    echo   [ERROR] Failed to install required packages.
    echo.
    echo   Please run this manually in Command Prompt:
    echo     "%PYTHON_EXE%" -m pip install -r "%SCRIPT_DIR%\requirements.txt"
    echo.
    popd
    pause
    exit /b 1
)

"%PYTHON_EXE%" -c "import msoffcrypto, openpyxl, requests, dotenv; from docx import Document" >nul 2>&1
if errorlevel 1 (
    echo   [ERROR] Required packages are still missing after install.
    echo.
    echo   Please run this manually in Command Prompt:
    echo     "%PYTHON_EXE%" -m pip install -r "%SCRIPT_DIR%\requirements.txt"
    echo.
    popd
    pause
    exit /b 1
)

echo   All packages ready.
echo.

rem --- Step 3: Check network drive ---
if not exist "Z:\" (
    echo [WARN] Drive Z: is not mapped.
    echo   The script will try the UNC path automatically.
    echo   If the master file is not found, please map Z: to:
    echo     \\file-server\Shared Folder
    echo.
)

rem --- Step 4: Launch script ---
"%PYTHON_EXE%" "%SCRIPT_DIR%\tax_reminder.py"
set "EXIT_CODE=%errorlevel%"

if not "%EXIT_CODE%"=="0" (
    echo.
    echo [ERROR] The script exited with an error ^(code %EXIT_CODE%^).
    echo   Please check the Logs\ folder for details.
    echo.
)

popd
pause
exit /b %EXIT_CODE%
