# update.ps1 을 주기적으로 실행하는 작업 스케줄러 등록 (자동 배포)
# push 하면 서버가 알아서 git pull → 자동 반영.
# 실행: 관리자 PowerShell 에서  ->  .\register-autoupdate.ps1  [-Minutes 2]
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

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger `
    -Settings $settings -Description "AssetPro: git pull 자동 반영 ($Minutes 분마다)" -Force | Out-Null

Write-Host "등록 완료: '$taskName' ($Minutes 분마다 update.ps1 실행)" -ForegroundColor Green
Write-Host "해제:  Unregister-ScheduledTask -TaskName '$taskName' -Confirm:`$false" -ForegroundColor Gray
