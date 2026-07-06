"""Tests for yt-dlp integration helpers"""

from main import _ytdlp_error_message, ytdlp_cmd


def test_ytdlp_cmd_uses_current_python():
    import sys

    assert ytdlp_cmd() == [sys.executable, "-m", "yt_dlp"]


def test_ytdlp_error_message_prefers_error_line():
    stderr = """Deprecated Feature: Support for Python version 3.10 has been deprecated.
WARNING: [youtube] something
ERROR: [youtube] abc123: This video is not available
"""
    assert _ytdlp_error_message(stderr) == "[youtube] abc123: This video is not available"


def test_ytdlp_error_message_skips_deprecation():
    stderr = "Deprecated Feature: Support for Python version 3.10 has been deprecated.\n"
    assert _ytdlp_error_message(stderr) == "unknown error"
