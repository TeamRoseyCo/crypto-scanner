@echo off
title Pre-Pump Radar v1.0
color 0B

echo.
echo ================================================================================
echo   PRE-PUMP RADAR  v1.0
echo   Early accumulation detector  ^|  1h candles  ^|  Binance-only  ^|  no rate limits
echo   Signals: BB squeeze  OBV divergence  CMF  vol build  ATR coil  higher lows
echo ================================================================================
echo.
echo   WARNING: This is a WATCH list, not a trade signal.
echo   Confirm on master scanner before any entry.
echo.

:: ── Virtual environment Python ───────────────────────────────────────────────
set VENV_PYTHON="C:\Users\bruno\OneDrive\Ambiente de Trabalho\Workspace\ankh\.venv\Scripts\python.exe"

if not exist %VENV_PYTHON% (
    echo ERROR: Virtual environment not found at:
    echo   %VENV_PYTHON%
    echo.
    pause
    exit /b 1
)

echo Virtual environment OK.
echo.

:: ── Run ─────────────────────────────────────────────────────────────────────
%VENV_PYTHON% ..\engine\prepump_radar.py %*

if %errorlevel% neq 0 (
    echo.
    echo ================================================================================
    echo   ERROR: Pre-pump radar exited with code %errorlevel%.
    echo   Check outputs/logs/ for details.
    echo ================================================================================
)

echo.
echo ================================================================================
echo   Done. Check outputs/scanner-results/prepump_radar_LATEST.txt
echo ================================================================================
echo.
pause
