# Start astock-hub web panel (production, single port 8756, hidden background process)
# Usage: powershell -File scripts\start-web.ps1
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$listening = Get-NetTCPConnection -LocalPort 8756 -State Listen -ErrorAction SilentlyContinue
if ($listening) {
    Write-Host "panel already running on port 8756"
    exit 0
}

$python = Join-Path $root ".venv\Scripts\python.exe"
# Mobile step 0 (2026-11-01): default bind LAN so phones can reach the panel;
# set ASTOCK_HUB_HOST=127.0.0.1 to keep it local-only.
$bind = if ($env:ASTOCK_HUB_HOST) { $env:ASTOCK_HUB_HOST } else { '0.0.0.0' }
Start-Process -FilePath $python `
    -ArgumentList "-m", "uvicorn", "astock_hub.webapi:app", "--host", $bind, "--port", "8756" `
    -WorkingDirectory $root `
    -WindowStyle Hidden `
    -RedirectStandardOutput (Join-Path $root "data\web-panel.out.log") `
    -RedirectStandardError (Join-Path $root "data\web-panel.err.log")

Start-Sleep -Seconds 4
try {
    $r = Invoke-WebRequest -Uri "http://127.0.0.1:8756/api/health" -TimeoutSec 10 -UseBasicParsing
    Write-Host "panel started: http://localhost:8756 (health $($r.StatusCode))"
} catch {
    Write-Host "health check failed after start; see data\web-panel.err.log"
    exit 1
}
