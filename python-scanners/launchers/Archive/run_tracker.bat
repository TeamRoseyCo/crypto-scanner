@echo off
setlocal EnableDelayedExpansion
title Signal Tracker - daily outcome update

chcp 65001 >nul
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"
color 0B

:: ----------------------------------------------------------------------------
:: SIGNAL TRACKER - DAILY UPDATE
::   Walks every still-open tracked signal, fetches latest OHLCV, marks
::   stop/TP/time outcomes. Schedule this to run ~once per day.
::
::   To schedule via Task Scheduler:
::     schtasks /create /tn "CryptoSignalTracker" /tr "%~f0" /sc daily /st 09:00
:: ----------------------------------------------------------------------------

set "SCRIPT_DIR=%~dp0"
set "PROJECT_ROOT=%SCRIPT_DIR%.."
set "VENV_PYTHON=C:\Users\bruno\OneDrive\Ambiente de Trabalho\Workspace\ankh\.venv\Scripts\python.exe"
set "TRACKER=%SCRIPT_DIR%..\engine\scanner_v3\signal_tracker.py"

if not exist "%VENV_PYTHON%" (
    echo ERROR: venv not found
    pause
    exit /b 1
)
if not exist "%TRACKER%" (
    echo ERROR: tracker not found
    pause
    exit /b 1
)

echo.
echo --------------------------------------------------------------------------------
echo  Updating outcomes for open tracked signals...
echo --------------------------------------------------------------------------------
"%VENV_PYTHON%" "%TRACKER%" update
set "EX=%errorlevel%"

echo.
echo --------------------------------------------------------------------------------
echo  Latest report:
echo --------------------------------------------------------------------------------
"%VENV_PYTHON%" "%TRACKER%" report

if %EX% neq 0 (
    color 0C
    echo  Update step exited %EX%
    pause
    exit /b %EX%
)
echo.
pause
endlocal
