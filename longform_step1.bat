@echo off
REM ============================================================
REM LONGFORM STEP 1 — Run Scanner + Generate Chart Shopping List
REM Runs at 06:30 on Mon/Wed/Fri
REM After this completes, check the shopping list and take screenshots
REM ============================================================
setlocal EnableDelayedExpansion

set "BASE=C:\Users\bruno\OneDrive\Ambiente de Trabalho\Workspace\crypto scanner\crypto-scanner"
set "PYTHON=C:\Program Files\Python312\python.exe"
set "LOGDIR=%BASE%\outputs\logs"

for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmmss"') do set "TS=%%i"
set "LOG=%LOGDIR%\longform_step1_%TS%.log"
set "LATEST=%LOGDIR%\longform_step1_LATEST.log"

if not exist "%LOGDIR%" mkdir "%LOGDIR%"

call :log "============================================================"
call :log "LONGFORM STEP 1 — SCANNER + SHOPPING LIST  (run: %TS%)"
call :log "============================================================"

if not exist "%PYTHON%" (
    call :log "FATAL: Python not found at %PYTHON%"
    goto :fail
)

REM — Step 1: Run scanners —
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

REM — Step 2: Generate script + shopping list (preview only, no video) —
call :log ""
call :log "[Step 2/2] Generating script + chart shopping list..."
cd /d "%BASE%\YOUTUBE - faceless channel" || (
    call :log "FATAL: Could not cd into YOUTUBE - faceless channel directory."
    goto :fail
)

"%PYTHON%" longform_pipeline.py --type auto --preview --no-upload >> "%LOG%" 2>&1
if errorlevel 1 (
    call :log "ERROR: Preview generation failed with exit code %errorlevel%."
    goto :fail
)
call :log "[Step 2/2] Shopping list generated."

REM — Done —
call :log ""
call :log "============================================================"
call :log "STEP 1 COMPLETE — Now take TradingView screenshots"
call :log "Check: YOUTUBE - faceless channel\Images for Videos\longform_charts\"
call :log "Then run: longform_step2.bat"
call :log "============================================================"

copy /Y "%LOG%" "%LATEST%" >nul
endlocal
exit /b 0

:log
    for /f %%t in ('powershell -NoProfile -Command "Get-Date -Format HH:mm:ss"') do set "NOW=%%t"
    echo [!NOW!] %~1
    echo [!NOW!] %~1>> "%LOG%"
    goto :eof

:fail
    call :log ""
    call :log "LONGFORM STEP 1 FAILED — see log above"
    copy /Y "%LOG%" "%LATEST%" >nul
    endlocal
    exit /b 1
