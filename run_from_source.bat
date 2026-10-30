@echo off
rem Runs the app straight from the source code (needs Python 3.11+ installed).
setlocal
cd /d "%~dp0"
if not exist .venv (
    py -3.12 -m venv .venv || python -m venv .venv || goto :error
    call .venv\Scripts\activate.bat || goto :error
    python -m pip install -r requirements.txt || goto :error
) else (
    call .venv\Scripts\activate.bat || goto :error
)
start "" pythonw run_app.py
exit /b 0
:error
echo Setup failed. Is Python 3.11 or newer installed? https://www.python.org/downloads/
pause
exit /b 1
