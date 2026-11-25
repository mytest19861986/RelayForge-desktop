@echo off
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" main.py
) else (
    py -3.12 -m pip install -r requirements.txt
    if errorlevel 1 goto error
    py -3.12 main.py
)
exit /b %errorlevel%
:error
echo Unable to run from source. Install Python 3.12 x64 and dependencies.
pause
exit /b 1
