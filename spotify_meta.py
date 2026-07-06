"""Cache Spotify API metadata as YAML sidecars next to MP3 files."""

import os
import time
from pathlib import Path

import requests
import yaml

SIDECAR_SUFFIX = ".spotify.yaml"
FEATURE_KEYS = (
    "danceability",
    "energy",
    "valence",
    "acousticness",
    "instrumentalness",
    "speechiness",
    "tempo",
    "loudness",
    "key",
    "mode",
)


def sidecar_path(mp3: Path) -> Path:
    return mp3.with_suffix(SIDECAR_SUFFIX)


def load_cache(mp3: Path) -> dict | None:
    path = sidecar_path(mp3)
    if not path.exists():
        return None
    try:
        return yaml.safe_load(path.read_text())
    except (OSError, yaml.YAMLError):
        return None


def save_cache(mp3: Path, data: dict) -> Path:
    path = sidecar_path(mp3)
    path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True))
    return path


def _chunks(items: list[str], size: int):
    for i in range(0, len(items), size):
        yield items[i : i + size]


def _api_get(token: str, url: str, params: dict | None = None) -> requests.Response:
    headers = {"Authorization": f"Bearer {token}"}
    return requests.get(url, headers=headers, params=params, timeout=30)


def _fetch_audio_features(token: str, spotify_id: str) -> tuple[dict | None, bool]:
    """Return (features or None, unavailable_flag). unavailable=True means don't retry."""
    response = _api_get(token, "https://api.spotify.com/v1/audio-features", {"ids": spotify_id})
    if response.status_code == 403:
        return None, True
    response.raise_for_status()
    items = response.json().get("audio_features") or []
    if not items or items[0] is None:
        return None, False
    feat = items[0]
    return {k: feat[k] for k in FEATURE_KEYS if k in feat}, False


def fetch_metadata(token: str, spotify_id: str, liked_fields: dict | None = None) -> dict:
    response = _api_get(token, f"https://api.spotify.com/v1/tracks/{spotify_id}")
    response.raise_for_status()
    track = response.json()

    artist_ids = list(dict.fromkeys(a["id"] for a in track["artists"]))
    artists: list[dict] = []
    for batch in _chunks(artist_ids, 50):
        res = _api_get(token, "https://api.spotify.com/v1/artists", {"ids": ",".join(batch)})
        res.raise_for_status()
        for artist in res.json().get("artists") or []:
            if artist:
                artists.append({"id": artist["id"], "name": artist["name"], "genres": artist.get("genres", [])})

    features, unavailable = _fetch_audio_features(token, spotify_id)

    payload: dict = {
        "fetched_at": time.time(),
        "spotify_id": spotify_id,
        "track": {
            "id": track["id"],
            "name": track["name"],
            "popularity": track.get("popularity"),
            "duration_ms": track.get("duration_ms"),
            "album": track.get("album", {}),
        },
        "artists": artists,
        "audio_features": features,
    }
    if unavailable:
        payload["audio_features_unavailable"] = True
    if liked_fields:
        if liked_fields.get("added_at"):
            payload["added_at"] = liked_fields["added_at"]
    return payload


def cache_for_mp3(mp3: Path, track: dict, token: str | None = None, force: bool = False) -> dict | None:
    """Write or refresh sidecar for an MP3. Returns cached payload or None on failure."""
    spotify_id = track.get("id")
    if not spotify_id or not mp3.exists():
        return None

    existing = load_cache(mp3)
    if existing and existing.get("spotify_id") == spotify_id and not force:
        if existing.get("audio_features") or existing.get("audio_features_unavailable"):
            return existing

    if token is None:
        from oauth import OAuth

        oauth = OAuth(os.environ["SPOTIFY_CLIENT_ID"], os.environ["SPOTIFY_CLIENT_SECRET"])
        token = oauth.authenticate(interactive=False)

    liked_fields = {k: track[k] for k in ("added_at",) if track.get(k)}
    payload = fetch_metadata(token, spotify_id, liked_fields=liked_fields or None)
    save_cache(mp3, payload)
    return payload


def batch_load(mp3_by_id: dict[str, Path], token: str, *, fetch_missing: bool = True) -> dict[str, dict]:
    """Load sidecars for spotify IDs; optionally fetch and write missing ones."""
    out: dict[str, dict] = {}
    missing: dict[str, Path] = {}

    for spotify_id, mp3 in mp3_by_id.items():
        cached = load_cache(mp3)
        if cached and cached.get("spotify_id") == spotify_id:
            out[spotify_id] = cached
        elif fetch_missing:
            missing[spotify_id] = mp3

    for spotify_id, mp3 in missing.items():
        try:
            payload = cache_for_mp3(mp3, {"id": spotify_id}, token=token)
            if payload:
                out[spotify_id] = payload
        except Exception as e:
            print(f"  ⚠️  cache fetch failed for {spotify_id}: {e}")

    return out


def artist_genres(cached: dict) -> list[str]:
    genres: list[str] = []
    for artist in cached.get("artists", []):
        genres.extend(artist.get("genres", []))
    return sorted(set(genres))
