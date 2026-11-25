@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv-run312\Scripts\python.exe" py -3.12 -m venv .venv-run312
if errorlevel 1 goto error
".venv-run312\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto error
powershell -NoProfile -Command "$n=Read-Host 'Project name (letters/numbers/dash)'; if($n -notmatch '^[a-zA-Z0-9_-]{1,40}$'){throw 'Invalid project name'}; $p=Read-Host 'Unique local port (8766-65535)'; if($p -notmatch '^\d{4,5}$' -or [int]$p -lt 8766 -or [int]$p -gt 65535){throw 'Invalid port'}; $d=Join-Path (Get-Location) ('projects\'+$n); & '.\.venv-run312\Scripts\python.exe' main.py --data-dir $d --port $p; exit $LASTEXITCODE"
if errorlevel 1 goto error
exit /b 0
:error
echo Project startup failed.
pause
exit /b 1
