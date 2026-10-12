# 최신 코드 받아 반영 (git pull + 필요 시 의존성 설치) — Windows 서버용
# 사용: backend 폴더에서  ->  .\update.ps1
# 서버를 --reload 로 띄워 두었다면 백엔드는 자동 재시작, 프론트는 브라우저 새로고침만.

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
    if ($changed -match "requirements\.txt") {
        Write-Host "requirements 변경 감지 → 의존성 설치" -ForegroundColor Yellow
        & $py -m pip install -r (Join-Path $PSScriptRoot "requirements.txt")
    }

    Write-Host "업데이트 완료: $($before.Substring(0,7)) -> $($after.Substring(0,7))" -ForegroundColor Green
    Write-Host "  (--reload 실행 중이면 백엔드 자동 반영 / 프론트는 브라우저 새로고침)" -ForegroundColor Gray
}
finally {
    Pop-Location
}
