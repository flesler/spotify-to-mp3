"""Tests for optional Essentia audio analysis."""

import pytest
from unittest.mock import MagicMock, patch

import yaml

from audio_analysis import (
    ANALYSIS_VERSION,
    _arc_profile,
    _binary_positive_score,
    _head_scores,
    _top_k,
    analysis_enabled,
    analyze_library,
    cache_is_fresh,
    essentia_use_cpu,
    iter_track_groups,
    propagate_sidecar,
    save_cache,
    sidecar_path,
)
from library import LibraryIndex


def test_iter_track_groups_dedupes_by_inode(tmp_path):
    music = tmp_path / "Music"
    a = music / "A"
    b = music / "B"
    a.mkdir(parents=True)
    b.mkdir(parents=True)
    primary = a / "song.mp3"
    duplicate = b / "song.mp3"
    primary.write_bytes(b"audio")
    duplicate.hardlink_to(primary)

    index = LibraryIndex(music)
    index.tracks["id1"] = {"paths": [str(primary.relative_to(music)), str(duplicate.relative_to(music))], "mtime": 0}
    groups = iter_track_groups(index)
    assert len(groups) == 1
    assert len(groups[0][1]) == 2


def test_iter_track_groups_playlist_filter(tmp_path):
    music = tmp_path / "Music"
    (music / "Rivotril").mkdir(parents=True)
    (music / "Liked Songs").mkdir(parents=True)
    (music / "Rivotril" / "a.mp3").write_bytes(b"x")
    (music / "Liked Songs" / "b.mp3").write_bytes(b"y")

    index = LibraryIndex(music)
    index.tracks["id1"] = {"paths": ["Rivotril/a.mp3", "Liked Songs/b.mp3"], "mtime": 0}
    assert len(iter_track_groups(index, playlist="Rivotril")) == 1
    assert len(iter_track_groups(index, playlist="Liked Songs")) == 1


def test_propagate_sidecar_hardlinks(tmp_path):
    a_dir = tmp_path / "A"
    b_dir = tmp_path / "B"
    a_dir.mkdir()
    b_dir.mkdir()
    mp3_a = a_dir / "song.mp3"
    mp3_b = b_dir / "song.mp3"
    mp3_a.write_bytes(b"x")
    mp3_b.write_bytes(b"x")
    save_cache(mp3_a, {"analysis_version": ANALYSIS_VERSION, "deam": {"arousal_ramp": 1.0}})

    linked = propagate_sidecar(mp3_a, [mp3_b])
    assert linked == 1
    assert sidecar_path(mp3_b).exists()
    assert sidecar_path(mp3_a).stat().st_ino == sidecar_path(mp3_b).stat().st_ino


def test_analysis_disabled_by_default(monkeypatch):
    monkeypatch.delenv("AUDIO_ANALYSIS", raising=False)
    assert analysis_enabled() is False


def test_analysis_enabled_with_flag(monkeypatch):
    monkeypatch.setenv("AUDIO_ANALYSIS", "1")
    with patch.dict("sys.modules", {"essentia": object()}):
        assert analysis_enabled() is True


def test_essentia_cpu_flag(monkeypatch):
    monkeypatch.setenv("ESSENTIA_CPU", "0")
    assert essentia_use_cpu() is False
    monkeypatch.setenv("ESSENTIA_CPU", "1")
    assert essentia_use_cpu() is True


def test_cache_is_fresh():
    assert cache_is_fresh(None) is False
    assert cache_is_fresh({"analysis_version": 1}) is False
    assert cache_is_fresh({"analysis_version": ANALYSIS_VERSION}) is True


def test_head_scores():
    preds = [[0.8, 0.2], [0.6, 0.4]]
    scores = _head_scores(preds, ["yes", "no"])
    assert scores["yes"] == 0.7
    assert scores["no"] == pytest.approx(0.3)


def test_binary_positive_score():
    preds = [[0.8, 0.2], [0.6, 0.4]]
    assert _binary_positive_score(preds, "mood_happy", ["happy", "non_happy"]) == 0.7
    assert _binary_positive_score(preds, "voice_instrumental", ["instrumental", "voice"]) == 0.3


def test_top_k():
    ranked = _top_k({"rock": 0.9, "pop": 0.5, "jazz": 0.7}, 2)
    assert ranked[0]["label"] == "rock"
    assert ranked[1]["label"] == "jazz"


def test_arc_profile_ramp():
    deam = [[3.0, 2.0], [4.0, 3.0], [5.0, 4.0], [6.0, 6.0]]
    arc = _arc_profile(deam)
    assert arc["arousal_ramp"] > 0
    assert arc["arousal_peak"] == 6.0


def test_audio_sidecar_roundtrip(tmp_path):
    mp3 = tmp_path / "song.mp3"
    mp3.write_bytes(b"x")
    data = {
        "analysis_version": ANALYSIS_VERSION,
        "deam": {"arousal_ramp": 1.5},
        "classifiers": {"gender": 0.7},
    }
    path = save_cache(mp3, data)
    assert path.name == "song.audio.yaml"
    assert yaml.safe_load(path.read_text())["deam"]["arousal_ramp"] == 1.5


@patch("audio_analysis.propagate_sidecar", return_value=0)
@patch("audio_analysis.cache_for_mp3")
@patch("audio_analysis._get_models")
@patch("audio_analysis.LibraryIndex")
def test_analyze_library_limit_skips_dont_count(mock_index_cls, mock_get_models, mock_cache, _mock_prop, tmp_path):
    music = tmp_path / "Music"
    music.mkdir()
    groups = []
    for i in range(5):
        mp3 = music / f"song{i}.mp3"
        mp3.write_bytes(b"x")
        groups.append((mp3, [mp3]))

    mock_index_cls.return_value = MagicMock()
    mock_get_models.return_value = MagicMock(head_count=14)

    fresh_flags = iter([True, True, True, False, False])

    def fake_fresh(existing):
        return next(fresh_flags)

    with patch("audio_analysis.iter_track_groups", return_value=groups):
        with patch("audio_analysis.load_cache", return_value={"analysis_version": ANALYSIS_VERSION}):
            with patch("audio_analysis.cache_is_fresh", side_effect=fake_fresh):
                stats = analyze_library(music, limit=2)

    assert stats["analyzed"] == 2
    assert stats["skipped"] == 3
    assert mock_cache.call_count == 2
