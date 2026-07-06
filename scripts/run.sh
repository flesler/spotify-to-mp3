#!/bin/bash
# Wrapper script to ensure the app always runs with the project's virtual environment

set -e

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_ACTIVATE="$ROOT_DIR/.venv/bin/activate"

if [ ! -f "$VENV_ACTIVATE" ]; then
    echo "❌ Virtual environment not found!"
    echo "Run: ./scripts/setup-venv.sh"
    exit 1
fi

source "$VENV_ACTIVATE"
export PYTHONUNBUFFERED=1
exec python "$ROOT_DIR/main.py" "$@"
