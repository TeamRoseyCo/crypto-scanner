@echo off
echo Activating virtual environment...
call "C:\Users\bruno\OneDrive\Ambiente de Trabalho\Workspace\ankh\.venv\Scripts\Activate.ps1"
if %errorlevel% neq 0 (
    echo Failed to activate virtual environment.
    pause
    exit /b 1
)
echo Running fetch_specific_tokens.py...
python fetch_specific_tokens.py
pause