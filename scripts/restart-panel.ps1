# Restart only this repository's panel process. ASCII comments keep PS 5.1 safe.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"
$bind = if ($env:ASTOCK_HUB_HOST) { $env:ASTOCK_HUB_HOST } else { "127.0.0.1" }

Get-CimInstance Win32_Process |
  Where-Object {
    $_.Name -like "python*" -and
    $_.CommandLine -like "*uvicorn*" -and
    $_.CommandLine -like "*$root*"
  } |
  ForEach-Object { taskkill /PID $_.ProcessId /T /F 2>&1 | Out-Null }

New-Item -ItemType Directory -Force (Join-Path $root "data") | Out-Null
Start-Process -FilePath $python `
  -ArgumentList "-m", "uvicorn", "astock_hub.webapi:app", "--host", $bind, "--port", "8756" `
  -WorkingDirectory $root -WindowStyle Hidden `
  -RedirectStandardOutput (Join-Path $root "data\web-panel.out.log") `
  -RedirectStandardError (Join-Path $root "data\web-panel.err.log")

Start-Sleep -Seconds 4
try {
  $r = Invoke-WebRequest -Uri "http://127.0.0.1:8756/api/health" -UseBasicParsing -TimeoutSec 15
  Write-Output "panel restarted: http://127.0.0.1:8756 (health $($r.StatusCode))"
} catch {
  Write-Output "health check failed; see data\web-panel.err.log"
  exit 1
}
