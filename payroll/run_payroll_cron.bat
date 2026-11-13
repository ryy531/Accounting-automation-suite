@echo off
REM ────────────────────────────────────────────────────────────
REM Payroll Auto-Create-Period Cron Job
REM Triggered daily at 6:00 AM by Windows Task Scheduler.
REM Creates payroll engagements in NocoDB for all due periods.
REM
REM No drive-letter mapping required -- uses pushd for UNC paths.
REM
REM Task Scheduler config:
REM   General:    Run whether user is logged on or not
REM               Run with highest privileges
REM   Triggers:   Daily at 6:00 AM
REM   Actions:    Program/script: \\file-server\Shared Folder\Scripts\payroll\run_payroll_cron.bat
REM   Settings:   Stop the task if it runs longer than 30 minutes
REM               If the task is already running: Do not start a new instance
REM               (cron.py internal lock file is a secondary guard)
REM ────────────────────────────────────────────────────────────

setlocal

REM Local Python (no network dependency)
set PYTHON_EXE=python

REM Timestamp (no wmic -- avoids deprecated tool and pipe issues)
for /f "tokens=1-3 delims=/-" %%A in ("%DATE:~4%") do set DS=%%A%%B%%C
for /f "tokens=1-3 delims=.: " %%A in ("%TIME%") do (
    set TS_H=0%%A
    set TS_M=0%%B
    set TS_S=0%%C
)
set TIMESTAMP=%DS%_%TS_H:~-2%%TS_M:~-2%%TS_S:~-2%

REM ── pushd FIRST: maps UNC → temp drive letter, all later paths use %CD% ──
pushd "%~dp0..\_lib"
if errorlevel 1 (
    echo ERROR: pushd failed for %~dp0..\_lib >> "%TEMP%\payroll_cron_pushd_fail_%TIMESTAMP%.log"
    endlocal & exit /b 3
)

REM Now %CD% is a drive-letter path (e.g. Y:\Scripts\_lib), not UNC
set LOG_DIR=%CD%\logs
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"
set LOG_FILE=%LOG_DIR%\payroll_cron_bat_%TIMESTAMP%.log

echo ============================================================ > "%LOG_FILE%"
echo Payroll cron .bat starting at %DATE% %TIME% >> "%LOG_FILE%"
echo python:     %PYTHON_EXE% >> "%LOG_FILE%"
echo cwd:        %CD% >> "%LOG_FILE%"
echo log_file:   %LOG_FILE% >> "%LOG_FILE%"
echo ============================================================ >> "%LOG_FILE%"

REM Run — stdout and stderr both append to log
"%PYTHON_EXE%" -m server.cron >> "%LOG_FILE%" 2>&1
set EXITCODE=%ERRORLEVEL%

REM Log finish BEFORE popd (drive letter still valid)
echo. >> "%LOG_FILE%"
echo ============================================================ >> "%LOG_FILE%"
echo Finished at %DATE% %TIME% with exit code %EXITCODE% >> "%LOG_FILE%"
echo ============================================================ >> "%LOG_FILE%"

popd
endlocal & exit /b %EXITCODE%
