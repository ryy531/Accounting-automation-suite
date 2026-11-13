@echo off
REM ────────────────────────────────────────────────────────────
REM Weekly To-Do Report for Alex
REM Builds Weekly_<date>.xlsx (NocoDB review/blocked/P0 + AI-extracted
REM chat to-dos) and delivers it via WhatsApp + email.
REM
REM Task Scheduler config (suggested):
REM   Triggers:  Weekly, every Friday at 12:00
REM   Actions:   \\file-server\Shared Folder\Scripts\WeeklyTodoReport\run_weekly_report.bat
REM   Settings:  Stop if runs longer than 10 minutes
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

REM pushd Scripts root so "python -m WeeklyTodoReport.*" resolves correctly
pushd "%~dp0.."
if errorlevel 1 (
    echo ERROR: pushd failed > "%TEMP%\weekly_report_pushd_fail_%TIMESTAMP%.log"
    endlocal & exit /b 3
)

set LOG_DIR=%CD%\WeeklyTodoReport\logs
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"
set LOG_FILE=%LOG_DIR%\weekly_report_%TIMESTAMP%.log

echo ============================================================ > "%LOG_FILE%"
echo Weekly To-Do Report starting at %DATE% %TIME% >> "%LOG_FILE%"
echo cwd: %CD% >> "%LOG_FILE%"
echo ============================================================ >> "%LOG_FILE%"

"%PYTHON_EXE%" -m WeeklyTodoReport.runners.weekly_report >> "%LOG_FILE%" 2>&1
set EXITCODE=%ERRORLEVEL%

echo. >> "%LOG_FILE%"
echo Finished at %DATE% %TIME% with exit code %EXITCODE% >> "%LOG_FILE%"

popd
endlocal & exit /b %EXITCODE%
