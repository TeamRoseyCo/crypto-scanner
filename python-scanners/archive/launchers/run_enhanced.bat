@echo off
title Enhanced Scan v2.1
color 0B

echo.
echo ================================================================================
echo   ENHANCED SCAN v2.1
echo   19 indicators x 6 timeframes (1H/2H/4H/6H/12H/1D) - Bybit data
echo   SuperTrend, EMA, RSI, MACD, ADX, BB, Aroon, ATS, B-Trend, PctST,
echo   StochRSI, Ichimoku, CMF, OBV, MFI, CCI, Hull MA, PSAR, Volume Surge
echo   A11: 1D RSI<78 gate  |  A12: Vol Surge signal  |  A13: RS vs BTC bonus
echo   ~5-8 minutes runtime
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

:: ── Run Enhanced Scan ─────────────────────────────────────────────────────────
%VENV_PYTHON% ..\tradingview\enhanced_scan.py %*

if %errorlevel% neq 0 (
    echo.
    echo ================================================================================
    echo   ERROR: Enhanced Scan exited with an error (code %errorlevel%).
    echo ================================================================================
)

echo.
echo ================================================================================
echo   Done.
echo   Tip: STRONG signals = 5+/6 TFs aligned + DMI bull + score 120+
echo ================================================================================
echo.
pause
