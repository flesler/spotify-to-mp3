"""Tests for Spotify metadata sidecar cache."""

from unittest.mock import patch

import yaml

from spotify_meta import artist_genres, cache_for_mp3, load_cache, save_cache, sidecar_path


def test_sidecar_roundtrip(tmp_path):
    mp3 = tmp_path / "Artist - Song.mp3"
    mp3.write_bytes(b"x")
    data = {"spotify_id": "abc", "track": {"name": "Song"}, "artists": [{"genres": ["rock"]}]}
    path = save_cache(mp3, data)
    assert path == sidecar_path(mp3)
    assert path.name == "Artist - Song.spotify.yaml"
    assert load_cache(mp3) == data


def test_cache_for_mp3_skips_when_present(tmp_path):
    mp3 = tmp_path / "t.mp3"
    mp3.write_bytes(b"x")
    save_cache(mp3, {"spotify_id": "id1", "audio_features_unavailable": True, "track": {"id": "id1"}, "artists": []})

    with patch("spotify_meta.fetch_metadata") as fetch:
        result = cache_for_mp3(mp3, {"id": "id1"}, token="tok")
        fetch.assert_not_called()
    assert result["spotify_id"] == "id1"


def test_artist_genres_dedupes():
    cached = {"artists": [{"genres": ["rock", "indie"]}, {"genres": ["rock"]}]}
    assert artist_genres(cached) == ["indie", "rock"]


def test_cache_for_mp3_fetches_when_missing(tmp_path):
    mp3 = tmp_path / "t.mp3"
    mp3.write_bytes(b"x")
    payload = {
        "spotify_id": "id1",
        "track": {"id": "id1", "name": "N"},
        "artists": [],
        "audio_features": None,
        "audio_features_unavailable": True,
    }

    with patch("spotify_meta.fetch_metadata", return_value=payload) as fetch:
        result = cache_for_mp3(mp3, {"id": "id1", "added_at": "2024-01-01"}, token="tok")

    fetch.assert_called_once_with("tok", "id1", liked_fields={"added_at": "2024-01-01"})
    assert yaml.safe_load(sidecar_path(mp3).read_text())["spotify_id"] == "id1"
    assert result == payload
