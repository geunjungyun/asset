# Auto-start the server at boot (register a scheduled task) - Windows server
# Run once in an ADMIN PowerShell (in backend folder):
#   .\register-autostart.ps1
#   (to run as SYSTEM)  ->  .\register-autostart.ps1 -System
#
# Default: current user + S4U (runs at boot without login, no stored password, local file access).
param([switch]$System)

$ErrorActionPreference = "Stop"
$taskName = "AssetPro-Server"
$script   = Join-Path $PSScriptRoot "run-server.ps1"
if (-not (Test-Path $script)) { throw "run-server.ps1 not found: $script" }

$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$script`"" `
    -WorkingDirectory $PSScriptRoot

$trigger = New-ScheduledTaskTrigger -AtStartup

# Server must stay up: no execution time limit + restart on failure
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -RestartInterval (New-TimeSpan -Minutes 1) -RestartCount 3 `
    -MultipleInstances IgnoreNew

if ($System) {
    $principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
    $who = "SYSTEM"
} else {
    $me = "$env:USERDOMAIN\$env:USERNAME"
    $principal = New-ScheduledTaskPrincipal -UserId $me -LogonType S4U -RunLevel Highest
    $who = $me
}

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger `
    -Principal $principal -Settings $settings `
    -Description "AssetPro server auto-start at boot" -Force | Out-Null

Write-Host "Registered: '$taskName' (auto-start at boot, account: $who)" -ForegroundColor Green
Write-Host "Start now:   Start-ScheduledTask -TaskName '$taskName'" -ForegroundColor Gray
Write-Host "Status:      Get-ScheduledTask -TaskName '$taskName' | Get-ScheduledTaskInfo" -ForegroundColor Gray
Write-Host "Remove:      Unregister-ScheduledTask -TaskName '$taskName' -Confirm:`$false" -ForegroundColor Gray
