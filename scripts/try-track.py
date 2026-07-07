#!/usr/bin/env python3
"""Spotify track ID → index lookup → download if missing → analyze → playlist match."""

import os
import re
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from api import API  # noqa: E402
from audio_analysis import ANALYSIS_VERSION, analysis_enabled, cache_for_mp3, load_cache  # noqa: E402
from audio_profile import (  # noqa: E402
    load_cached_profiles,
    load_track_features,
    match_track_to_playlists,
)
from library import LibraryIndex  # noqa: E402
from main import download_track, sanitize_filename  # noqa: E402

TRACK_ID_RE = re.compile(r"(?:^|track[/:])([0-9A-Za-z]{22})")


def parse_track_id(raw: str) -> str:
    raw = raw.strip()
    match = TRACK_ID_RE.search(raw)
    if match:
        return match.group(1)
    if re.fullmatch(r"[0-9A-Za-z]{22}", raw):
        return raw
    raise SystemExit(f"Not a Spotify track ID or URL: {raw}")


def fetch_track(api: API, track_id: str) -> dict:
    data = api._make_request(f"https://api.spotify.com/v1/tracks/{track_id}")
    return {
        "id": data["id"],
        "name": data["name"],
        "artists": ", ".join(artist["name"] for artist in data["artists"]),
        "duration_ms": data["duration_ms"],
        "popularity": data.get("popularity", 0),
        "album": data.get("album", {}),
    }


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
        print("\n  No playlist matches — run correlate-playlist.py on your playlists first")
        return
    print(f"\n  playlist matches (top {top}):")
    for row in matches[:top]:
        timbre = f" timbre={row['timbre']:.2f}" if row["timbre"] is not None else ""
        print(
            f"    {row['score']:.2f}  {row['playlist']:<16}  "
            f"fit={row['feature_fit']:.2f} ({row['stable_dims']} dims){timbre}  tags={row['tag_fit']:.2f}"
        )
        for miss in (row.get("mismatches") or [])[:2]:
            key = str(miss["key"]).split(".", 1)[-1]
            print(f"         ↳ {key}: song={miss['value']} playlist≈{miss['mean']} (z={miss['z']:+.1f})")
        for v in (row.get("violations") or [])[:2]:
            print(f"         ✗ {v['label']}: song={v['value']} playlist≈{v['mean']} (z={v['z']:+.1f})")


def ensure_analyzed(mp3: Path) -> None:
    if not analysis_enabled():
        print("⚠️  AUDIO_ANALYSIS=0 — skipping analysis", file=sys.stderr)
        return
    cached = load_cache(mp3)
    if cached and cached.get("analysis_version") == ANALYSIS_VERSION:
        print(f"🎛️  cached analysis")
        return
    print(f"🎛️  analyzing…")
    cache_for_mp3(mp3)


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Try a Spotify track: index → download → analyze → match")
    parser.add_argument("track_id", help="Spotify track ID or open.spotify.com/track/… URL")
    parser.add_argument("--top", type=int, default=6, help="Top playlist matches to show")
    parser.add_argument("--no-match", action="store_true", help="Skip playlist matching")
    parser.add_argument("--refresh-profiles", action="store_true", help="Rebuild missing .playlist-profile.json")
    args = parser.parse_args()

    client_id = os.environ.get("SPOTIFY_CLIENT_ID")
    client_secret = os.environ.get("SPOTIFY_CLIENT_SECRET")
    music_dir = os.environ.get("MUSIC_DIR")
    if not client_id or not client_secret or not music_dir:
        print("SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET, MUSIC_DIR required", file=sys.stderr)
        return 1

    track_id = parse_track_id(args.track_id)
    music_path = Path(music_dir)
    index = LibraryIndex(music_path)

    mp3 = index.lookup_spotify_id(track_id)
    if mp3:
        print(f"📂 indexed: {mp3.relative_to(music_path)}")
    else:
        api = API(client_id, client_secret)
        track = fetch_track(api, track_id)
        print(f"🎵 {track['artists']} - {track['name']} ({track_id})")

        index.build()
        mp3 = index.find_by_spotify_id(track_id)
        if mp3:
            print(f"📂 found after index: {mp3.relative_to(music_path)}")
        else:
            playlist_dir = music_path / "Liked Songs"
            playlist_dir.mkdir(parents=True, exist_ok=True)
            result = download_track(
                track,
                playlist_dir,
                music_dir,
                api,
                auto_rename=True,
                auto_link=True,
                fix_metadata=True,
                library_index=index,
            )
            index.save()
            if result not in (True, "skipped"):
                return 1
            mp3 = index.find_by_spotify_id(track_id)
            if not mp3:
                filename = sanitize_filename(f"{track['artists']} - {track['name']}")
                mp3 = playlist_dir / f"{filename}.mp3"
            print(f"⬇️  {mp3.relative_to(music_path)}")

    if not mp3.is_file():
        print(f"❌ MP3 not found: {mp3}", file=sys.stderr)
        return 1

    ensure_analyzed(mp3)

    if args.no_match:
        return 0

    profiles = load_cached_profiles(music_path, refresh_missing=args.refresh_profiles)
    if not profiles:
        print("⚠️  No playlist profiles — run: ./scripts/correlate-playlist.py <playlists…>", file=sys.stderr)
        return 0

    track = load_track_features(mp3)
    matches = match_track_to_playlists(track, profiles)
    print_matches(track, matches, top=args.top)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
