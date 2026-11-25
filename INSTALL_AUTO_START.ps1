# Run this script in PowerShell as Administrator, on the VPS after EXE build.
# A dedicated low-privilege service account is strongly recommended.
$ErrorActionPreference = 'Stop'
$exe = Join-Path $PSScriptRoot 'RelayForge.exe'
if (!(Test-Path -LiteralPath $exe)) { throw 'RelayForge.exe is missing; compile on Windows first.' }
$account = Read-Host 'Windows account to run task as (DOMAIN\Username or .\serviceuser)'
if (!$account) { throw 'Account was not supplied' }
$secure = Read-Host 'Password for that Windows account (not stored by this script)' -AsSecureString
$handle = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
try {
  $password = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($handle)
  $action = New-ScheduledTaskAction -Execute $exe -Argument '--no-browser' -WorkingDirectory $PSScriptRoot
  $trigger = New-ScheduledTaskTrigger -AtStartup
  $settings = New-ScheduledTaskSettingsSet -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 2) -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew
  Register-ScheduledTask -TaskName 'RelayForge' -Action $action -Trigger $trigger -Settings $settings -User $account -Password $password -RunLevel Limited -Force | Out-Null
  Write-Host 'Scheduled Task RelayForge was created. Run it in Task Scheduler to test.'
} finally {
  [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($handle)
  Remove-Variable password -ErrorAction SilentlyContinue
}
