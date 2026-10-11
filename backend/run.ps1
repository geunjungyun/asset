# AssetPro 백엔드 실행 스크립트 (Windows PowerShell)
# 포트 8000을 쓰는 기존 프로세스를 정리한 뒤 서버를 실행한다.
# 사용: 이 폴더(backend)에서  ->  .\run.ps1

$ErrorActionPreference = "SilentlyContinue"
$port = 8000

# 1) 8000 포트를 점유한 프로세스 종료
$conns = Get-NetTCPConnection -LocalPort $port -State Listen
if ($conns) {
    $pids = $conns | Select-Object -ExpandProperty OwningProcess -Unique
    foreach ($procId in $pids) {
        Write-Host "기존 프로세스 종료: PID $procId" -ForegroundColor Yellow
        Stop-Process -Id $procId -Force
    }
    Start-Sleep -Milliseconds 700
}

# 2) 서버 실행 (venv 파이썬 직접 호출 → ExecutionPolicy 무관)
$py = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { $py = "python" }

Write-Host "AssetPro 서버 시작 → http://localhost:$port" -ForegroundColor Green
& $py -m uvicorn main:app --reload --port $port
