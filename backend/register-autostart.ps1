# 부팅 시 서버 자동 시작 (작업 스케줄러 등록) — Windows 서버용
# 서버에서 "관리자 PowerShell" 로 1회 실행:
#   backend 폴더에서  ->  .\register-autostart.ps1
#   (SYSTEM 계정으로 돌리려면)  ->  .\register-autostart.ps1 -System
#
# 기본: 현재 사용자 + S4U(로그인 없이도 부팅 시 실행, 암호 저장 불필요, 로컬 파일 접근 가능).
param([switch]$System)

$ErrorActionPreference = "Stop"
$taskName = "AssetPro-Server"
$script   = Join-Path $PSScriptRoot "run-server.ps1"
if (-not (Test-Path $script)) { throw "run-server.ps1 을 찾을 수 없습니다: $script" }

$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$script`"" `
    -WorkingDirectory $PSScriptRoot

$trigger = New-ScheduledTaskTrigger -AtStartup

# 서버는 계속 떠 있어야 하므로 실행시간 제한 해제 + 꺼지면 재시작
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
    -Description "AssetPro 서버 부팅 시 자동 시작" -Force | Out-Null

Write-Host "등록 완료: '$taskName' (부팅 시 자동 시작, 계정: $who)" -ForegroundColor Green
Write-Host "지금 바로 시작:  Start-ScheduledTask -TaskName '$taskName'" -ForegroundColor Gray
Write-Host "상태 확인:      Get-ScheduledTask -TaskName '$taskName' | Get-ScheduledTaskInfo" -ForegroundColor Gray
Write-Host "해제:          Unregister-ScheduledTask -TaskName '$taskName' -Confirm:`$false" -ForegroundColor Gray
