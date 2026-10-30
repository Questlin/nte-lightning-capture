@echo off
rem Builds dist\NTELightningCapture.exe (a single file, no Python needed to run it).
setlocal
cd /d "%~dp0"
if not exist .venv (
    py -3.12 -m venv .venv || python -m venv .venv || goto :error
)
call .venv\Scripts\activate.bat || goto :error
python -m pip install --upgrade pip >nul
python -m pip install -r requirements-dev.txt || goto :error
python -m pytest -q || goto :error
pyinstaller --noconfirm --onefile --windowed --uac-admin --name NTELightningCapture ^
    --collect-submodules dxcam --collect-submodules comtypes run_app.py || goto :error
echo.
echo Built dist\NTELightningCapture.exe
exit /b 0
:error
echo Build failed.
exit /b 1
