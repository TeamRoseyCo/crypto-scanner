@echo off
title Bybit Sync - Trade Journal Auto-Import
color 0D

echo.
echo ================================================================================
echo   BYBIT SYNC v1.0
echo   Auto-imports closed trades from Bybit into Trade Journal
echo   Fixes circuit breaker gap - no more manual logging
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
echo.

:: ── Bybit API keys ───────────────────────────────────────────────────────────
:: Set these once with:
::   setx BYBIT_API_KEY "your_key_here"
::   setx BYBIT_API_SECRET "your_secret_here"
:: Then restart terminal.

if "%BYBIT_API_KEY%"=="" (
    echo ERROR: BYBIT_API_KEY not set.
    echo.
    echo 1. Go to: https://www.bybit.com/app/user/api-management
    echo 2. Create a new API key with READ-ONLY permissions
    echo 3. Run these commands:
    echo      setx BYBIT_API_KEY "your_key_here"
    echo      setx BYBIT_API_SECRET "your_secret_here"
    echo 4. Restart this terminal
    echo.
    pause
    exit /b 1
)

:: ── Run sync (single pass — use --interval 300 for continuous) ───────────────
%VENV_PYTHON% ..\engine\bybit_sync.py --once --days 7

echo.
echo ================================================================================
echo   Sync complete. Run "run_journal.bat" to see updated stats.
echo ================================================================================
echo.
pause
