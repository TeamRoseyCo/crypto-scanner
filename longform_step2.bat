@echo off
REM ============================================================
REM LONGFORM STEP 2 — Produce Video + Upload to YouTube
REM Run this MANUALLY after taking TradingView screenshots
REM ============================================================
setlocal EnableDelayedExpansion

set "BASE=C:\Users\bruno\OneDrive\Ambiente de Trabalho\Workspace\crypto scanner\crypto-scanner"
set "PYTHON=C:\Program Files\Python312\python.exe"
set "LOGDIR=%BASE%\outputs\logs"

for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmmss"') do set "TS=%%i"
set "LOG=%LOGDIR%\longform_step2_%TS%.log"
set "LATEST=%LOGDIR%\longform_step2_LATEST.log"

if not exist "%LOGDIR%" mkdir "%LOGDIR%"

call :log "============================================================"
call :log "LONGFORM STEP 2 — PRODUCE VIDEO + UPLOAD  (run: %TS%)"
call :log "============================================================"

if not exist "%PYTHON%" (
    call :log "FATAL: Python not found at %PYTHON%"
    goto :fail
)

cd /d "%BASE%\YOUTUBE - faceless channel" || (
    call :log "FATAL: Could not cd into YOUTUBE - faceless channel directory."
    goto :fail
)

REM — Check if charts exist for today —
for /f %%d in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd"') do set "TODAY=%%d"
set "CHART_DIR=%BASE%\YOUTUBE - faceless channel\Images for Videos\longform_charts\%TODAY%"

if exist "%CHART_DIR%\*.png" (
    call :log "Found real TradingView charts in %CHART_DIR%"
    for /f %%n in ('dir /b "%CHART_DIR%\*.png" 2^>nul ^| find /c /v ""') do call :log "  Chart files: %%n"
) else (
    call :log "WARNING: No charts found in %CHART_DIR%"
    call :log "  Pipeline will use synthetic frames as fallback."
    call :log "  To use real charts, save TradingView screenshots there first."
    echo.
    echo  No TradingView charts found for today.
    echo  Continue with synthetic frames? (Y/N)
    set /p "CONTINUE=  > "
    if /i not "!CONTINUE!"=="Y" (
        call :log "User chose to abort. Take screenshots first, then re-run."
        goto :fail
    )
)

REM — Produce video and upload —
call :log ""
call :log "Producing long-form video..."
"%PYTHON%" longform_pipeline.py --type auto >> "%LOG%" 2>&1
if errorlevel 1 (
    call :log "ERROR: Longform pipeline failed with exit code %errorlevel%."
    goto :fail
)

call :log ""
call :log "============================================================"
call :log "LONGFORM VIDEO COMPLETE — Check Videos folder for output"
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
    call :log "LONGFORM STEP 2 FAILED — see log above"
    copy /Y "%LOG%" "%LATEST%" >nul
    endlocal
    exit /b 1
