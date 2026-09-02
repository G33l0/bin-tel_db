@echo off
REM Run BIN-TEL from source (no build required).
setlocal
cd /d "%~dp0"

where python >nul 2>&1
if errorlevel 1 (
    echo Python was not found on PATH. Install Python 3.9+ and try again.
    pause
    exit /b 1
)

python -c "import rich" >nul 2>&1
if errorlevel 1 (
    echo Installing dependencies...
    python -m pip install -r requirements.txt || exit /b 1
)

python bin_tool.py %*
endlocal
