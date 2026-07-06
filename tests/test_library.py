"""Tests for library index"""

import subprocess

from library import LibraryIndex, get_txxx
from main import set_mp3_metadata
from mutagen.mp3 import MP3


def test_library_index_finds_by_spotify_id(tmp_path):
    music_dir = tmp_path / "Music"
    music_dir.mkdir()
    mp3 = music_dir / "Artist - Title.mp3"
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
    set_mp3_metadata(
        mp3, {"id": "spotify123", "name": "Title", "artists": "Artist", "album": {"name": "Al", "release_date": "2024"}}
    )

    index = LibraryIndex(music_dir)
    index.build()

    assert index.find_by_spotify_id("spotify123") == mp3.resolve()
    assert "spotify123" in index.spotify_ids()


def test_check_if_track_exists_by_spotify_id(tmp_path):
    music_dir = tmp_path / "Music"
    music_dir.mkdir()
    mp3 = music_dir / "wrong name.mp3"
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
    set_mp3_metadata(
        mp3,
        {"id": "abc", "name": "Real Title", "artists": "Real Artist", "album": {"name": "Al", "release_date": "2024"}},
    )

    index = LibraryIndex(music_dir)
    index.build()

    from main import check_if_track_exists

    result, reason = check_if_track_exists(
        artists="Real Artist", title="Real Title", base_music_dir=music_dir, spotify_id="abc", library_index=index
    )
    assert result.name == "Real Artist - Real Title.mp3"
    assert reason == "spotify id"


def test_library_index_deferred_save(tmp_path):
    music_dir = tmp_path / "Music"
    music_dir.mkdir()
    mp3 = music_dir / "Artist - Title.mp3"
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
    set_mp3_metadata(
        mp3, {"id": "spotify123", "name": "Title", "artists": "Artist", "album": {"name": "Al", "release_date": "2024"}}
    )

    index = LibraryIndex(music_dir)
    index.build()
    index.save()
    mtime = index.cache_file.stat().st_mtime

    index.note_file(mp3, spotify_id="spotify123", youtube_id="yt99")
    assert not index.cache_file.with_suffix(".json.tmp").exists()
    assert index.cache_file.stat().st_mtime == mtime

    index.save()
    assert index.cache_file.stat().st_mtime >= mtime


def test_library_index_skips_save_when_unchanged(tmp_path):
    music_dir = tmp_path / "Music"
    music_dir.mkdir()
    mp3 = music_dir / "Artist - Title.mp3"
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
    set_mp3_metadata(
        mp3, {"id": "spotify123", "name": "Title", "artists": "Artist", "album": {"name": "Al", "release_date": "2024"}}
    )

    index = LibraryIndex(music_dir)
    index.build()
    index.save()
    mtime = index.cache_file.stat().st_mtime

    index2 = LibraryIndex(music_dir)
    index2.build()
    index2.save()
    assert index2.cache_file.stat().st_mtime == mtime


def test_get_txxx_reads_tag(tmp_path):
    music_dir = tmp_path / "Music"
    music_dir.mkdir()
    mp3 = music_dir / "t.mp3"
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
    set_mp3_metadata(
        mp3, {"id": "x", "name": "T", "artists": "A", "album": {"name": "Al", "release_date": "2024"}}, youtube_id="yt1"
    )

    tags = MP3(mp3).tags
    assert get_txxx(tags, "SPOTIFY_ID") == "x"
    assert get_txxx(tags, "YOUTUBE_ID") == "yt1"
