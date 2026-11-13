@echo off
chcp 65001 >nul 2>nul
title Annual Return Tracker Tool - ExampleCo Group

REM ---- Fix UNC path issue: switch to local temp dir ----
cd /d "%TEMP%" >nul 2>nul

echo ============================================================
echo   Annual Return Tracker Tool
echo   ExampleCo Group / Example CPA Firm P.C. Inc.
echo ============================================================
echo.

REM ---- Check Python ----
where python >nul 2>nul
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Python not found. Please install Python first.
    echo Download: https://www.python.org/downloads/
    echo IMPORTANT: Check "Add Python to PATH" during installation.
    echo.
    pause
    exit /b 1
)
echo [OK] Python found.

REM ---- Check dependencies ----
python -c "import msoffcrypto" >nul 2>nul
if %ERRORLEVEL% NEQ 0 (
    echo [INFO] Installing required packages, please wait...
    python -m pip install msoffcrypto-tool openpyxl -q
    if %ERRORLEVEL% NEQ 0 (
        echo [ERROR] Failed to install packages. Try running as Administrator.
        echo.
        pause
        exit /b 1
    )
    echo [OK] Packages installed.
    echo.
)
echo [OK] All dependencies ready.
echo.

REM ---- Locate the script (same folder as this BAT) ----
set "SCRIPT_DIR=%~dp0"
echo [OK] Script location: %SCRIPT_DIR%
echo.

REM ---- Run ----
echo Starting script...
echo.
python "%SCRIPT_DIR%generate_annual_return.py"

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo ============================================================
    echo   [ERROR] Script exited with error code %ERRORLEVEL%
    echo   Please screenshot this window and report the error.
    echo ============================================================
)

echo.
pause
