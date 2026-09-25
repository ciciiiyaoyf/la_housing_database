@echo off
cd /d "%~dp0"
set "HousingPython=.venv\Scripts\python.exe"
if not exist "%HousingPython%" (
    echo The project Python environment is missing.
    echo Follow the environment setup in README.md, then reopen this launcher.
    pause
    exit /b 1
)
"%HousingPython%" run_data_browser.py --open-browser
if errorlevel 1 pause
