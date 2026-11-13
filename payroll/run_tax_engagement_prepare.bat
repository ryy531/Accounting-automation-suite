@echo off
REM GST/PST/T2 Engagement Prepare Runner
REM Default: dry-run. Pass --apply for live creation and dynamic view refresh.

pushd "\\file-server\Shared Folder\Scripts\_lib"
if errorlevel 1 (
    echo ERROR: pushd failed for \\file-server\Shared Folder\Scripts\_lib
    exit /b 3
)

"..\.venv\Scripts\python.exe" server\tax_engagement_prepare.py %*
set EXITCODE=%ERRORLEVEL%

popd
exit /b %EXITCODE%
