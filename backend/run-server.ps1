# AssetPro 서버 실행 (LAN 접속 + 자동 리로드) — Windows 서버용
# 사용: backend 폴더에서  ->  .\run-server.ps1
# git pull 로 코드가 바뀌면 uvicorn 이 자동 재시작(--reload)한다.

$ErrorActionPreference = "SilentlyContinue"
$port = 8000

# 1) 8000 포트를 점유한 기존 프로세스 종료
$conns = Get-NetTCPConnection -LocalPort $port -State Listen
if ($conns) {
    $conns | Select-Object -ExpandProperty OwningProcess -Unique | ForEach-Object {
        Write-Host "기존 프로세스 종료: PID $_" -ForegroundColor Yellow
        Stop-Process -Id $_ -Force
    }
    Start-Sleep -Milliseconds 700
}

# 2) venv 파이썬 직접 호출 (ExecutionPolicy 무관)
$py = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { $py = "python" }

# 3) 이 PC의 LAN IP 안내
$ip = (Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
    Where-Object { $_.IPAddress -notlike "169.*" -and $_.IPAddress -ne "127.0.0.1" } |
    Select-Object -First 1 -ExpandProperty IPAddress)
Write-Host "AssetPro 서버 시작 → http://localhost:$port  /  http://$ip`:$port (LAN)" -ForegroundColor Green
Write-Host "코드 변경(git pull) 시 자동 반영됩니다." -ForegroundColor Gray

& $py -m uvicorn main:app --host 0.0.0.0 --port $port --reload
