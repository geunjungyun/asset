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
        try {
            Stop-ScheduledTask  -TaskName "AssetPro-Server" -ErrorAction SilentlyContinue
            Start-Sleep -Milliseconds 1000
            Start-ScheduledTask -TaskName "AssetPro-Server"
            Write-Host "  server restarted" -ForegroundColor Green
        } catch {
            Write-Host "  server restart failed (privilege?): $_" -ForegroundColor Red
            Write-Host "  -> re-register AssetPro-AutoUpdate as admin (RunLevel Highest), or restart manually" -ForegroundColor Gray
        }
    } else {
        Write-Host "Frontend only -> refresh browser (no restart needed)" -ForegroundColor Gray
    }

    Write-Host "Updated: $($before.Substring(0,7)) -> $($after.Substring(0,7))" -ForegroundColor Green
}
finally {
    Pop-Location
}
