@echo off
title Trade Journal
color 0E

:: ── Activate virtual environment ─────────────────────────────────────────────
set VENV_PYTHON="C:\Users\bruno\OneDrive\Ambiente de Trabalho\Workspace\ankh\.venv\Scripts\python.exe"

if not exist %VENV_PYTHON% (
    echo ERROR: Virtual environment not found.
    pause
    exit /b 1
)

set JOURNAL=%VENV_PYTHON% ..\engine\trade_journal.py

:: ── No args → show stats ──────────────────────────────────────────────────────
if "%~1"=="" (
    %JOURNAL% stats
    pause
    exit /b 0
)

:: ── Pass all args through ─────────────────────────────────────────────────────
%JOURNAL% %*

if %errorlevel% neq 0 (
    echo.
    echo ERROR: journal exited with code %errorlevel%
)

echo.
pause
