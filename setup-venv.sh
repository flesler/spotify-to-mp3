#!/bin/bash
set -e

echo "🔧 Setting up Python virtual environment..."

# Create virtual environment if it doesn't exist
if [ ! -d ".venv" ]; then
    echo "Creating virtual environment in .venv/"
    python3 -m venv .venv
else
    echo "Virtual environment already exists"
fi

# Activate virtual environment
source .venv/bin/activate

# Upgrade pip
echo "Upgrading pip..."
python -m pip install --upgrade pip

# Install dependencies
echo "Installing dependencies from requirements.txt..."
pip install -r requirements.txt

# Verify yt-dlp is available
if command -v yt-dlp &> /dev/null || python -c "import yt_dlp" &> /dev/null; then
    echo "✅ yt-dlp is available"
else
    echo "⚠️  Warning: yt-dlp not found. Install it with:"
    echo "   pip install yt-dlp"
    echo "   or: sudo apt-get install yt-dlp"
fi

echo ""
echo "✅ Virtual environment setup complete!"
echo ""
echo "To activate:"
echo "  source .venv/bin/activate"
echo ""
echo "To deactivate:"
echo "  deactivate"
