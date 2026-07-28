#!/usr/bin/env python3
"""Fetch lyrics sidecars via LRCLIB — library, playlists, or individual MP3s."""

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from lyrics import fetch_lyrics, lyrics_enabled  # noqa: E402


def resolve_targets(targets: list[str], music_dir: Path | None) -> tuple[list[Path], list[str]]:
    paths: list[Path] = []
    playlists: list[str] = []

    for arg in targets:
        p = Path(arg)
        if not p.is_absolute() and music_dir and (music_dir / p).exists():
            p = music_dir / p
        if p.exists():
            if p.is_dir():
                paths.extend(sorted(p.rglob("*.mp3")))
            else:
                paths.append(p)
        else:
            playlists.append(arg)

    return paths, playlists


def run_library(music_dir: Path, playlist: str | None, *, limit: int | None, force: bool) -> dict:
    label = playlist or "library"
    print(f"🎤 Fetching lyrics for {label}…")
    return fetch_lyrics(music_dir, playlist=playlist, limit=limit, force=force)


def print_stats(stats: dict) -> None:
    print(
        f"✅ Done: {stats['fetched']} fetched, {stats['skipped']} skipped, "
        f"{stats['missing']} not found, {stats['no_tags']} no tags, "
        f"{stats['linked']} sidecar links, {stats['failed']} failed"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="LRCLIB lyrics fetch (host only, no GPU)")
    parser.add_argument(
        "targets",
        nargs="*",
        help="Playlist folders, MP3 files, or dirs under MUSIC_DIR (default: full library)",
    )
    parser.add_argument("--limit", type=int, help="Max tracks to fetch per run (skips don't count)")
    parser.add_argument("--force", action="store_true", help="Re-fetch even if sidecar or nomatch tag exists")
    args = parser.parse_args()

    if not lyrics_enabled():
        print("Set LYRICS_FETCH=1 in .env", file=sys.stderr)
        return 1

    music_dir_raw = os.environ.get("MUSIC_DIR")
    if not music_dir_raw:
        print("MUSIC_DIR not set", file=sys.stderr)
        return 1

    music_dir = Path(music_dir_raw)
    paths, playlists = resolve_targets(args.targets, music_dir)
    failed = 0

    if paths:
        print(f"🎤 Fetching lyrics for {len(paths)} file(s)…")
        stats = fetch_lyrics(music_dir, mp3_paths=paths, limit=args.limit, force=args.force)
        print_stats(stats)
        failed += stats["failed"]
    elif playlists:
        for playlist in playlists:
            stats = run_library(music_dir, playlist, limit=args.limit, force=args.force)
            print_stats(stats)
            failed += stats["failed"]
    else:
        stats = run_library(music_dir, None, limit=args.limit, force=args.force)
        print_stats(stats)
        failed += stats["failed"]

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
