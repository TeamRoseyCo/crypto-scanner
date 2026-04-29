@echo off
title Spot Scanner v2.0 — 15-Signal Heavy Scan
color 0E

echo.
echo ================================================================================
echo   SPOT SCANNER v2.0
echo   15 signals, CoinGecko, regime-aware entry gate (sideways: conv >=60)
echo   ~30-40 min runtime  ^|  Run once per session
echo   Output: outputs/scanner-results/spot_trade_plan_LATEST.txt
echo ================================================================================
echo.

:: Activate venv
set VENV_PYTHON="C:\Users\bruno\OneDrive\Ambiente de Trabalho\Workspace\ankh\.venv\Scripts\python.exe"

if not exist %VENV_PYTHON% (
    echo ERROR: Virtual environment not found at:
    echo   %VENV_PYTHON%
    pause
    exit /b 1
)

echo Virtual environment OK.
echo.

:: CoinGecko Demo API key
set CG_DEMO_KEY=CG-VMU55ZMLpBrBeQBKfPwknWTa

:: Run spot scanner
%VENV_PYTHON% ..\engine\spot_scanner.py %*

if %errorlevel% neq 0 (
    echo.
    echo ================================================================================
    echo   ERROR: Spot scanner exited with code %errorlevel%
    echo ================================================================================
)

echo.
echo ================================================================================
echo   Done. Report: outputs/scanner-results/spot_trade_plan_LATEST.txt
echo ================================================================================
echo.
pause
