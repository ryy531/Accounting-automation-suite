@echo off
chcp 65001 > nul
title AR Reminder System

REM Check Python
python --version > nul 2>&1
if errorlevel 1 (
    echo.
    echo  [ERROR] Python not found. Please install Python 3 and try again.
    echo.
    pause
    exit /b 1
)

REM Check and install required packages
python -c "import rapidfuzz, pandas, openpyxl, cryptography, fpdf" > nul 2>&1
if errorlevel 1 (
    echo.
    echo  Installing required packages, please wait...
    pip install rapidfuzz pandas openpyxl cryptography google-genai fpdf2 -q
    if errorlevel 1 (
        echo  [ERROR] Failed to install packages. Check your internet connection.
        pause
        exit /b 1
    )
    echo  Done.
    echo.
)

REM Run the script
pushd "%~dp0"
python ar_reminder.py
popd

pause
