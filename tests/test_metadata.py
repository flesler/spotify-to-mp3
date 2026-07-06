"""Tests for ID3 metadata including Spotify/YouTube IDs"""

import subprocess

import pytest
from mutagen.mp3 import MP3


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


def test_set_mp3_metadata_writes_spotify_and_youtube_ids(tagged_mp3):
    from main import _get_txxx, set_mp3_metadata

    track = {
        "id": "spotifyTrack123",
        "name": "Title",
        "artists": "Artist",
        "album": {"name": "Album", "release_date": "2024-01-01"},
    }

    set_mp3_metadata(tagged_mp3, track, youtube_id="youtubeVid456")

    tags = MP3(tagged_mp3).tags
    assert tags is not None
    assert _get_txxx(tags, "SPOTIFY_ID") == "spotifyTrack123"
    assert _get_txxx(tags, "YOUTUBE_ID") == "youtubeVid456"
    assert tags.getall("WOAR")[0].url == "https://open.spotify.com/track/spotifyTrack123"
    assert tags.getall("WOAS")[0].url == "https://www.youtube.com/watch?v=youtubeVid456"


def test_read_youtube_id_deletes_sidecar(tmp_path):
    from main import _read_youtube_id

    info_json = tmp_path / "song.info.json"
    info_json.write_text('{"id": "abc123xyz"}', encoding="utf-8")

    assert _read_youtube_id(info_json) == "abc123xyz"
    assert not info_json.exists()
