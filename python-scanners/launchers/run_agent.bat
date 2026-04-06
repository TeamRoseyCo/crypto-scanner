@echo off
title Alpha Signal Agent — Claude AI
color 0A

echo.
echo ================================================================================
echo   ALPHA SIGNAL AGENT — Powered by Claude
echo   Live scanner data + AI trading analyst
echo ================================================================================
echo.

:: ── Activate virtual environment ─────────────────────────────────────────────
set VENV_ACTIVATE="C:\Users\bruno\OneDrive\Ambiente de Trabalho\Workspace\ankh\.venv\Scripts\activate.bat"

if not exist %VENV_ACTIVATE% (
    echo ERROR: Virtual environment not found.
    pause
    exit /b 1
)

call %VENV_ACTIVATE%

:: ── Check API key is set ──────────────────────────────────────────────────────
if "%ANTHROPIC_API_KEY%"=="" (
    echo ERROR: ANTHROPIC_API_KEY is not set.
    echo Run:  setx ANTHROPIC_API_KEY "sk-ant-your-key-here"
    echo Then restart this terminal.
    pause
    exit /b 1
)

:: ── Launch agent ──────────────────────────────────────────────────────────────
python ..\engine\trade_agent.py

echo.
pause
