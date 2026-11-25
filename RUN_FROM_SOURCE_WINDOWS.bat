@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv-run312\Scripts\python.exe" py -3.12 -m venv .venv-run312
if errorlevel 1 goto error
".venv-run312\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto error
".venv-run312\Scripts\python.exe" main.py
if errorlevel 1 goto error
exit /b 0
:error
echo Source runtime failed. See the error above.
pause
exit /b 1
