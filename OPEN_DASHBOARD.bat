@echo off
cd /d "%~dp0"
if not exist "data\dashboard-key.txt" (echo Start RelayForge first. & pause & exit /b 1)
powershell -NoProfile -Command "$k=(Get-Content -LiteralPath 'data\dashboard-key.txt' -Raw).Trim(); if(-not $k){throw 'Missing dashboard key'}; Start-Process ('http://127.0.0.1:8765/#rf-key='+[uri]::EscapeDataString($k))"
if errorlevel 1 pause
