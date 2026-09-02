@echo off
setlocal
cd /d "%~dp0"

where python >nul 2>&1
if errorlevel 1 (
    echo Python was not found on PATH. Install Python 3.9+ and try again.
    exit /b 1
)

echo [1/3] Installing dependencies...
python -m pip install --upgrade pip || exit /b 1
python -m pip install -r requirements.txt pyinstaller || exit /b 1

echo [2/3] Cleaning previous build...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist

echo [3/3] Building single-file executable...
python -m PyInstaller --clean --noconfirm bin_tool.spec || exit /b 1

echo.
echo Done. The executable is dist\BIN-TEL.exe
echo Copy it anywhere; it creates config.json and the data\ folders on first run.
endlocal
