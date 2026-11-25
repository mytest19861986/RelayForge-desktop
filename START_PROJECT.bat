@echo off
cd /d "%~dp0"
if not exist "RelayForge.exe" (echo Build RelayForge.exe first & pause & exit /b 1)
powershell -NoProfile -Command "$n=Read-Host 'Project name (letters/numbers/dash)'; if($n -notmatch '^[a-zA-Z0-9_-]{1,40}$'){throw 'Invalid project name'}; $p=Read-Host 'Unique local port (8766-65535)'; if($p -notmatch '^\d{4,5}$' -or [int]$p -lt 8766 -or [int]$p -gt 65535){throw 'Invalid port'}; $d=Join-Path (Get-Location) ('projects\'+$n); & '.\RelayForge.exe' --data-dir $d --port $p"
pause
