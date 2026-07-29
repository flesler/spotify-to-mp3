"""Tests for yt-dlp integration helpers"""

from unittest.mock import MagicMock, patch

from ytdlp_util import (
    duration_acceptable,
    download_with_search_fallback,
    is_rate_limited,
    js_runtime_args,
    rank_candidates_by_duration,
    read_youtube_id,
    search_youtube_videos,
    ytdlp_cmd,
    ytdlp_error_message,
)


def test_ytdlp_cmd_uses_current_python():
    import sys

    assert ytdlp_cmd() == [sys.executable, "-m", "yt_dlp"]


def test_ytdlp_error_message_prefers_error_line():
    stderr = """Deprecated Feature: Support for Python version 3.10 has been deprecated.
WARNING: [youtube] something
ERROR: [youtube] abc123: This video is not available
"""
    assert ytdlp_error_message(stderr) == "[youtube] abc123: This video is not available"


def test_ytdlp_error_message_skips_deprecation():
    stderr = "Deprecated Feature: Support for Python version 3.10 has been deprecated.\n"
    assert ytdlp_error_message(stderr) == "unknown error"


def test_js_runtime_args_prefers_node():
    with patch("ytdlp_util.shutil.which", side_effect=lambda name: "/usr/bin/node" if name == "node" else None):
        assert js_runtime_args() == ["--js-runtimes", "node"]


def test_js_runtime_args_falls_back_to_deno():
    with patch("ytdlp_util.shutil.which", side_effect=lambda name: "/usr/bin/deno" if name == "deno" else None):
        assert js_runtime_args() == ["--js-runtimes", "deno"]


def test_search_youtube_videos_parses_output():
    completed = MagicMock(returncode=0, stdout="abc123|||Song Title|||201\n", stderr="")
    with patch("ytdlp_util.subprocess.run", return_value=completed):
        assert search_youtube_videos("query", limit=3) == [("abc123", "Song Title", 201.0)]


def test_rank_candidates_by_duration_prefers_close_match():
    candidates = [
        ("bad", "Broadcast cut", 314.0),
        ("good", "Clean version", 259.0),
        ("other", "Other", None),
    ]
    ranked = rank_candidates_by_duration(candidates, 258_000)
    assert ranked[0][0] == "good"


def test_read_youtube_id_deletes_sidecar(tmp_path):
    info_json = tmp_path / "track.info.json"
    info_json.write_text('{"id": "abc123xyz"}', encoding="utf-8")

    assert read_youtube_id(info_json) == "abc123xyz"
    assert not info_json.exists()


def test_download_with_search_fallback_tries_next_result(tmp_path):
    playlist_dir = tmp_path / "pl"
    playlist_dir.mkdir()
    mp3 = playlist_dir / "Artist - Song.mp3"

    fail = MagicMock(returncode=1, stderr="ERROR: not available")
    ok = MagicMock(returncode=0, stderr="")
    candidates = [("bad1", "Bad 1", 400.0), ("good", "Good Song", 200.0)]

    def fake_download(video_id, output_template, quality):
        if video_id == "good":
            mp3.write_bytes(b"mp3")
            (playlist_dir / "Artist - Song.info.json").write_text('{"id": "good"}', encoding="utf-8")
            return ok
        return fail

    with patch("ytdlp_util.search_youtube_videos", return_value=candidates):
        with patch("ytdlp_util.download_youtube_video", side_effect=fake_download):
            with patch("ytdlp_util.mp3_duration_sec", return_value=200.0):
                success, youtube_id, error = download_with_search_fallback(
                    "Artist - Song",
                    playlist_dir,
                    "Artist - Song",
                    "192K",
                    target_duration_ms=200_000,
                    tolerance_sec=12.0,
                )

    assert success is True
    assert youtube_id == "good"
    assert error == ""


def test_download_rejects_when_all_candidates_drift(tmp_path):
    playlist_dir = tmp_path / "pl"
    playlist_dir.mkdir()
    mp3 = playlist_dir / "Artist - Song.mp3"

    ok = MagicMock(returncode=0, stderr="")
    candidates = [("only", "Only result", 200.0)]

    def fake_download(video_id, output_template, quality):
        mp3.write_bytes(b"mp3")
        (playlist_dir / "Artist - Song.info.json").write_text('{"id": "only"}', encoding="utf-8")
        return ok

    with patch("ytdlp_util.search_youtube_videos", return_value=candidates):
        with patch("ytdlp_util.download_youtube_video", side_effect=fake_download):
            with patch("ytdlp_util.mp3_duration_sec", return_value=300.0):
                success, youtube_id, error = download_with_search_fallback(
                    "Artist - Song",
                    playlist_dir,
                    "Artist - Song",
                    "192K",
                    target_duration_ms=200_000,
                    tolerance_sec=12.0,
                )

    assert success is False
    assert youtube_id is None
    assert "duration drift" in error


def test_duration_acceptable_within_tolerance(tmp_path):
    mp3 = tmp_path / "song.mp3"
    mp3.write_bytes(b"x")
    with patch("ytdlp_util.mp3_duration_sec", return_value=200.0):
        assert duration_acceptable(mp3, 201_000, tolerance_sec=12.0) is True
        assert duration_acceptable(mp3, 250_000, tolerance_sec=12.0) is False


def test_is_rate_limited_detects_429():
    assert is_rate_limited("HTTP Error 429: Too Many Requests")
    assert not is_rate_limited("This video is not available")
