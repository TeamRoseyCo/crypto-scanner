@echo off
title Spot Scanner v2.0 — 15-Signal Heavy Scan
color 0E

echo.
echo ================================================================================
echo   SPOT SCANNER v2.0
echo   15 signals, CoinGecko, regime-aware entry gate (sideways: conv ^>=60)
echo   ~30-40 min runtime  ^|  Run once per session
echo   Output: outputs/scanner-results/spot_trade_plan_LATEST.txt
echo ================================================================================
echo.

:: ── Virtual environment Python ───────────────────────────────────────────────
set VENV_PYTHON="C:\Users\bruno\OneDrive\Ambiente de Trabalho\Workspace\ankh\.venv\Scripts\python.exe"

if not exist %VENV_PYTHON% (
    echo ERROR: Virtual environment not found at:
    echo   %VENV_PYTHON%
    pause
    exit /b 1
)

echo Virtual environment OK.

:: ── CoinGecko Demo API key (from environment variable, NOT hardcoded) ───────
if "%CG_DEMO_KEY%"=="" (
    echo.
    echo WARNING: CG_DEMO_KEY env var not set. CoinGecko calls may hit rate limit.
    echo To set persistently:
    echo   [System.Environment]::SetEnvironmentVariable("CG_DEMO_KEY", "your_key_here", "User")
    echo Then restart this terminal.
    echo.
    echo Continuing without key...
) else (
    echo CoinGecko Demo key OK.
)

echo.

:: ── Run spot scanner ────────────────────────────────────────────────────────
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
