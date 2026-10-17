# Pull latest + apply (git pull + pip install if needed + restart server on backend change)
# Usage (in backend folder):  .\update.ps1
# Frontend (js/css/html): applied by pull only (refresh browser).
# Backend (.py) / requirements: server is auto-restarted.

$ErrorActionPreference = "Stop"
$repo = Split-Path $PSScriptRoot -Parent          # parent of backend = repo root
$py   = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { $py = "python" }

Push-Location $repo
try {
    $before = (git rev-parse HEAD).Trim()
    git pull --ff-only
    if (-not $?) { Write-Host "git pull failed - check conflict/network" -ForegroundColor Red; return }
    $after = (git rev-parse HEAD).Trim()

    if ($before -eq $after) {
        Write-Host "Already up to date ($($after.Substring(0,7)))." -ForegroundColor Gray
        return
    }

    $changed = git diff --name-only $before $after
    $reqChanged     = $changed -match "requirements\.txt"
    $backendChanged = ($changed -match "\.py$") -or $reqChanged

    if ($reqChanged) {
        Write-Host "requirements changed -> installing deps" -ForegroundColor Yellow
        & $py -m pip install -r (Join-Path $PSScriptRoot "requirements.txt")
    }

    if ($backendChanged) {
        Write-Host "Backend (.py) changed -> restarting server" -ForegroundColor Yellow
        # Stop the task (best effort). The task can stay 'Running' because the uvicorn
        # child keeps it alive, which makes Start-ScheduledTask a no-op
        # (MultipleInstances=IgnoreNew) and leaves OLD code serving :8000. So we also
        # FORCE-kill whatever holds port 8000 (+ stray uvicorn/multiprocessing pythons).
        Stop-ScheduledTask -TaskName "AssetPro-Server" -ErrorAction SilentlyContinue
        Start-Sleep -Milliseconds 500
        Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue |
            Select-Object -ExpandProperty OwningProcess -Unique |
            ForEach-Object { Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue }
        Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
            Where-Object { $_.CommandLine -like '*uvicorn*' -or $_.CommandLine -like '*multiprocessing*' } |
            ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
        Start-Sleep -Milliseconds 800
        Start-ScheduledTask -TaskName "AssetPro-Server"
        # Verify it actually bound :8000; fall back to launching run-server.ps1 directly.
        $up = $false
        for ($i = 0; $i -lt 10; $i++) {
            Start-Sleep -Milliseconds 800
            if (Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue) { $up = $true; break }
        }
        if (-not $up) {
            Write-Host "  task start did not bind :8000 -> launching run-server.ps1 directly" -ForegroundColor Yellow
            $rs = Join-Path $PSScriptRoot "run-server.ps1"
            Start-Process powershell -ArgumentList "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$rs`"" -WorkingDirectory $PSScriptRoot
        }
        Write-Host "  server restarted (force-killed old process on :8000)" -ForegroundColor Green
    } else {
        Write-Host "Frontend only -> refresh browser (no restart needed)" -ForegroundColor Gray
    }

    Write-Host "Updated: $($before.Substring(0,7)) -> $($after.Substring(0,7))" -ForegroundColor Green
}
finally {
    Pop-Location
}
