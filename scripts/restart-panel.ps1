Get-CimInstance Win32_Process |
  Where-Object { $_.Name -like 'python*' -and $_.CommandLine -like '*uvicorn*' } |
  ForEach-Object { taskkill /PID $_.ProcessId /T /F 2>&1 | Out-Null }
Start-Sleep 1
# Mobile step 0 (2026-11-01): default bind LAN so phones can reach the panel;
# set ASTOCK_HUB_HOST=127.0.0.1 to keep it local-only.
$bind = if ($env:ASTOCK_HUB_HOST) { $env:ASTOCK_HUB_HOST } else { '0.0.0.0' }
Start-Process -FilePath 'F:\astock-hub\.venv\Scripts\python.exe' `
  -ArgumentList '-m','uvicorn','astock_hub.webapi:app','--host',$bind,'--port','8756' `
  -WorkingDirectory 'F:\astock-hub' -WindowStyle Hidden `
  -RedirectStandardOutput 'F:\astock-hub\data\web-panel.out.log' `
  -RedirectStandardError 'F:\astock-hub\data\web-panel.err.log'
Start-Sleep 4
# Task 13: service token for the zsxq-tier probe below (read AFTER panel start so
# the startup materialization has run; missing file -> null header -> 401 fail-closed)
$svcToken = (Get-Content (Join-Path $PSScriptRoot "..\data\auth_service_token") -Raw -ErrorAction SilentlyContinue)
if ($svcToken) { $svcToken = $svcToken.Trim() }
$headers = @{ "X-Service-Token" = $svcToken }
try {
  $r = Invoke-WebRequest -Uri 'http://127.0.0.1:8756/api/digest-pool' -UseBasicParsing -Headers $headers -TimeoutSec 15
  Write-Output "STATUS=$($r.StatusCode)"
} catch {
  Write-Output "ERR=$($_.Exception.Message)"
}
