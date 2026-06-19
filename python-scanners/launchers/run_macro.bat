@echo off
REM ============================================================
REM  MACRO WATCH — DXY + Treasury yields gauge for crypto regime
REM    run_macro.bat            one-shot dashboard
REM    run_macro.bat watch      refresh loop every 5 min
REM ============================================================
setlocal
if /I "%~1"=="watch" (
  "C:\Program Files\Python312\python.exe" "%~dp0macro_watch.py" --watch --alert-dxy-below 99.0 --alert-y10-below 4.30
) else (
  "C:\Program Files\Python312\python.exe" "%~dp0macro_watch.py"
)
endlocal
pause
