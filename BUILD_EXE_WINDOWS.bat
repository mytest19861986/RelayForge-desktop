@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title RelayForge v3.0.2 - Python 3.12 Windows EXE Builder
color 0B

echo =========================================================
echo   RelayForge 3.0.2 - WINDOWS EXE BUILD [Python 3.12]
echo =========================================================
echo.
echo [INFO] This build uses a separate .venv-build312 environment.
echo [INFO] Your existing .venv, data, config and sessions are preserved.
echo.

rem Prefer Python Launcher (allows side-by-side 3.12 and 3.13).
set "PY_CMD="
py -3.12 -c "import sys,struct; assert sys.version_info[:2] == (3,12) and struct.calcsize('P') == 8" >nul 2>&1
if not errorlevel 1 set "PY_CMD=py -3.12"
if not defined PY_CMD (
    python -c "import sys,struct; assert sys.version_info[:2] == (3,12) and struct.calcsize('P') == 8" >nul 2>&1
    if not errorlevel 1 set "PY_CMD=python"
)
if not defined PY_CMD (
    echo [ERROR] 64-bit Python 3.12 was not found.
    echo [ACTION] Install Python 3.12 x64 from https://www.python.org/downloads/windows/
    echo [ACTION] Include the Python Launcher for Windows during installation.
    echo [ACTION] Run: py -0p
    echo [NOTE] Do NOT uninstall Python 3.13; both versions can coexist.
    goto error
)
echo [OK] Python 3.12 selected:
%PY_CMD% -c "import sys; print(sys.executable, sys.version.split()[0])"
if errorlevel 1 goto error

set "BUILD_PY=.venv-build312\Scripts\python.exe"
if not exist "%BUILD_PY%" (
    echo [SETUP] Creating isolated Python 3.12 build environment...
    %PY_CMD% -m venv ".venv-build312"
    if errorlevel 1 goto error
)
"%BUILD_PY%" -c "import sys,struct; assert sys.version_info[:2] == (3,12) and struct.calcsize('P') == 8" >nul 2>&1
if errorlevel 1 (
    echo [ERROR] .venv-build312 exists but does not contain Python 3.12 x64.
    echo [ACTION] Rename it as a backup, then run the builder again.
    goto error
)

echo [DEPS] Installing source, test, and build dependencies...
"%BUILD_PY%" -m pip install --upgrade pip
if errorlevel 1 goto error
"%BUILD_PY%" -m pip install -r requirements-dev.txt
if errorlevel 1 goto error

echo.
echo [TEST] Running full test suite with a private temporary directory...
set "RF_PYTEST_TEMP=%CD%\.pytest_build312_%RANDOM%_%RANDOM%"
"%BUILD_PY%" -m pytest -q tests --basetemp="%RF_PYTEST_TEMP%"
if errorlevel 1 (
    echo [ERROR] A test or test-cleanup step failed. Refusing to build.
    goto error
)
if exist "%RF_PYTEST_TEMP%" rmdir /s /q "%RF_PYTEST_TEMP%" >nul 2>&1

echo.
echo [BUILD] Compiling with Nuitka and MinGW64 on Python 3.12...
"%BUILD_PY%" -m nuitka --mode=onefile --mingw64 --assume-yes-for-downloads --windows-console-mode=force --include-data-dir=web=web --include-package=uvicorn --include-package=telethon --output-dir=build_output_py312 --output-filename=RelayForge.exe main.py
if errorlevel 1 goto error
if not exist "build_output_py312\RelayForge.exe" (
    echo [ERROR] Nuitka returned success but EXE was not created.
    goto error
)

if not exist "build_output" mkdir "build_output"
copy /y "build_output_py312\RelayForge.exe" "build_output\RelayForge.exe" >nul
if errorlevel 1 goto error

rem Stage only runtime files; do not package .py, local data, tokens, or sessions.
set "STAGE=release_stage_%RANDOM%_%RANDOM%"
mkdir "%STAGE%"
if errorlevel 1 goto error
copy /y "build_output\RelayForge.exe" "%STAGE%\RelayForge.exe" >nul
if errorlevel 1 goto error
copy /y "START_SERVER.bat" "%STAGE%\START_SERVER.bat" >nul
if errorlevel 1 goto error
copy /y "OPEN_DASHBOARD.bat" "%STAGE%\OPEN_DASHBOARD.bat" >nul
if errorlevel 1 goto error
copy /y "INSTALL_AUTO_START.ps1" "%STAGE%\INSTALL_AUTO_START.ps1" >nul
if errorlevel 1 goto error
copy /y "docs\RUNTIME_README_FA.md" "%STAGE%\README_FA.md" >nul
if errorlevel 1 goto error

set "FINAL_ZIP=RelayForge_v3.0.2_WINDOWS_EXE.zip"
powershell -NoProfile -ExecutionPolicy Bypass -Command "Compress-Archive -LiteralPath '%STAGE%\RelayForge.exe','%STAGE%\START_SERVER.bat','%STAGE%\OPEN_DASHBOARD.bat','%STAGE%\INSTALL_AUTO_START.ps1','%STAGE%\README_FA.md' -DestinationPath '%FINAL_ZIP%' -Force"
if errorlevel 1 goto error
rmdir /s /q "%STAGE%" >nul 2>&1

echo.
echo [SUCCESS] Windows executable: build_output\RelayForge.exe
echo [SUCCESS] Runtime archive: %FINAL_ZIP%
echo [INFO] No Python source files were included in the runtime archive.
if not defined CI pause
exit /b 0

:error
echo.
echo [FAILED] Review errors above. An EXE build is NOT claimed successful.
if not defined CI pause
exit /b 1
