@echo off
REM ────────────────────────────────────────────────────────────
REM Annual Return Follow-up (15th of month)
REM Sends a follow-up to the group ONLY if there are overdue
REM unfiled clients from last month. Silent when all clear.
REM
REM Task Scheduler config:
REM   Triggers:  Monthly, day 15 at 09:00 SK
REM   Actions:   \\file-server\Shared Folder\Scripts\ReminderAutomation\run_annual_return_followup.bat
REM   Settings:  Stop if runs longer than 5 minutes
REM              Do not start a new instance if already running
REM ────────────────────────────────────────────────────────────

setlocal

REM Bare "python" on PATH can resolve to a different interpreter without
REM openpyxl installed (confirmed on this machine) -- use the project venv.
set PYTHON_EXE=.venv314\Scripts\python.exe

for /f "tokens=1-3 delims=/-" %%A in ("%DATE:~4%") do set DS=%%A%%B%%C
for /f "tokens=1-3 delims=.: " %%A in ("%TIME%") do (
    set TS_H=0%%A
    set TS_M=0%%B
    set TS_S=0%%C
)
set TIMESTAMP=%DS%_%TS_H:~-2%%TS_M:~-2%%TS_S:~-2%

pushd "%~dp0.."
if errorlevel 1 (
    echo ERROR: pushd failed > "%TEMP%\ar_followup_pushd_fail_%TIMESTAMP%.log"
    endlocal & exit /b 3
)

set LOG_DIR=%CD%\ReminderAutomation\logs
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"
set LOG_FILE=%LOG_DIR%\annual_return_followup_%TIMESTAMP%.log

echo ============================================================ > "%LOG_FILE%"
echo Annual Return Follow-up starting at %DATE% %TIME% >> "%LOG_FILE%"
echo cwd: %CD% >> "%LOG_FILE%"
echo ============================================================ >> "%LOG_FILE%"

"%PYTHON_EXE%" -m ReminderAutomation.runners.annual_return_followup >> "%LOG_FILE%" 2>&1
set EXITCODE=%ERRORLEVEL%

echo. >> "%LOG_FILE%"
echo Finished at %DATE% %TIME% with exit code %EXITCODE% >> "%LOG_FILE%"

popd
endlocal & exit /b %EXITCODE%
