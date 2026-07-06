#!/usr/bin/env python3
"""Analyze library MP3s once per unique track; hard-link .audio.yaml to duplicate paths."""

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from audio_analysis import analysis_enabled, analyze_library  # noqa: E402


def run_one(music_dir: Path, playlist: str | None, *, limit: int | None, force: bool) -> dict:
    label = playlist or "library"
    print(f"🎛️  Analyzing {label}…")
    stats = analyze_library(music_dir, playlist=playlist, limit=limit, force=force)
    print(
        f"✅ Done: {stats['analyzed']} analyzed, {stats['skipped']} cached, "
        f"{stats['linked']} sidecar links, {stats['failed']} failed"
    )
    return stats


def main() -> int:
    parser = argparse.ArgumentParser(description="Essentia analysis for the music library (host only)")
    parser.add_argument(
        "playlists",
        nargs="*",
        help="Optional playlist folder names (e.g. Epic Rivotril); omit for full library",
    )
    parser.add_argument("--limit", type=int, help="Max tracks to analyze per run (skips don't count)")
    parser.add_argument("--force", action="store_true", help="Re-analyze even if sidecar exists")
    args = parser.parse_args()

    if not analysis_enabled():
        print("Set AUDIO_ANALYSIS=1 and install requirements-analysis.txt", file=sys.stderr)
        return 1

    music_dir = os.environ.get("MUSIC_DIR")
    if not music_dir:
        print("MUSIC_DIR not set", file=sys.stderr)
        return 1

    music_path = Path(music_dir)
    targets = args.playlists or [None]
    failed = 0
    for playlist in targets:
        stats = run_one(music_path, playlist, limit=args.limit, force=args.force)
        failed += stats["failed"]
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
