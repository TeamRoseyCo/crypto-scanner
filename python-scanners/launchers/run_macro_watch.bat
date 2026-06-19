@echo off
REM ============================================================
REM  MACRO WATCH (CONTINUOUS) — just double-click and leave open.
REM  Refreshes DXY + yields every 5 min and BEEPS + pops a window
REM  when the macro turns: DXY < 99 OR 10Y yield < 4.30%.
REM  That alarm = crypto can finally bottom -> re-engage the scanner.
REM ============================================================
setlocal
title MACRO WATCH - continuous (leave open)
chcp 65001 >nul
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"
"C:\Program Files\Python312\python.exe" "%~dp0macro_watch.py" --watch --interval 300 --alert-dxy-below 99.0 --alert-y10-below 4.30
endlocal
pause
