@echo off
setlocal
title Daily Loss Gate - PnL Sync
chcp 65001 >nul
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"
color 0E

echo.
echo ================================================================================
echo   DAILY LOSS GATE  -  refresh today.json before ANY entry
echo   Pulls today's realized PnL from Bybit; writes the gate the AI reads.
echo ================================================================================
echo.

set "SCRIPT_DIR=%~dp0"
set "VENV_PYTHON=C:\Users\bruno\OneDrive\Ambiente de Trabalho\Workspace\ankh\.venv\Scripts\python.exe"
set "GATE=%SCRIPT_DIR%..\engine\scanner_v3\daily_pnl_tracker.py"
set "TODAY=%SCRIPT_DIR%..\outputs\daily_pnl\today.json"

if not exist "%VENV_PYTHON%" goto err_no_venv
if not exist "%GATE%" goto err_no_gate

call "%VENV_PYTHON%" "%GATE%"
set "EXIT_GATE=%errorlevel%"

echo.
echo -------------------------------- today.json ------------------------------------
if exist "%TODAY%" ( type "%TODAY%" ) else ( echo   (today.json not found - gate NOT written!) )
echo.
echo --------------------------------------------------------------------------------
if not %EXIT_GATE% == 0 (
  color 0C
  echo   WARNING: gate script exit=%EXIT_GATE% - treat gate as STALE, do NOT enter.
) else (
  color 0A
  echo   Gate refreshed. Confirm date_utc = TODAY and status = OK before any entry.
)
echo --------------------------------------------------------------------------------
echo.
pause
goto :eof

:err_no_venv
color 0C
echo ERROR: venv not found at %VENV_PYTHON%
pause
exit /b 1

:err_no_gate
color 0C
echo ERROR: daily_pnl_tracker.py not found at %GATE%
pause
exit /b 1
