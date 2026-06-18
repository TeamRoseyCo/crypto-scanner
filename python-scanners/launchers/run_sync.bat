@echo off
title Trade Journal Sync — Daily Import
color 0D

echo.
echo ================================================================================
echo   TRADE JOURNAL SYNC
echo   Auto-imports closed trades from Bybit using stopOrderType classification
echo   ~30 seconds  ^|  Idempotent (safe to run multiple times)
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

:: ── Verify Bybit API keys are present ────────────────────────────────────────
if "%BYBIT_API_KEY%"=="" (
    echo.
    echo ERROR: BYBIT_API_KEY env var not set.
    echo To set persistently:
    echo   [System.Environment]::SetEnvironmentVariable("BYBIT_API_KEY",    "...", "User")
    echo   [System.Environment]::SetEnvironmentVariable("BYBIT_API_SECRET", "...", "User")
    echo Then restart this terminal.
    pause
    exit /b 1
)

echo Bybit credentials OK.
echo.

:: ── Run sync (7-day window, non-interactive auto-classification) ────────────
%VENV_PYTHON% ..\engine\scanner_v3\trade_journal_sync.py --days 7 --non-interactive

if %errorlevel% neq 0 (
    echo.
    echo ================================================================================
    echo   ERROR: Sync exited with code %errorlevel%
    echo ================================================================================
)

echo.
echo ================================================================================
echo   Sync complete. Run "run_journal.bat" to see updated stats.
echo ================================================================================
echo.
pause
