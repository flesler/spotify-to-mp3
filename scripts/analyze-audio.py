#!/usr/bin/env python3
"""Analyze local MP3(s) with Essentia and write .audio.yaml sidecars."""

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from audio_analysis import analysis_enabled, cache_for_mp3, sidecar_path  # noqa: E402


def main() -> int:
    if len(sys.argv) < 2:
        print(f"Usage: AUDIO_ANALYSIS=1 {sys.argv[0]} <mp3-or-dir> [...]", file=sys.stderr)
        return 1

    if not analysis_enabled():
        print("AUDIO_ANALYSIS=1 and essentia-tensorflow required (host only)", file=sys.stderr)
        return 1

    music_dir = Path(os.environ.get("MUSIC_DIR", ""))
    paths: list[Path] = []
    for arg in sys.argv[1:]:
        p = Path(arg)
        if not p.is_absolute() and music_dir and (music_dir / p).exists():
            p = music_dir / p
        if p.is_dir():
            paths.extend(sorted(p.rglob("*.mp3")))
        elif p.is_file():
            paths.append(p)
        else:
            print(f"⚠️  not found: {arg}", file=sys.stderr)

    if not paths:
        return 1

    for mp3 in paths:
        try:
            data = cache_for_mp3(mp3, force="--force" in sys.argv)
            deam = (data or {}).get("deam", {})
            cls = (data or {}).get("classifiers", {})
            print(
                f"✅ {mp3.name} → {sidecar_path(mp3).name}  "
                f"ramp={deam.get('arousal_ramp', 0):.2f}  "
                f"electronic={cls.get('mood_electronic', 0):.2f}  "
                f"female={cls.get('gender', 0):.2f}"
            )
        except Exception as e:
            print(f"❌ {mp3}: {e}", file=sys.stderr)
            return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
