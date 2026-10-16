# 최신 코드 받아 반영 (git pull + 필요 시 의존성 설치 + 백엔드 변경 시 서버 자동 재시작)
# 사용: backend 폴더에서  ->  .\update.ps1
# 프론트(js/css/html)는 pull만으로 반영(브라우저 새로고침). 백엔드(.py)/의존성 변경 시 서버 재시작.

$ErrorActionPreference = "Stop"
$repo = Split-Path $PSScriptRoot -Parent          # backend 의 상위 = 저장소 루트
$py   = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { $py = "python" }

Push-Location $repo
try {
    $before = (git rev-parse HEAD).Trim()
    git pull --ff-only
    if (-not $?) { Write-Host "git pull 실패 — 충돌/네트워크 확인" -ForegroundColor Red; return }
    $after = (git rev-parse HEAD).Trim()

    if ($before -eq $after) {
        Write-Host "이미 최신입니다 ($($after.Substring(0,7)))." -ForegroundColor Gray
        return
    }

    $changed = git diff --name-only $before $after
    $reqChanged     = $changed -match "requirements\.txt"
    $backendChanged = ($changed -match "\.py$") -or $reqChanged   # .py 또는 의존성 변경

    if ($reqChanged) {
        Write-Host "requirements 변경 감지 → 의존성 설치" -ForegroundColor Yellow
        & $py -m pip install -r (Join-Path $PSScriptRoot "requirements.txt")
    }

    if ($backendChanged) {
        Write-Host "백엔드(.py) 변경 감지 → 서버 재시작" -ForegroundColor Yellow
        try {
            Stop-ScheduledTask  -TaskName "AssetPro-Server" -ErrorAction SilentlyContinue
            Start-Sleep -Milliseconds 1000
            Start-ScheduledTask -TaskName "AssetPro-Server"
            Write-Host "  서버 재시작 완료" -ForegroundColor Green
        } catch {
            Write-Host "  서버 재시작 실패(권한?): $_" -ForegroundColor Red
            Write-Host "  → AssetPro-AutoUpdate 작업을 관리자 권한(-RunLevel Highest)으로 재등록하거나, 수동 재시작 필요" -ForegroundColor Gray
        }
    } else {
        Write-Host "프론트만 변경 → 브라우저 새로고침으로 반영(서버 재시작 불필요)" -ForegroundColor Gray
    }

    Write-Host "업데이트 완료: $($before.Substring(0,7)) -> $($after.Substring(0,7))" -ForegroundColor Green
}
finally {
    Pop-Location
}
