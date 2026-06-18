@echo off
REM ============================================================
REM DAILY CRYPTO VIDEO — Automated Scanner + YouTube Pipeline
REM ============================================================
setlocal EnableDelayedExpansion

REM ── Paths ───────────────────────────────────────────────────
set "BASE=C:\Users\bruno\OneDrive\Ambiente de Trabalho\Workspace\crypto scanner\crypto-scanner"
set "PYTHON=C:\Program Files\Python312\python.exe"
set "LOGDIR=%BASE%\outputs\logs"

REM ── Build a stable timestamp (YYYYMMDD_HHMMSS) ──────────────
REM Uses PowerShell so we don't depend on locale-specific %date%/%time% formats.
for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmmss"') do set "TS=%%i"

set "LOG=%LOGDIR%\daily_video_%TS%.log"
set "LATEST=%LOGDIR%\daily_video_LATEST.log"

if not exist "%LOGDIR%" mkdir "%LOGDIR%"

REM ── Header ──────────────────────────────────────────────────
call :log "============================================================"
call :log "DAILY CRYPTO VIDEO PIPELINE  (run: %TS%)"
call :log "Log file: %LOG%"
call :log "============================================================"

REM ── Sanity check: Python exists ─────────────────────────────
if not exist "%PYTHON%" (
    call :log "FATAL: Python not found at %PYTHON%"
    goto :fail
)

REM ── Step 1: Run scanners ────────────────────────────────────
call :log ""
call :log "[Step 1/2] Running scanners..."
cd /d "%BASE%\python-scanners\engine\scanner_v3" || (
    call :log "FATAL: Could not cd into scanner_v3 directory."
    goto :fail
)

"%PYTHON%" run_scan.py --account 96700 >> "%LOG%" 2>&1
if errorlevel 1 (
    call :log "ERROR: Scanner failed with exit code %errorlevel%."
    goto :fail
)
call :log "[Step 1/2] Scanners complete."

REM ── Step 2: Generate video and upload to YouTube ────────────
call :log ""
call :log "[Step 2/2] Generating video and uploading..."
cd /d "%BASE%\YOUTUBE - faceless channel" || (
    call :log "FATAL: Could not cd into YOUTUBE - faceless channel directory."
    goto :fail
)

"%PYTHON%" video_pipeline\main.py >> "%LOG%" 2>&1
if errorlevel 1 (
    call :log "ERROR: Video pipeline failed with exit code %errorlevel%."
    goto :fail
)
call :log "[Step 2/2] Video pipeline complete."

REM ── Success ─────────────────────────────────────────────────
call :log ""
call :log "============================================================"
call :log "PIPELINE COMPLETED SUCCESSFULLY"
call :log "============================================================"

REM Copy this run's log to LATEST so you always know where to look
copy /Y "%LOG%" "%LATEST%" >nul

endlocal
exit /b 0

REM ============================================================
REM Helpers
REM ============================================================
:log
    REM Writes a timestamped line to both console and the log file.
    for /f %%t in ('powershell -NoProfile -Command "Get-Date -Format HH:mm:ss"') do set "NOW=%%t"
    echo [!NOW!] %~1
    echo [!NOW!] %~1>> "%LOG%"
    goto :eof

:fail
    call :log ""
    call :log "============================================================"
    call :log "PIPELINE FAILED — see log above"
    call :log "============================================================"
    copy /Y "%LOG%" "%LATEST%" >nul
    endlocal
    exit /b 1
