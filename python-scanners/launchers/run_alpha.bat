@echo off
title Alpha Breakout Scanner v1.0
color 0B

echo.
echo ================================================================================
echo   ALPHA BREAKOUT SCANNER v1.0
echo   RS decoupling plays - tokens beating BTC regardless of regime
echo ================================================================================
echo.

:: ── Virtual environment Python (direct path) ──────────────────────────────────
set VENV_PYTHON="C:\Users\bruno\OneDrive\Ambiente de Trabalho\Workspace\ankh\.venv\Scripts\python.exe"

if not exist %VENV_PYTHON% (
    echo ERROR: Virtual environment not found at:
    echo   %VENV_PYTHON%
    echo.
    echo Please check the path and try again.
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

:: ── Run alpha scanner ─────────────────────────────────────────────────────────
%VENV_PYTHON% ..\engine\alpha_scanner.py %*

if %errorlevel% neq 0 (
    echo.
    echo ================================================================================
    echo   ERROR: Alpha scanner exited with an error (code %errorlevel%).
    echo   Check the log file in outputs/logs/ for details.
    echo ================================================================================
)

echo.
echo ================================================================================
echo   Scan complete. Check outputs/scanner-results/alpha_scan_LATEST.txt
echo ================================================================================
echo.
pause
