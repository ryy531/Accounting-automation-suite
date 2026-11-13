@echo off
chcp 65001 >nul
cd /d "%~dp0gewechat-channel"
call "..\venv312\Scripts\activate.bat"

:loop
echo [wechat-gewechat-channel] Starting WeChat GeweChat bridge...
python wechat_gewechat_channel.py
echo [wechat-gewechat-channel] Exited (code %ERRORLEVEL%) -- restarting in 5 seconds...
timeout /t 5 /nobreak >nul
goto loop
