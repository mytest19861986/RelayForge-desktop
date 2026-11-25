# Optional, unverified on Windows in this release. Run elevated in a protected installation.
$ErrorActionPreference='Stop'
$me=New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (!$me.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'Run PowerShell as administrator.' }
$root=(Resolve-Path -LiteralPath $PSScriptRoot).Path
$exe=Join-Path $root 'RelayForge.exe'
if (!(Test-Path -LiteralPath $exe)) { throw 'Build EXE first.' }
if (Get-Service -Name RelayForgeStudio -ErrorAction SilentlyContinue) { throw 'Service already exists. Do not overwrite.' }
if (Get-ScheduledTask -TaskName RelayForge -ErrorAction SilentlyContinue) { throw 'Remove or disable the RelayForge scheduled task before installing service.' }
# Prevent a privileged service running from a user-writable Downloads/Desktop directory.
if (!$root.StartsWith(($env:ProgramFiles+'\'),[StringComparison]::OrdinalIgnoreCase)) { throw 'Install in a protected Program Files directory first; give the dedicated service account access only to its data directory.' }
$data=Join-Path $env:ProgramData 'RelayForgeStudio'
if (!(Test-Path -LiteralPath $data)) { throw 'Create and secure ProgramData\RelayForgeStudio first; migrate stopped data and grant only the dedicated account access.' }
$credential=Get-Credential -Message 'Dedicated NON-administrator Windows account with Log on as a service rights and access to the data directory'
if (!$credential) { throw 'Cancelled' }
if ((Read-Host 'Install automatic service with this account? Type YES') -cne 'YES') { exit 0 }
$command='"'+$exe+'" --windows-service --no-browser --data-dir "'+$data+'"'
New-Service -Name RelayForgeStudio -BinaryPathName $command -DisplayName 'RelayForge Studio' -StartupType Automatic -Credential $credential | Out-Null
Write-Host 'Service registered. Verify permissions, stop other instances, then Start-Service RelayForgeStudio and check local health.'
