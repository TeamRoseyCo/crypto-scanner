@echo off
title Bybit Alert Loop — Live OI/Funding Monitor
color 0B

echo.
echo ================================================================================
echo   BYBIT ALERT LOOP v1.0
echo   Scans 430 Bybit perp pairs every 2 minutes
echo   Fires Telegram alert the moment OI spikes + signals fire
echo   Cooldown: 3h per coin  ^|  Re-alerts if score jumps 2+ or OI +50%%
echo   Press Ctrl+C to stop
echo ================================================================================
echo.

set VENV_PYTHON="C:\Users\bruno\OneDrive\Ambiente de Trabalho\Workspace\ankh\.venv\Scripts\python.exe"
set TELEGRAM_BOT_TOKEN=7665303397:AAHDXF0giiuTNCfbjdimfTthDp2keTnTGtA
set TELEGRAM_CHAT_ID=1287299443

if not exist %VENV_PYTHON% (
    echo ERROR: Virtual environment not found at:
    echo   %VENV_PYTHON%
    pause
    exit /b 1
)

echo Virtual environment OK.
echo Starting alert loop...
echo.

%VENV_PYTHON% ..\engine\bybit_alert_loop.py --interval 600 --threshold 7.0

echo.
echo ================================================================================
echo   Loop stopped.
echo ================================================================================
pause
