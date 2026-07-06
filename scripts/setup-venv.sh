#!/bin/bash
set -e

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

echo "🔧 Setting up Python virtual environment..."

PYTHON=""
for candidate in python3.13 python3.12 python3.11 python3; do
    if command -v "$candidate" &> /dev/null; then
        PYTHON="$candidate"
        break
    fi
done

if [ -z "$PYTHON" ]; then
    echo "❌ python3 not found"
    exit 1
fi

echo "Using $PYTHON ($($PYTHON --version))"

if [ ! -d ".venv" ]; then
    echo "Creating virtual environment in .venv/"
    "$PYTHON" -m venv .venv
else
    echo "Virtual environment already exists"
fi

source .venv/bin/activate

echo "Upgrading pip..."
python -m pip install --upgrade pip

echo "Installing dependencies from requirements.txt..."
pip install -r requirements.txt

if [ "${INSTALL_AUDIO_ANALYSIS:-}" = "1" ]; then
    echo "Installing optional audio analysis deps (host only)..."
    pip install -r requirements-analysis.txt
fi

if command -v yt-dlp &> /dev/null || python -c "import yt_dlp" &> /dev/null; then
    echo "✅ yt-dlp is available"
else
    echo "⚠️  Warning: yt-dlp not found. Install it with:"
    echo "   pip install yt-dlp"
    echo "   or: sudo apt-get install yt-dlp"
fi

if command -v node &> /dev/null; then
    echo "✅ Node.js found ($(node --version)) — used by yt-dlp for YouTube"
elif command -v deno &> /dev/null; then
    echo "✅ Deno found — used by yt-dlp for YouTube"
else
    echo "⚠️  No JS runtime for yt-dlp (node or deno). YouTube downloads may fail with 403."
    echo "   Install Node: https://nodejs.org/ or: nvm install --lts"
fi

echo ""
echo "✅ Virtual environment setup complete!"
echo ""
echo "To activate:"
echo "  source .venv/bin/activate"
echo ""
echo "To deactivate:"
echo "  deactivate"
