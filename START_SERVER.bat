@echo off
cd /d "%~dp0"
if exist "RelayForge.exe" (
   "RelayForge.exe" --no-browser
) else (
   echo RelayForge.exe not found. Build with BUILD_EXE_WINDOWS.bat first.
   echo In source mode use RUN_FROM_SOURCE_WINDOWS.bat.
   pause
)
