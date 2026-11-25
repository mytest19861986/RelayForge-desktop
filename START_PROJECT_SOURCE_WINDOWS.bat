@echo off
cd /d "%~dp0"
call RUN_FROM_SOURCE_WINDOWS.bat
exit /b %ERRORLEVEL%
