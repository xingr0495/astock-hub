# Restart the free-edition panel and verify the public health endpoint.
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

Get-CimInstance Win32_Process |
  Where-Object { $_.Name -like 'python*' -and $_.CommandLine -like '*uvicorn*' -and $_.CommandLine -like '*8756*' } |
  ForEach-Object { taskkill /PID $_.ProcessId /T /F 2>&1 | Out-Null }
Start-Sleep 1

$bind = if ($env:ASTOCK_HUB_HOST) { $env:ASTOCK_HUB_HOST } else { '0.0.0.0' }
$python = Join-Path $root '.venv\Scripts\python.exe'
New-Item -ItemType Directory -Path (Join-Path $root 'data') -Force | Out-Null
Start-Process -FilePath $python `
  -ArgumentList '-m','uvicorn','astock_hub.webapi:app','--host',$bind,'--port','8756' `
  -WorkingDirectory $root -WindowStyle Hidden `
  -RedirectStandardOutput (Join-Path $root 'data\web-panel.out.log') `
  -RedirectStandardError (Join-Path $root 'data\web-panel.err.log')
Start-Sleep 4

$r = Invoke-WebRequest -Uri 'http://127.0.0.1:8756/api/health' -UseBasicParsing -TimeoutSec 15
Write-Output "STATUS=$($r.StatusCode)"
