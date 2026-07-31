@echo off
REM ============================================================
REM  PRICE ALERT — BTC structural "go run the gate" ping
REM    Fires (beep + popup) when BTC reclaims 62000 = first sign
REM    the down-leg is failing. NOT an entry — cue to run the
REM    full macro + scanner gate (see precommitted_entry_trigger).
REM
REM    run_price_alert.bat                 default: BTC >= 62000
REM    run_price_alert.bat 58500 below     custom level/direction
REM ============================================================
setlocal
set LEVEL=%~1
set DIR=%~2
if "%LEVEL%"=="" set LEVEL=62000
if "%DIR%"=="" set DIR=above
"C:\Program Files\Python312\python.exe" "%~dp0price_alert_watch.py" --symbol BTCUSDT --category spot --level %LEVEL% --dir %DIR% --interval 180 --keep --label "BTC reclaim 62000 -> run macro + scanner gate (NOT an entry)"
endlocal
pause
