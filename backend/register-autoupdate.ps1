# Register a scheduled task that runs update.ps1 periodically (auto-deploy).
# After you push, the server auto-pulls and (on backend change) auto-restarts.
# Run in an ADMIN PowerShell:  .\register-autoupdate.ps1  [-Minutes 2]
param([int]$Minutes = 2)

$script = Join-Path $PSScriptRoot "update.ps1"
$taskName = "AssetPro-AutoUpdate"

$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$script`"" `
    -WorkingDirectory $PSScriptRoot

$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date)
$trigger.Repetition = (New-ScheduledTaskTrigger -Once -At (Get-Date) `
    -RepetitionInterval (New-TimeSpan -Minutes $Minutes) `
    -RepetitionDuration ([TimeSpan]::FromDays(3650))).Repetition

$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -DontStopOnIdleEnd `
    -MultipleInstances IgnoreNew

# Run with highest privilege so it can restart the AssetPro-Server task
$me = "$env:USERDOMAIN\$env:USERNAME"
$principal = New-ScheduledTaskPrincipal -UserId $me -LogonType S4U -RunLevel Highest

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger `
    -Settings $settings -Principal $principal `
    -Description "AssetPro: git pull auto-deploy + restart server on backend change ($Minutes min)" -Force | Out-Null

Write-Host "Registered: '$taskName' (runs update.ps1 every $Minutes min, auto-restarts server on backend change)" -ForegroundColor Green
Write-Host "Remove:  Unregister-ScheduledTask -TaskName '$taskName' -Confirm:`$false" -ForegroundColor Gray
