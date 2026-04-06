@echo off
title Alpha Signal Agent - Claude AI
color 0A

echo.
echo ================================================================================
echo   ALPHA SIGNAL AGENT - Powered by Claude
echo   Live scanner data + AI trading analyst
echo ================================================================================
echo.

:: ── Virtual environment Python (direct path) ──────────────────────────────────
set VENV_PYTHON="C:\Users\bruno\OneDrive\Ambiente de Trabalho\Workspace\ankh\.venv\Scripts\python.exe"

if not exist %VENV_PYTHON% (
    echo ERROR: Virtual environment not found.
    pause
    exit /b 1
)

echo Virtual environment OK.
echo.

:: ── Check API key is set ──────────────────────────────────────────────────────
if "%ANTHROPIC_API_KEY%"=="" (
    echo ERROR: ANTHROPIC_API_KEY is not set.
    echo Run:  setx ANTHROPIC_API_KEY "sk-ant-your-key-here"
    echo Then restart this terminal.
    pause
    exit /b 1
)

:: ── Launch agent ──────────────────────────────────────────────────────────────
%VENV_PYTHON% ..\engine\trade_agent.py

echo.
pause
