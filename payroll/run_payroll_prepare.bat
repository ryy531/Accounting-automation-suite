@echo off
setlocal EnableDelayedExpansion
REM Payroll Prepare Runner
REM Default: dry-run mode. Add --apply to actually create engagements.

for /f "tokens=1-3 delims=/-" %%A in ("%DATE:~4%") do set DS=%%A%%B%%C
for /f "tokens=1-3 delims=.: " %%A in ("%TIME%") do (
    set TS_H=0%%A
    set TS_M=0%%B
    set TS_S=0%%C
)
set TIMESTAMP=%DS%_%TS_H:~-2%%TS_M:~-2%%TS_S:~-2%

pushd "\\file-server\Shared Folder\Scripts\_lib"
if errorlevel 1 (
    echo ERROR: pushd failed for \\file-server\Shared Folder\Scripts\_lib
    exit /b 3
)

set LOG_DIR=%CD%\logs
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"
set LOG_FILE=%LOG_DIR%\run_payroll_prepare_%TIMESTAMP%.log

echo ============================================================ > "%LOG_FILE%"
echo Payroll Prepare Runner starting at %DATE% %TIME% >> "%LOG_FILE%"
echo args: %* >> "%LOG_FILE%"
echo ============================================================ >> "%LOG_FILE%"

"..\.venv314\Scripts\python.exe" server\payroll_prepare.py %* >> "%LOG_FILE%" 2>&1
set EXITCODE=!ERRORLEVEL!

echo. >> "%LOG_FILE%"
echo Finished at %DATE% %TIME% with exit code %EXITCODE% >> "%LOG_FILE%"

popd

if "%~1"=="" (
    echo.
    echo Press any key to exit...
    pause > nul
)

exit /b %EXITCODE%
