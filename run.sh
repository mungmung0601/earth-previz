#!/usr/bin/env bash
set -e

cd "$(dirname "$0")"

if ! command -v python3 &>/dev/null; then
  echo "ERROR: python3 not found. Install Python 3.9+ first."
  echo "  macOS:  brew install python3"
  echo "  Linux:  sudo apt install python3 python3-venv"
  exit 1
fi

PY_VER=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
PY_MIN=$(python3 -c "import sys; print(1 if sys.version_info >= (3,9) else 0)")
if [ "$PY_MIN" = "0" ]; then
  echo "ERROR: Python 3.9+ required (found $PY_VER)"
  exit 1
fi

if [ ! -d ".venv" ]; then
  echo "Creating virtual environment (Python $PY_VER)..."
  python3 -m venv .venv
fi

source .venv/bin/activate

echo "Installing dependencies (Flask, Playwright, FFmpeg, etc.)..."
pip install -q -r requirements.txt
python -m playwright install chromium 2>/dev/null || true

echo ""
echo "Starting Earth Previz..."
echo "Open http://127.0.0.1:5100 in your browser"
echo ""
python app.py
