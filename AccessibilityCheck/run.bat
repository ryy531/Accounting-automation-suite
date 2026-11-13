@echo off
chcp 65001 > nul
title ISC Accessibility Check

:: Enable ANSI colour support (Windows 10+)
reg add HKCU\Console /v VirtualTerminalLevel /t REG_DWORD /d 1 /f > nul 2>&1

echo.
echo  =============================================
echo   ISC Corporate Registry - Accessibility Check
echo  =============================================
echo.

"%LOCALAPPDATA%\Python\pythoncore-3.14-64\python.exe" "\\file-server\Shared Folder\Scripts\AccessibilityCheck\accessibility_check.py"

echo.
if %ERRORLEVEL% EQU 0 (
    echo  All done.
) else (
    echo  Something went wrong. Check the output above.
)

echo.
pause
