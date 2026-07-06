"""Tests for Spotify API helpers"""

from api import looks_like_playlist_ref


def test_looks_like_playlist_ref_accepts_id():
    assert looks_like_playlist_ref("5W3Iy92MwEwLflZcOhlq9l")


def test_looks_like_playlist_ref_accepts_url():
    assert looks_like_playlist_ref("https://open.spotify.com/playlist/5W3Iy92MwEwLflZcOhlq9l")


def test_looks_like_playlist_ref_rejects_name():
    assert not looks_like_playlist_ref("Infancia")
    assert not looks_like_playlist_ref("Rock en Español")
