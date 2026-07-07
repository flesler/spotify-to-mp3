#!/usr/bin/env python3
"""Fetch Spotify metadata for a local playlist folder and print aggregates."""

import json
import os
import sys
from collections import Counter
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

load_dotenv(ROOT / ".env")

from library import LibraryIndex  # noqa: E402
from oauth import OAuth  # noqa: E402
from spotify_meta import FEATURE_KEYS, artist_genres, batch_load  # noqa: E402


def get_token() -> str:
    oauth = OAuth(os.environ["SPOTIFY_CLIENT_ID"], os.environ["SPOTIFY_CLIENT_SECRET"])
    return oauth.authenticate(interactive=False)


def tracks_in_playlist(index: LibraryIndex, playlist: str) -> dict[str, list[str]]:
    prefix = f"{playlist}/"
    found: dict[str, list[str]] = {}
    for spotify_id, entry in index.tracks.items():
        paths = [p for p in entry.get("paths", []) if p.startswith(prefix)]
        if paths:
            found[spotify_id] = paths
    return found


def main() -> int:
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} <playlist-folder-name>", file=sys.stderr)
        return 1

    playlist = sys.argv[1]
    music_dir = Path(os.environ["MUSIC_DIR"])
    index = LibraryIndex(music_dir)
    index.build()

    by_id = tracks_in_playlist(index, playlist)
    if not by_id:
        print(f"No indexed tracks under {playlist}/")
        return 1

    mp3_by_id = {sid: music_dir / paths[0] for sid, paths in by_id.items()}
    print(f"📂 {playlist}: {len(mp3_by_id)} tracks\n")

    token = get_token()
    cached = batch_load(mp3_by_id, token, fetch_missing=True)
    sidecars = sum(1 for p in mp3_by_id.values() if p.with_suffix(".spotify.yaml").exists())
    print(f"💾 Sidecars: {sidecars}/{len(mp3_by_id)}")

    unavailable = sum(1 for c in cached.values() if c.get("audio_features_unavailable"))
    has_features = sum(1 for c in cached.values() if c.get("audio_features"))
    if unavailable and not has_features:
        print("⚠️  audio-features unavailable (Spotify API restriction) — using genres/popularity only\n")
    elif has_features:
        print(f"🎛️  audio-features: {has_features} tracks\n")

    genre_counts: Counter = Counter()
    decade_counts: Counter = Counter()
    popularity: list[int] = []
    feature_sums: dict[str, float] = {}
    feature_n = 0

    print("Tracks:")
    for spotify_id, data in cached.items():
        track = data.get("track", {})
        year = (track.get("album", {}).get("release_date") or "?")[:4]
        decade = f"{year[:3]}0s" if year.isdigit() else "unknown"
        decade_counts[decade] += 1
        pop = track.get("popularity") or 0
        popularity.append(pop)
        genres = artist_genres(data)
        for g in genres:
            genre_counts[g] += 1
        artist_name = data.get("artists", [{}])[0].get("name", "?")
        genre_str = ", ".join(genres[:4]) or "(no genres)"
        print(f"  • {artist_name} - {track.get('name', '?')}  [{year}]  pop={pop}  {genre_str}")

        feat = data.get("audio_features")
        if feat:
            feature_n += 1
            for k in FEATURE_KEYS:
                if k in feat and isinstance(feat[k], (int, float)):
                    feature_sums[k] = feature_sums.get(k, 0) + feat[k]

    print(f"\n📊 Aggregates ({len(cached)} tracks)")
    if popularity:
        print(
            f"  popularity: avg={sum(popularity) / len(popularity):.0f}  min={min(popularity)}  max={max(popularity)}"
        )

    if feature_n:
        print("  audio features (avg):")
        for k in FEATURE_KEYS:
            if k in feature_sums:
                print(f"    {k}: {feature_sums[k] / feature_n:.3f}")

    print("\n  decades:")
    for decade, n in decade_counts.most_common():
        print(f"    {decade}: {n}")

    print("\n  artist genres (track count per genre):")
    for genre, n in genre_counts.most_common(20):
        print(f"    {genre}: {n}")

    out = music_dir / playlist / ".spotify-profile.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "playlist": playlist,
        "track_count": len(cached),
        "genres": genre_counts.most_common(),
        "decades": decade_counts.most_common(),
        "avg_features": {k: feature_sums[k] / feature_n for k in feature_sums} if feature_n else {},
        "tracks": [
            {
                "id": sid,
                "name": c.get("track", {}).get("name"),
                "artist": (c.get("artists") or [{}])[0].get("name"),
                "year": (c.get("track", {}).get("album", {}).get("release_date") or "")[:4],
                "popularity": c.get("track", {}).get("popularity"),
                "genres": artist_genres(c),
                "features": c.get("audio_features"),
                "sidecar": str(mp3_by_id[sid].with_suffix(".spotify.yaml").relative_to(music_dir)),
            }
            for sid, c in cached.items()
        ],
    }
    out.write_text(json.dumps(payload, indent=2))
    print(f"\n💾 Full dump: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
