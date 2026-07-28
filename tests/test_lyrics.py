"""Tests for LRCLIB lyrics fetch."""

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from lyrics import (
    clear_lyrics_lrclib_tag,
    fetch_lrclib,
    has_lyrics_sidecar,
    lrc_to_plain,
    lyrics_lrclib_nomatch,
    read_mp3_tags,
    set_lyrics_nomatch,
    should_skip_lyrics_fetch,
    write_lyrics_sidecar,
)


@pytest.fixture
def tagged_mp3(tmp_path):
    mp3 = tmp_path / "Artist - Title.mp3"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=1",
            "-acodec",
            "libmp3lame",
            "-q:a",
            "9",
            str(mp3),
        ],
        capture_output=True,
        check=True,
    )
    return mp3


def test_lrc_to_plain_strips_timestamps():
    lrc = """[00:12.34]First line
[01:00.00]Second line
[ar:Artist]
"""
    assert lrc_to_plain(lrc) == "First line\nSecond line"


def test_write_lyrics_sidecar_prefers_lrc(tmp_path):
    mp3 = tmp_path / "Artist - Song.mp3"
    mp3.write_bytes(b"ID3")
    record = {
        "plainLyrics": "plain only",
        "syncedLyrics": "[00:01.00]synced line\n",
        "instrumental": False,
    }
    path = write_lyrics_sidecar(mp3, record)
    assert path == mp3.with_suffix(".lrc")
    assert "[00:01.00]" in path.read_text()


def test_write_lyrics_sidecar_plain_fallback(tmp_path):
    mp3 = tmp_path / "Artist - Song.mp3"
    mp3.write_bytes(b"ID3")
    record = {"plainLyrics": "hello\nworld", "instrumental": False}
    path = write_lyrics_sidecar(mp3, record)
    assert path == mp3.with_suffix(".txt")
    assert path.read_text() == "hello\nworld\n"


def test_write_lyrics_sidecar_skips_instrumental(tmp_path):
    mp3 = tmp_path / "Artist - Song.mp3"
    mp3.write_bytes(b"ID3")
    assert write_lyrics_sidecar(mp3, {"instrumental": True, "plainLyrics": "x"}) is None


def test_has_lyrics_sidecar(tmp_path):
    mp3 = tmp_path / "a.mp3"
    mp3.write_bytes(b"x")
    assert not has_lyrics_sidecar(mp3)
    mp3.with_suffix(".lrc").write_text("[00:00.00]hi\n")
    assert has_lyrics_sidecar(mp3)


def test_should_skip_nomatch(tagged_mp3):
    assert should_skip_lyrics_fetch(tagged_mp3) is None
    set_lyrics_nomatch(tagged_mp3)
    assert should_skip_lyrics_fetch(tagged_mp3) == "nomatch"
    assert should_skip_lyrics_fetch(tagged_mp3, force=True) is None
    clear_lyrics_lrclib_tag(tagged_mp3)
    assert not lyrics_lrclib_nomatch(tagged_mp3)


def test_should_skip_sidecar(tmp_path):
    mp3 = tmp_path / "a.mp3"
    mp3.write_bytes(b"x")
    mp3.with_suffix(".lrc").write_text("[00:00.00]hi\n")
    assert should_skip_lyrics_fetch(mp3) == "sidecar"


@patch("lyrics.MP3")
def test_read_mp3_tags(mock_mp3):
    tags = MagicMock()
    tags.get.side_effect = lambda key: {
        "TIT2": MagicMock(text=["Title"]),
        "TPE1": MagicMock(text=["Artist"]),
        "TALB": MagicMock(text=["Album"]),
    }.get(key)
    mock_mp3.return_value.tags = tags
    assert read_mp3_tags("fake.mp3") == ("Title", "Artist", "Album")


def test_fetch_lrclib_get_hit():
    session = MagicMock()
    session.headers = {}
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"plainLyrics": "ok", "duration": 200}
    session.get.return_value = resp

    result = fetch_lrclib(
        track_name="Song",
        artist_name="Artist",
        album_name="Album",
        duration_sec=200.5,
        session=session,
    )
    assert result["plainLyrics"] == "ok"
    session.get.assert_called_once()


def test_fetch_lrclib_search_fallback():
    session = MagicMock()
    session.headers = {}

    miss = MagicMock()
    miss.status_code = 404

    hit = MagicMock()
    hit.status_code = 200
    hit.json.return_value = [
        {"duration": 500, "plainLyrics": "wrong"},
        {"duration": 181, "plainLyrics": "right"},
    ]

    session.get.side_effect = [miss, hit]

    result = fetch_lrclib(
        track_name="Song",
        artist_name="Artist",
        album_name=None,
        duration_sec=180.0,
        session=session,
    )
    assert result["plainLyrics"] == "right"
    assert session.get.call_count == 2


def test_plain_lyrics_from_lrc_via_helper():
    lrc = "[00:01.00]Hello\n[00:02.00]World\n"
    assert lrc_to_plain(lrc) == "Hello\nWorld"
