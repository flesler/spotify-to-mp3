#!/usr/bin/env python3
"""Download one Spotify track by ID (see try-track.py for full pipeline)."""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} <spotify-track-id-or-url>", file=sys.stderr)
        print("Tip: use ./scripts/try-track.py for download + analyze + match", file=sys.stderr)
        return 1
    return subprocess.call(
        [sys.executable, str(ROOT / "scripts" / "try-track.py"), sys.argv[1], "--no-match"],
    )


if __name__ == "__main__":
    raise SystemExit(main())
