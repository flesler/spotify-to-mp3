#!/usr/bin/env python3
"""Profile playlist vibe from audio sidecars, tags, years, and library baselines."""

import argparse
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from audio_profile import compare_playlists, profile_playlist, save_playlist_profile  # noqa: E402

COMPARE_KEYS = (
    "classifiers.voice_instrumental",
    "classifiers.danceability",
    "classifiers.mood_electronic",
    "deam.arousal_mean",
    "deam.valence_mean",
    "features.bpm",
    "features.dynamic_complexity",
    "features.loudness",
)


def print_profile(profile: dict) -> None:
    n = profile["track_count"]
    print(f"\n📂 {profile['playlist']} ({n} tracks)")
    if n == 0:
        print("  (no v3 audio sidecars)")
        return

    years = profile.get("years") or {}
    if years.get("n"):
        print(f"  years: {years['min']}–{years['max']} (avg {years['mean']:.0f}, n={years['n']})")
        decades = profile.get("decades") or []
        if decades:
            decade_str = ", ".join(f"{d}:{c}" for d, c in decades[:5])
            print(f"  decades: {decade_str}")

    cohesion = profile.get("embedding_cohesion")
    if cohesion is not None:
        print(f"  timbre cohesion: {cohesion:.2f}")

    tags = profile.get("tags") or []
    if tags:
        tag_line = ", ".join(f"{t['label']} ({int(t['frac'] * 100)}%)" for t in tags[:8])
        print(f"\n  tag signature: {tag_line}")

    traits = profile.get("traits") or []
    stable = profile.get("stable_features") or []
    if stable:
        stable_line = ", ".join(str(s["label"]) for s in stable[:8])
        print(f"\n  stable dims ({len(stable)}): {stable_line}")
    if traits:
        print("\n  consistent classifiers:")
        for trait in traits[:8]:
            print(f"    {trait['phrase']:16}  {trait['mean']:.2f} ± {trait['std']:.2f}")

    vs_lib = profile.get("vs_library") or []
    if vs_lib:
        print("\n  vs library (z-score, tight playlist):")
        for row in vs_lib[:8]:
            sign = "+" if row["z"] >= 0 else ""
            print(
                f"    {row['phrase']:20}  playlist={row['mean']:.2f}  "
                f"lib={row['library_mean']:.2f}  z={sign}{row['z']}"
            )

    outliers = profile.get("outliers") or []
    if outliers:
        print("\n  doesn't fit (low timbre similarity):")
        for row in outliers:
            print(f"    {row['similarity']:.2f}  {row['name'][:70]}")

    genres = profile.get("genres_dortmund") or []
    if genres:
        print("\n  genre #1:")
        for label, count in genres[:5]:
            print(f"    {label}: {count}/{n}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Profile playlist vibe from Essentia audio sidecars")
    parser.add_argument("playlists", nargs="+", help="Playlist folder name(s) under MUSIC_DIR")
    parser.add_argument("--min-distance", type=float, default=0.2, help="Classifier |mean−0.5| threshold")
    parser.add_argument("--max-std", type=float, default=0.25, help="Max std for a consistent trait")
    parser.add_argument("--min-z", type=float, default=1.0, help="Min |z| vs library")
    parser.add_argument("--no-save", action="store_true", help="Do not write .playlist-profile.json")
    args = parser.parse_args()

    music_dir = os.environ.get("MUSIC_DIR")
    if not music_dir:
        print("MUSIC_DIR not set", file=sys.stderr)
        return 1

    music_path = Path(music_dir)
    profiles = []
    for name in args.playlists:
        folder = music_path / name
        if not folder.is_dir():
            print(f"⚠️  Missing folder: {folder}", file=sys.stderr)
            continue
        profile = profile_playlist(
            folder,
            music_path,
            min_distance=args.min_distance,
            max_std=args.max_std,
            min_z=args.min_z,
        )
        profiles.append(profile)
        print_profile(profile)
        if not args.no_save:
            out = save_playlist_profile(folder, profile)
            print(f"\n  💾 {out}")

    if len(profiles) > 1:
        print("\n📊 Means across playlists")
        table = compare_playlists(profiles, COMPARE_KEYS)
        header = ["metric", *[p["playlist"] for p in profiles]]
        print("  " + "  ".join(f"{h:>14}" for h in header))
        for key in COMPARE_KEYS:
            row = table.get(key, {})
            if not row:
                continue
            label = key.split(".", 1)[-1]
            cells = [f"{row.get(p['playlist'], float('nan')):>14.1f}" for p in profiles]
            print(f"  {label:14}" + "".join(cells))

    return 0 if profiles else 1


if __name__ == "__main__":
    raise SystemExit(main())
