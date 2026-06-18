@echo off
setlocal EnableDelayedExpansion
title Master Radar v3.8 - Longs + Shorts + Tracker

chcp 65001 >nul
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"
color 0B

echo.
echo ================================================================================
echo   MASTER RADAR v3.8
echo   ignition + perp + trend + SHORT + tracker
echo   ~3-20 min  ^|  Bybit perps + Binance spot
echo ================================================================================
echo.

:: ---- Parse args ----------------------------------------------------------
set "RUN_SHORTS=1"
set "RUN_TRACKER=1"
set "LONGS_ARGS="
set "SHORTS_ARGS="

:parse_loop
if "%~1"=="" goto parse_done
if /I "%~1"=="--no-shorts"     goto opt_no_shorts
if /I "%~1"=="--no-tracker"    goto opt_no_tracker
if /I "%~1"=="--no-rerun"      goto opt_no_rerun
if /I "%~1"=="--include-spot"  goto opt_include_spot
if /I "%~1"=="--account"       goto opt_account
if /I "%~1"=="--skip"          goto opt_skip
if /I "%~1"=="--no-cache"      goto opt_no_cache
if /I "%~1"=="--top"           goto opt_top
echo   (notice) Unknown arg ignored: %~1
shift
goto parse_loop

:opt_no_shorts
set "RUN_SHORTS=0"
shift
goto parse_loop

:opt_no_tracker
set "RUN_TRACKER=0"
shift
goto parse_loop

:opt_no_rerun
set "LONGS_ARGS=%LONGS_ARGS% --no-rerun"
shift
goto parse_loop

:opt_include_spot
set "LONGS_ARGS=%LONGS_ARGS% --include-spot"
shift
goto parse_loop

:opt_account
set "LONGS_ARGS=%LONGS_ARGS% --account %~2"
shift
shift
goto parse_loop

:opt_skip
set "LONGS_ARGS=%LONGS_ARGS% --skip %~2"
shift
shift
goto parse_loop

:opt_no_cache
set "SHORTS_ARGS=%SHORTS_ARGS% --no-cache"
shift
goto parse_loop

:opt_top
set "SHORTS_ARGS=%SHORTS_ARGS% --top %~2"
shift
shift
goto parse_loop

:parse_done

:: ---- Paths ---------------------------------------------------------------
set "SCRIPT_DIR=%~dp0"
set "PROJECT_ROOT=%SCRIPT_DIR%.."
set "VENV_PYTHON=C:\Users\bruno\OneDrive\Ambiente de Trabalho\Workspace\ankh\.venv\Scripts\python.exe"
set "SCANNER_DIR=%SCRIPT_DIR%..\engine\scanner_v3"
set "RUN_SCAN=%SCANNER_DIR%\run_scan.py"
set "SHORT_SCANNER=%SCANNER_DIR%\short_scanner.py"
set "TRACKER=%SCANNER_DIR%\signal_tracker.py"
set "RESULTS_DIR=%PROJECT_ROOT%\outputs\scanner-results"
set "LOG_DIR=%PROJECT_ROOT%\outputs\logs"

if not exist "%VENV_PYTHON%" goto err_no_venv
if not exist "%RUN_SCAN%"    goto err_no_runscan
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%" >nul 2>&1

echo Virtual environment OK.
echo Shorts:  %RUN_SHORTS%   Tracker: %RUN_TRACKER%
echo Longs args :%LONGS_ARGS%
echo Shorts args:%SHORTS_ARGS%
echo.

:: ---- STEP 1: longs orchestrator -----------------------------------------
echo --------------------------------------------------------------------------------
echo  STEP 1/3: longs orchestrator (ignition + perp + trend)
echo --------------------------------------------------------------------------------
call "%VENV_PYTHON%" "%RUN_SCAN%" %LONGS_ARGS%
set "EXIT_LONGS=%errorlevel%"
if not %EXIT_LONGS% == 0 goto err_longs_failed

:: ---- STEP 2: short scanner ----------------------------------------------
set "EXIT_SHORTS=0"
if "%RUN_SHORTS%"=="0" goto skip_shorts_disabled
if not exist "%SHORT_SCANNER%" goto skip_shorts_missing

echo.
echo --------------------------------------------------------------------------------
echo  STEP 2/3: short scanner (bearish setups, Bybit perps)
echo --------------------------------------------------------------------------------
call "%VENV_PYTHON%" "%SHORT_SCANNER%" %SHORTS_ARGS%
set "EXIT_SHORTS=%errorlevel%"
if not %EXIT_SHORTS% == 0 echo   (WARN) short scanner failed exit=%EXIT_SHORTS% - continuing
goto step3

:skip_shorts_disabled
echo.
echo   (SKIP) shorts disabled via --no-shorts
goto step3

:skip_shorts_missing
echo.
echo   (SKIP) short_scanner.py not found at %SHORT_SCANNER%
goto step3

:step3

:: ---- STEP 3: tracker record ---------------------------------------------
set "EXIT_TRACK=0"
if "%RUN_TRACKER%"=="0" goto skip_tracker_disabled
if not exist "%TRACKER%" goto skip_tracker_missing

echo.
echo --------------------------------------------------------------------------------
echo  STEP 3/3: signal tracker - record new WATCH NOW entries
echo --------------------------------------------------------------------------------
call "%VENV_PYTHON%" "%TRACKER%" record
set "EXIT_TRACK=%errorlevel%"
if not %EXIT_TRACK% == 0 echo   (WARN) tracker recording failed exit=%EXIT_TRACK%
goto done

:skip_tracker_disabled
echo.
echo   (SKIP) tracker disabled via --no-tracker
goto done

:skip_tracker_missing
echo.
echo   (SKIP) signal_tracker.py not found at %TRACKER%
goto done

:done
echo.
color 0A
echo ================================================================================
echo   DONE.  Reports in: %RESULTS_DIR%
echo     ignition_v3_LATEST.txt
echo     perp_v3_LATEST.txt
echo     trend_v3_LATEST.txt
echo     short_v3_LATEST.txt        (if shorts ran)
echo   Tracker DB: %PROJECT_ROOT%\outputs\tracker\signals.sqlite
echo ================================================================================
echo.
pause
goto :eof

:: ---- Error labels -------------------------------------------------------
:err_no_venv
echo ERROR: venv not found at %VENV_PYTHON%
pause
exit /b 1

:err_no_runscan
echo ERROR: run_scan.py not found at %RUN_SCAN%
pause
exit /b 1

:err_longs_failed
color 0C
echo.
echo ================================================================================
echo   LONGS ORCHESTRATOR FAILED  exit=%EXIT_LONGS% - skipping remaining steps
echo ================================================================================
pause
exit /b %EXIT_LONGS%
