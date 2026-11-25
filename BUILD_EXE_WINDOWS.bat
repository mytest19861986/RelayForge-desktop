@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title RelayForge 3.0 - Windows EXE Builder (Nuitka)
color 0B

echo ====================================================
echo      RelayForge 3.0 - BUILD NATIVE WINDOWS EXE
echo ====================================================
echo.
echo WARNING: Build on Windows Server 2019 / Windows 10 or 11.
echo         Python 3.12 x64 and Internet are recommended.
echo.

if not exist ".venv\Scripts\python.exe" (
    py -3.12 -m venv .venv 2>nul
    if errorlevel 1 py -3.13 -m venv .venv 2>nul
    if errorlevel 1 (
        echo [ERROR] Python 3.12 or 3.13 is required. Install Python 3.12 x64.
        goto error
    )
)

call ".venv\Scripts\activate.bat"
if errorlevel 1 goto error
python -m pip install --upgrade pip
if errorlevel 1 goto error
python -m pip install -r requirements-dev.txt
if errorlevel 1 goto error

python -m pytest -q tests
if errorlevel 1 (
    echo [ERROR] Tests failed. Refusing to build.
    goto error
)

echo.
echo [BUILD] Nuitka onefile binary; source .py is NOT distributed in runtime ZIP.
python -m nuitka --mode=onefile --mingw64 --assume-yes-for-downloads --windows-console-mode=force --include-data-dir=web=web --include-package=uvicorn --include-package=telethon --output-dir=build_output --output-filename=RelayForge.exe main.py
if errorlevel 1 goto error
if not exist "build_output\RelayForge.exe" (
    echo [ERROR] EXE was not produced.
    goto error
)

if exist "release_runtime" rmdir /s /q "release_runtime"
mkdir "release_runtime"
copy /y "build_output\RelayForge.exe" "release_runtime\RelayForge.exe" >nul
copy /y "START_SERVER.bat" "release_runtime\START_SERVER.bat" >nul
copy /y "OPEN_DASHBOARD.bat" "release_runtime\OPEN_DASHBOARD.bat" >nul
copy /y "INSTALL_AUTO_START.ps1" "release_runtime\INSTALL_AUTO_START.ps1" >nul
copy /y "docs\RUNTIME_README_FA.md" "release_runtime\README_FA.md" >nul
if exist "RelayForge_v3.0.1_WINDOWS_EXE.zip" del /q "RelayForge_v3.0.1_WINDOWS_EXE.zip"
powershell -NoProfile -ExecutionPolicy Bypass -Command "Compress-Archive -LiteralPath 'release_runtime\RelayForge.exe','release_runtime\START_SERVER.bat','release_runtime\OPEN_DASHBOARD.bat','release_runtime\INSTALL_AUTO_START.ps1','release_runtime\README_FA.md' -DestinationPath 'RelayForge_v3.0.1_WINDOWS_EXE.zip' -Force"
if errorlevel 1 goto error

echo.
echo [SUCCESS] build_output\RelayForge.exe
echo [SUCCESS] RelayForge_v3.0.1_WINDOWS_EXE.zip
if not defined CI pause
exit /b 0

:error
echo.
echo [FAILED] See errors above. No successful executable is claimed.
if not defined CI pause
exit /b 1
