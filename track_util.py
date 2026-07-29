"""Spotify track ID parsing and single-track fetch."""

import re

from api import API

TRACK_ID_RE = re.compile(r"(?:^|track[/:])([0-9A-Za-z]{22})")


def parse_track_id(raw: str) -> str:
    raw = raw.strip()
    match = TRACK_ID_RE.search(raw)
    if match:
        return match.group(1)
    if re.fullmatch(r"[0-9A-Za-z]{22}", raw):
        return raw
    raise ValueError(f"Not a Spotify track ID or URL: {raw}")


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
