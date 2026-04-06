@echo off
title Ignition Radar v1.1
color 0B

echo.
echo ================================================================================
echo   IGNITION RADAR v1.1
echo   Early volume/breakout watchlist - rank 5-700, 1h candles
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

:: ── Run ignition radar ────────────────────────────────────────────────────────
%VENV_PYTHON% ..\engine\ignition_radar.py --account 96700

if %errorlevel% neq 0 (
    echo.
    echo ================================================================================
    echo   ERROR: Ignition radar exited with an error (code %errorlevel%).
    echo   Check outputs/logs/ for details.
    echo ================================================================================
)

echo.
echo ================================================================================
echo   Done. Check outputs/scanner-results/ignition_radar_LATEST.txt
echo ================================================================================
echo.
pause
