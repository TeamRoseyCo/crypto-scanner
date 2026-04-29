@echo off
title Master Radar v3.0 — Multi-Scanner Confluence
color 0B

echo.
echo ================================================================================
echo   MASTER RADAR v3.0
echo   Orchestrates ignition + perp + trend scanners
echo   ~2-15 min runtime  ^|  Bybit perps + Binance spot
echo   Output: outputs/scanner-results/master_radar_LATEST.txt
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

:: Run master orchestrator. Pass through any args (e.g. --include-spot)
%VENV_PYTHON% ..\engine\scanner_v3\run_scan.py %*

if %errorlevel% neq 0 (
    echo.
    echo ================================================================================
    echo   ERROR: Master radar exited with code %errorlevel%
    echo ================================================================================
)

echo.
echo ================================================================================
echo   Done. Reports:
echo     outputs/scanner-results/master_radar_LATEST.txt
echo     outputs/scanner-results/ignition_v3_LATEST.txt
echo     outputs/scanner-results/perp_v3_LATEST.txt
echo     outputs/scanner-results/trend_v3_LATEST.txt
echo ================================================================================
echo.
pause
