@echo off
title Alpha Breakout Scanner v1.0
color 0B

echo.
echo ================================================================================
echo   ALPHA BREAKOUT SCANNER v1.0
echo   RS decoupling plays - tokens beating BTC regardless of regime
echo ================================================================================
echo.

:: ── Activate virtual environment ────────────────────────────────────────────
set VENV_ACTIVATE="C:\Users\bruno\OneDrive\Ambiente de Trabalho\Workspace\ankh\.venv\Scripts\activate.bat"

if not exist %VENV_ACTIVATE% (
    echo ERROR: Virtual environment not found at:
    echo   %VENV_ACTIVATE%
    echo.
    echo Please check the path and try again.
    pause
    exit /b 1
)

call %VENV_ACTIVATE%
if %errorlevel% neq 0 (
    echo ERROR: Failed to activate virtual environment.
    pause
    exit /b 1
)

echo Virtual environment activated.
echo.

:: ── CoinGecko Demo API key ───────────────────────────────────────────────────
set CG_DEMO_KEY=CG-oEG3MATjJ1ShQN3xnkJDcGVS

:: ── Run alpha scanner ────────────────────────────────────────────────────────
python ..\engine\alpha_scanner.py

if %errorlevel% neq 0 (
    echo.
    echo ================================================================================
    echo   ERROR: Alpha scanner exited with an error (code %errorlevel%).
    echo   Check the log file in outputs/logs/ for details.
    echo ================================================================================
)

echo.
echo ================================================================================
echo   Scan complete.  Check outputs/scanner-results/alpha_scan_LATEST.txt
echo ================================================================================
echo.
pause
