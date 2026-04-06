@echo off
title Fast Scan v1.0
color 0E

echo.
echo ================================================================================
echo   FAST SCAN v1.0
echo   30-minute momentum scan — no OHLCV, pure market data (~15 seconds)
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

:: ── CoinGecko Demo API key ────────────────────────────────────────────────────
set CG_DEMO_KEY=CG-oEG3MATjJ1ShQN3xnkJDcGVS

:: ── Optional: Telegram alerts ─────────────────────────────────────────────────
:: set TELEGRAM_BOT_TOKEN=your_token_here
:: set TELEGRAM_CHAT_ID=your_chat_id_here

:: ── Run Fast Scan ─────────────────────────────────────────────────────────────
%VENV_PYTHON% ..\engine\fast_scan.py %*

if %errorlevel% neq 0 (
    echo.
    echo ================================================================================
    echo   ERROR: Fast Scan exited with an error (code %errorlevel%).
    echo   Check outputs/logs/ for details.
    echo ================================================================================
)

echo.
echo ================================================================================
echo   Done. Check outputs/scanner-results/fast_scan_LATEST.txt
echo   Tip: Run master scanner to confirm any tokens before entering.
echo ================================================================================
echo.
pause
