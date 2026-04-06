@echo off
title Bybit Radar v1.0
color 0A

echo.
echo ================================================================================
echo   BYBIT RADAR v1.0
echo   OI + Funding velocity scanner - single Bybit API call, no rate limits
echo ================================================================================
echo.

:: ── Activate virtual environment ─────────────────────────────────────────────
set VENV_PYTHON="C:\Users\bruno\OneDrive\Ambiente de Trabalho\Workspace\ankh\.venv\Scripts\python.exe"

if not exist %VENV_PYTHON% (
    echo ERROR: Virtual environment not found at:
    echo   %VENV_PYTHON%
    pause
    exit /b 1
)

echo Virtual environment OK.
echo.

:: ── Optional: Telegram alerts ─────────────────────────────────────────────────
:: Uncomment and fill in to enable Telegram notifications:
:: set TELEGRAM_BOT_TOKEN=your_token_here
:: set TELEGRAM_CHAT_ID=your_chat_id_here

:: ── Run Bybit Radar ───────────────────────────────────────────────────────────
%VENV_PYTHON% ..\engine\bybit_radar.py %*

if %errorlevel% neq 0 (
    echo.
    echo ================================================================================
    echo   ERROR: Bybit Radar exited with an error (code %errorlevel%).
    echo   Check outputs/logs/ for details.
    echo ================================================================================
)

echo.
echo ================================================================================
echo   Done. Check outputs/scanner-results/bybit_radar_LATEST.txt
echo   Symbol set saved to cache/shared_ohlcv/bybit_symbols.json
echo ================================================================================
echo.
pause
