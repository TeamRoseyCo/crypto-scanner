@echo off
title Crypto Scanner Pipeline v1.0
color 0A

echo.
echo ================================================================================
echo   CRYPTO SCANNER PIPELINE v1.0
echo   pump_hunter + prime_key pre-filter  ^|  ~50 min vs ~130 min standalone
echo ================================================================================
echo.

:: ── Activate virtual environment ────────────────────────────────────────────
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

:: ── CoinGecko Demo API key (30 req/min dedicated) ────────────────────────────
set CG_DEMO_KEY=CG-oEG3MATjJ1ShQN3xnkJDcGVS

:: ── Run pipeline ─────────────────────────────────────────────────────────────
:: To pass a custom account size:  %VENV_PYTHON% ..\engine\pipeline.py --account 96700

%VENV_PYTHON% ..\engine\pipeline.py --account 96700

if %errorlevel% neq 0 (
    echo.
    echo ================================================================================
    echo   ERROR: Pipeline exited with an error (code %errorlevel%).
    echo   Check the log file in outputs/logs/ for details.
    echo ================================================================================
)

echo.
echo ================================================================================
echo   Scan complete.  Check outputs/scanner-results/master_trade_plan_LATEST.txt
echo ================================================================================
echo.
pause
