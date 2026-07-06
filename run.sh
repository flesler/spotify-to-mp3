#!/bin/bash
# Wrapper script to ensure the app always runs with the project's virtual environment

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_ACTIVATE="$SCRIPT_DIR/.venv/bin/activate"

# Check if venv exists
if [ ! -f "$VENV_ACTIVATE" ]; then
    echo "❌ Virtual environment not found!"
    echo "Run: ./setup-venv.sh"
    exit 1
fi

# Activate venv and run the script
source "$VENV_ACTIVATE"
exec python "$SCRIPT_DIR/main.py" "$@"
