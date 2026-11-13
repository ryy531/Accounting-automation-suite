@echo off
chcp 65001 >nul 2>&1
title Tax Automation - ExampleCo CPA
set "PYTHONIOENCODING=utf-8"

set "PYTHON_EXE="

rem Prefer the current user's real Python install before falling back to PATH.
for %%P in (
    "%LOCALAPPDATA%\Python\bin\python.exe"
    "%LOCALAPPDATA%\Python\pythoncore-3.14-64\python.exe"
) do (
    if exist %%~P if not defined PYTHON_EXE set "PYTHON_EXE=%%~P"
)

if not defined PYTHON_EXE set "PYTHON_EXE=python"

"%PYTHON_EXE%" --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python is not installed or not in PATH.
    echo Install Python or disable the Windows Store python.exe app execution alias.
    echo Tried: %PYTHON_EXE%
    pause
    exit /b 1
)

echo Using Python: "%PYTHON_EXE%"
echo Checking dependencies...
"%PYTHON_EXE%" -m pip install -r "%~dp0requirements.txt" -q --disable-pip-version-check
if errorlevel 1 (
    echo [ERROR] Failed to install dependencies from "%~dp0requirements.txt".
    pause
    exit /b 1
)

"%PYTHON_EXE%" "%~dp0src\tax_automation.py"
set "EXITCODE=%ERRORLEVEL%"
if not "%EXITCODE%"=="0" (
    echo [ERROR] Tax Automation exited with code %EXITCODE%.
    pause
    exit /b %EXITCODE%
)

exit /b 0