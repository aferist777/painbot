@echo off
chcp 65001 >nul
set PYTHONUTF8=1
cd /d %~dp0
if not exist .venv (
    echo [painbot] creating venv...
    py -3 -m venv .venv
    call .venv\Scripts\activate.bat
    python -m pip install --upgrade pip -q
    pip install -r requirements.txt
) else (
    call .venv\Scripts\activate.bat
)
python -m app.bot.main
pause
