@echo off
REM ============================================================
REM  PRICE ALERT WATCHER launcher
REM  Usage examples:
REM    run_alert.bat MUUSDT 1071 below "MU pullback entry"
REM    run_alert.bat MUUSDT 1135 above "MU breakout"
REM    run_alert.bat XLMUSDT 0.2200 below "XLM ribbon pullback" spot
REM  Args: SYMBOL LEVEL DIR(below|above) LABEL [CATEGORY linear|spot]
REM ============================================================
setlocal
set SYMBOL=%~1
set LEVEL=%~2
set DIR=%~3
set LABEL=%~4
set CATEGORY=%~5
if "%CATEGORY%"=="" set CATEGORY=linear
if "%SYMBOL%"=="" (
  echo Usage: run_alert.bat SYMBOL LEVEL below^|above "LABEL" [linear^|spot]
  goto :eof
)
"C:\Program Files\Python312\python.exe" "%~dp0price_alert_watch.py" --symbol %SYMBOL% --level %LEVEL% --dir %DIR% --label "%LABEL%" --category %CATEGORY%
endlocal
pause
