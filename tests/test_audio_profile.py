"""Tests for audio sidecar playlist profiling."""

from pathlib import Path

import yaml

from audio_analysis import ANALYSIS_VERSION
from audio_profile import (
    classifier_traits,
    feature_matrix,
    load_playlist_profile,
    load_playlist_tracks,
    load_track_features,
    match_track_to_playlists,
    profile_playlist,
    save_playlist_profile,
    summarize_features,
    year_from_mp3,
)


def _write_sidecar(path: Path, *, year_tag: str | None = None, **classifiers: float) -> None:
    defaults = {
        "mood_happy": 0.5,
        "mood_sad": 0.5,
        "mood_relaxed": 0.5,
        "mood_aggressive": 0.5,
        "mood_electronic": 0.5,
        "mood_party": 0.5,
        "mood_acoustic": 0.5,
        "gender": 0.5,
        "danceability": 0.5,
        "voice_instrumental": 0.5,
        "tonal_atonal": 0.5,
    }
    defaults.update(classifiers)
    payload = {
        "analysis_version": ANALYSIS_VERSION,
        "features": {"bpm": 100.0, "key_strength": 0.8, "loudness": 100.0, "dynamic_complexity": 2.0},
        "deam": {
            "valence_mean": 5.0,
            "arousal_mean": 5.0,
            "arousal_peak": 7.0,
            "arousal_ramp": 2.0,
        },
        "classifiers": defaults,
        "tags": [{"label": "rock", "score": 0.5}],
        "genres": {"genre_dortmund": [{"label": "rock", "score": 0.9}]},
        "embedding_mean": [1.0, 0.0, 0.5],
    }
    path.write_text(yaml.safe_dump(payload))
    mp3 = path.parent / path.name.replace(".audio.yaml", ".mp3")
    mp3.write_bytes(b"ID3")
    if year_tag:
        from mutagen.id3 import ID3, TDRC

        tags = ID3()
        tags.add(TDRC(encoding=3, text=year_tag))
        tags.save(mp3, v2_version=3)


def test_year_from_mp3(tmp_path):
    mp3 = tmp_path / "song.mp3"
    mp3.write_bytes(b"x")
    from mutagen.id3 import ID3, TDRC

    tags = ID3()
    tags.add(TDRC(encoding=3, text="1987"))
    tags.save(mp3, v2_version=3)
    assert year_from_mp3(mp3) == 1987


def test_classifier_traits_skip_neutral_moods(tmp_path):
    playlist = tmp_path / "Epic"
    playlist.mkdir()
    for i in range(5):
        _write_sidecar(
            playlist / f"track{i}.audio.yaml",
            gender=0.82,
            voice_instrumental=0.18,
            mood_happy=0.26,
            mood_sad=0.26,
            mood_party=0.45,
        )

    rows = load_playlist_tracks(playlist)
    names, matrix = feature_matrix(rows)
    summary = summarize_features(names, matrix)
    traits = classifier_traits(summary, min_distance=0.15, max_std=0.05)
    phrases = {t["phrase"] for t in traits}
    assert "female" in phrases
    assert "instrumental" in phrases
    assert "happy" not in phrases
    assert "sad" not in phrases
    assert "party" not in phrases


def test_profile_includes_decades(tmp_path):
    playlist = tmp_path / "Oldies"
    playlist.mkdir()
    for year in (1982, 1985, 1988, 1991):
        _write_sidecar(playlist / f"track{year}.audio.yaml", year_tag=str(year), mood_happy=0.7)

    profile = profile_playlist(playlist, tmp_path)
    assert profile["years"]["n"] == 4
    assert ("1980s", 3) in profile["decades"]


def test_save_profile_and_match_track(tmp_path):
    music = tmp_path / "Music"
    dance = music / "Pila"
    chill = music / "Tranca"
    dance.mkdir(parents=True)
    chill.mkdir(parents=True)

    for i in range(4):
        _write_sidecar(
            dance / f"d{i}.audio.yaml",
            danceability=0.9,
            mood_electronic=0.85,
            voice_instrumental=0.8,
            mood_relaxed=0.2,
        )
        _write_sidecar(
            chill / f"c{i}.audio.yaml",
            danceability=0.3,
            mood_electronic=0.2,
            voice_instrumental=0.5,
            mood_relaxed=0.8,
            mood_sad=0.7,
        )

    save_playlist_profile(dance, profile_playlist(dance, music))
    save_playlist_profile(chill, profile_playlist(chill, music))
    dance_profile = load_playlist_profile(dance)
    chill_profile = load_playlist_profile(chill)
    assert dance_profile is not None
    assert dance_profile.get("stable_features")

    mp3 = dance / "d0.mp3"
    track = load_track_features(mp3)
    matches = match_track_to_playlists(track, [dance_profile, chill_profile])
    assert matches[0]["playlist"] == "Pila"
    assert matches[0]["score"] > matches[1]["score"]
