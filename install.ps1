# astock-hub one-click installer for Windows PowerShell 5.1+
# Keep this file ASCII-only so Windows PowerShell reads it correctly.
param(
    [switch]$Start
)

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
Set-Location $root

function Step([string]$message) {
    Write-Host "[astock-hub] $message"
}

function Require-Command([string]$name, [string]$hint) {
    if (-not (Get-Command $name -ErrorAction SilentlyContinue)) {
        Write-Error "$name was not found. $hint"
        exit 1
    }
}

function Invoke-Checked([string]$file, [string[]]$commandArgs) {
    & $file @commandArgs
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed with exit code $LASTEXITCODE`: $file $($commandArgs -join ' ')"
    }
}

Step "Checking Python 3.11+ and Node.js 18+..."
Require-Command "python" "Install Python from https://www.python.org/downloads/ and enable Add python.exe to PATH."
Require-Command "node" "Install Node.js LTS from https://nodejs.org/."
Require-Command "npm" "Reinstall Node.js LTS to include npm."

$pythonVersion = & python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
if ([version]$pythonVersion -lt [version]"3.11") {
    Write-Error "Python 3.11+ is required; found $pythonVersion."
    exit 1
}
$nodeMajor = & node -e "process.stdout.write(String(process.versions.node.split('.')[0]))"
if ([int]$nodeMajor -lt 18) {
    Write-Error "Node.js 18+ is required; found major version $nodeMajor."
    exit 1
}

$venvPython = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    Step "Creating virtual environment..."
    Invoke-Checked "python" @("-m", "venv", ".venv")
} else {
    Step "Using existing virtual environment."
}

Step "Installing backend dependencies..."
Invoke-Checked $venvPython @("-m", "pip", "install", "--upgrade", "pip")
Invoke-Checked $venvPython @("-m", "pip", "install", "-e", ".[web]")

Step "Installing frontend dependencies..."
Invoke-Checked "npm" @("--prefix", "frontend", "ci")

Step "Building frontend..."
Invoke-Checked "npm" @("--prefix", "frontend", "run", "build")

Step "Installation completed."

if ($Start) {
    $bind = if ($env:ASTOCK_HUB_HOST) { $env:ASTOCK_HUB_HOST } else { "127.0.0.1" }
    $listening = Get-NetTCPConnection -LocalPort 8756 -State Listen -ErrorAction SilentlyContinue
    if (-not $listening) {
        Step "Starting panel on $bind`:8756..."
        New-Item -ItemType Directory -Force (Join-Path $root "data") | Out-Null
        Start-Process -FilePath $venvPython `
            -ArgumentList "-m", "uvicorn", "astock_hub.webapi:app", "--host", $bind, "--port", "8756" `
            -WorkingDirectory $root -WindowStyle Hidden `
            -RedirectStandardOutput (Join-Path $root "data\web-panel.out.log") `
            -RedirectStandardError (Join-Path $root "data\web-panel.err.log")
        Start-Sleep -Seconds 4
    }
    try {
        $response = Invoke-WebRequest -Uri "http://127.0.0.1:8756/api/health" -UseBasicParsing -TimeoutSec 15
        Step "Panel is ready (health $($response.StatusCode))."
        Start-Process "http://127.0.0.1:8756"
    } catch {
        Write-Error "Panel did not become healthy. See data\web-panel.err.log."
        exit 1
    }
} else {
    Step "Start later with: .venv\Scripts\python.exe -m uvicorn astock_hub.webapi:app --host 127.0.0.1 --port 8756"
}
