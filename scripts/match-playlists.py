#!/usr/bin/env python3
"""Match one analyzed track against cached playlist profiles."""

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from audio_profile import (  # noqa: E402
    load_cached_profiles,
    load_track_features,
    match_track_to_playlists,
    profile_playlist,
    resolve_track_mp3,
    save_playlist_profile,
)


def print_matches(track: dict, matches: list[dict], *, top: int) -> None:
    print(f"\n🎵 {track['name']}")
    tags = ", ".join(track.get("tags", [])[:6])
    if tags:
        print(f"   tags: {tags}")
    cls = track.get("classifiers") or {}
    print(
        f"   dance={cls.get('danceability', 0):.2f}  "
        f"electronic={cls.get('mood_electronic', 0):.2f}  "
        f"voice={cls.get('voice_instrumental', 0):.2f}  "
        f"female={cls.get('gender', 0):.2f}"
    )

    if not matches:
        print("\n  No playlist matches (generate profiles with correlate-playlist.py)")
        return

    print(f"\n  playlist matches (top {top}):")
    for row in matches[:top]:
        timbre = f" timbre={row['timbre']:.2f}" if row["timbre"] is not None else ""
        print(
            f"    {row['score']:.2f}  {row['playlist']:<16}  "
            f"fit={row['feature_fit']:.2f} ({row['stable_dims']} dims){timbre}  tags={row['tag_fit']:.2f}  "
            f"n={row['track_count']}"
        )
        for miss in (row.get("mismatches") or [])[:3]:
            key = str(miss["key"]).split(".", 1)[-1]
            print(f"         ↳ {key}: song={miss['value']} playlist≈{miss['mean']} (z={miss['z']:+.1f})")


def main() -> int:
    parser = argparse.ArgumentParser(description="Find playlists that match an analyzed track")
    parser.add_argument("track", help="MP3 path, or track name to search under MUSIC_DIR")
    parser.add_argument("--top", type=int, default=8, help="Show top N playlists")
    parser.add_argument("--min-score", type=float, default=0.0, help="Minimum match score")
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Rebuild missing .playlist-profile.json files before matching",
    )
    parser.add_argument(
        "--refresh-all",
        action="store_true",
        help="Rebuild all playlist profiles before matching",
    )
    args = parser.parse_args()

    music_dir = os.environ.get("MUSIC_DIR")
    if not music_dir:
        print("MUSIC_DIR not set", file=sys.stderr)
        return 1

    music_path = Path(music_dir)
    mp3 = resolve_track_mp3(music_path, args.track)
    if mp3 is None:
        print(f"Track not found: {args.track}", file=sys.stderr)
        return 1

    if args.refresh_all:
        for folder in sorted(p for p in music_path.iterdir() if p.is_dir() and not p.name.startswith(".")):
            if list(folder.glob("*.audio.yaml")):
                save_playlist_profile(folder, profile_playlist(folder, music_path))

    profiles = load_cached_profiles(music_path, refresh_missing=args.refresh or args.refresh_all)
    if not profiles:
        print("No cached playlist profiles — run: ./scripts/correlate-playlist.py <playlist...>", file=sys.stderr)
        return 1

    try:
        track = load_track_features(mp3)
    except RuntimeError as e:
        print(e, file=sys.stderr)
        return 1

    matches = match_track_to_playlists(track, profiles, min_score=args.min_score)
    print_matches(track, matches, top=args.top)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
