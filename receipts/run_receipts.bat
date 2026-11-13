@echo off
chcp 65001 > nul

if "%~1"=="" (
    echo.
    echo 使用方法：把要处理的文件夹拖到这个.bat图标上。
    echo.
    pause
    exit /b 1
)

pushd "%~dp0\.."
REM 切到Scripts/ 目录，以便python-m receipts 能找到模块

set "GEMINI_MODEL=gemini-2.5-flash"

call .venv\Scripts\activate.bat 2>nul

python -m receipts "%~1"

if errorlevel 1 (
    echo.
    echo 处理失败，请联系脚本维护人员
    pause
    exit /b 1
)