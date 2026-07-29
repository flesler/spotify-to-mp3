#!/usr/bin/env python3
"""Download one Spotify track by ID (duration-validated YouTube rip)."""

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from api import API  # noqa: E402
from library import LibraryIndex  # noqa: E402
from main import download_track, sanitize_filename  # noqa: E402
from track_util import fetch_track, parse_track_id  # noqa: E402


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="Download one Spotify track (YouTube rip, duration-checked vs Spotify)"
    )
    parser.add_argument("track_id", help="Spotify track ID or open.spotify.com/track/… URL")
    parser.add_argument(
        "--playlist",
        default="Liked Songs",
        help="Playlist folder under MUSIC_DIR (default: Liked Songs)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Replace existing file in playlist dir and re-download",
    )
    args = parser.parse_args()

    music_dir = os.environ.get("MUSIC_DIR")
    client_id = os.environ.get("SPOTIFY_CLIENT_ID")
    client_secret = os.environ.get("SPOTIFY_CLIENT_SECRET")
    if not music_dir or not client_id or not client_secret:
        print("MUSIC_DIR, SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET required", file=sys.stderr)
        return 1

    try:
        track_id = parse_track_id(args.track_id)
    except ValueError as e:
        print(f"❌ {e}", file=sys.stderr)
        return 1

    music_path = Path(music_dir)
    playlist_dir = music_path / args.playlist
    playlist_dir.mkdir(parents=True, exist_ok=True)

    api = API(client_id, client_secret)
    track = fetch_track(api, track_id)
    print(f"🎵 {track['artists']} - {track['name']} ({track_id})")

    index = LibraryIndex(music_path)
    index.build()
    result = download_track(
        track,
        playlist_dir,
        music_dir,
        api,
        auto_rename=True,
        auto_link=not args.force,
        fix_metadata=True,
        library_index=index,
        force_redownload=args.force,
    )
    index.save()

    if result in (True, "skipped"):
        filename = sanitize_filename(f"{track['artists']} - {track['name']}")
        mp3 = playlist_dir / f"{filename}.mp3"
        if mp3.is_file():
            print(f"✅ {mp3.relative_to(music_path)}")
            return 0
        existing = index.find_by_spotify_id(track_id)
        if existing and existing.is_file():
            print(f"✅ {existing.relative_to(music_path)}")
            return 0

    print(f"❌ download failed: {result}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
