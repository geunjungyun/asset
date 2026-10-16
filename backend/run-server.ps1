# AssetPro server runner (LAN access) - Windows server
# Usage (in backend folder):  .\run-server.ps1
# Stable mode (no --reload). Code changes are applied by update.ps1 (auto-restart on change).

$ErrorActionPreference = "SilentlyContinue"
$port = 8000

# 1) Kill any process holding port 8000
$conns = Get-NetTCPConnection -LocalPort $port -State Listen
if ($conns) {
    $conns | Select-Object -ExpandProperty OwningProcess -Unique | ForEach-Object {
        Write-Host "Killing existing PID $_" -ForegroundColor Yellow
        Stop-Process -Id $_ -Force
    }
    Start-Sleep -Milliseconds 700
}

# 2) Call venv python directly (independent of ExecutionPolicy)
$py = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { $py = "python" }

# 3) Show this PC's LAN IP
$ip = (Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
    Where-Object { $_.IPAddress -notlike "169.*" -and $_.IPAddress -ne "127.0.0.1" } |
    Select-Object -First 1 -ExpandProperty IPAddress)
Write-Host "AssetPro server starting -> http://localhost:$port  /  http://$ip`:$port (LAN)" -ForegroundColor Green
Write-Host "Stable mode (no --reload). Code is applied by update.ps1 auto-restart." -ForegroundColor Gray

& $py -m uvicorn main:app --host 0.0.0.0 --port $port
