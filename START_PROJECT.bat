@echo off
cd /d "%~dp0"
if not exist "RelayForge.exe" (echo Build RelayForge.exe first & pause & exit /b 1)
"RelayForge.exe"
if errorlevel 1 pause
