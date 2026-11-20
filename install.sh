#!/usr/bin/env bash
# astock-hub one-click installer for Linux and macOS.
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"
START=0
if [[ "${1:-}" == "--start" || "${1:-}" == "-s" ]]; then
  START=1
elif [[ $# -gt 0 ]]; then
  echo "Usage: bash install.sh [--start]" >&2
  exit 2
fi

step() { printf '[astock-hub] %s\n' "$1"; }
need_command() {
  if ! command -v "$1" >/dev/null 2>&1; then
    printf '[astock-hub] Missing command: %s. %s\n' "$1" "$2" >&2
    exit 1
  fi
}

step "Checking Python 3.11+ and Node.js 18+..."
need_command python3 "Install Python 3.11 or newer."
need_command node "Install Node.js LTS from https://nodejs.org/."
need_command npm "Reinstall Node.js LTS to include npm."
python3 -c 'import sys; assert sys.version_info >= (3, 11), "Python 3.11+ is required"'
NODE_MAJOR="$(node -e 'process.stdout.write(process.versions.node.split(".")[0])')"
if (( NODE_MAJOR < 18 )); then
  echo "[astock-hub] Node.js 18+ is required; found major version $NODE_MAJOR." >&2
  exit 1
fi

if [[ ! -x .venv/bin/python ]]; then
  step "Creating virtual environment..."
  python3 -m venv .venv
else
  step "Using existing virtual environment."
fi

step "Installing backend dependencies..."
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e ".[web]"
step "Installing frontend dependencies..."
npm --prefix frontend ci
step "Building frontend..."
npm --prefix frontend run build
step "Installation completed."

if (( START == 0 )); then
  step "Start later with: .venv/bin/python -m uvicorn astock_hub.webapi:app --host 127.0.0.1 --port 8756"
  exit 0
fi

BIND="${ASTOCK_HUB_HOST:-127.0.0.1}"
mkdir -p data
if ! .venv/bin/python -c 'import urllib.request; urllib.request.urlopen("http://127.0.0.1:8756/api/health", timeout=2)' >/dev/null 2>&1; then
  step "Starting panel on $BIND:8756..."
  nohup .venv/bin/python -m uvicorn astock_hub.webapi:app \
    --host "$BIND" --port 8756 \
    >data/web-panel.out.log 2>data/web-panel.err.log &
  echo "$!" > data/web-panel.pid
  sleep 4
fi

if .venv/bin/python -c 'import urllib.request; urllib.request.urlopen("http://127.0.0.1:8756/api/health", timeout=15)' >/dev/null 2>&1; then
  step "Panel is ready: http://127.0.0.1:8756"
else
  echo "[astock-hub] Panel did not become healthy. See data/web-panel.err.log." >&2
  exit 1
fi
