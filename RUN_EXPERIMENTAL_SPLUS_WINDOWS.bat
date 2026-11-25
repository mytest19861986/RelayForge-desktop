@echo off
setlocal
cd /d "%~dp0"
echo OPTIONAL THIRD-PARTY SPlusthon 1.1.4 - GPLv3 - experimental
set /p CONSENT=Type YES to install and run the optional package: 
if /i not "%CONSENT%"=="YES" exit /b 1
if not exist ".venv-run312\Scripts\python.exe" py -3.12 -m venv .venv-run312
if errorlevel 1 goto error
".venv-run312\Scripts\python.exe" -m pip install -r requirements.txt -r requirements-splus-optional.txt
if errorlevel 1 goto error
".venv-run312\Scripts\python.exe" main.py
if errorlevel 1 goto error
exit /b 0
:error
echo Failed. Check the first error above.
pause
exit /b 1
