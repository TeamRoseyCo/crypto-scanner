@echo off
title Short Scanner v1.1
color 0C

echo.
echo ================================================================================
echo   SHORT SCANNER v1.1
echo   Bearish setups on Bybit perps
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

:: ── Run short scanner ─────────────────────────────────────────────────────────
%VENV_PYTHON% ..\engine\short_scanner.py --account 96700

if %errorlevel% neq 0 (
    echo.
    echo ================================================================================
    echo   ERROR: Short scanner exited with an error (code %errorlevel%).
    echo   Check outputs/logs/ for details.
    echo ================================================================================
)

echo.
echo ================================================================================
echo   Done. Check outputs/scanner-results/short_scan_LATEST.txt
echo ================================================================================
echo.
pause
