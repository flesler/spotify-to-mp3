"""yt-dlp helpers: JS runtime detection, search fallback, download."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

SEARCH_RESULT_LIMIT = int(os.getenv("YTDLP_SEARCH_LIMIT", "5"))


def ytdlp_cmd() -> list[str]:
    """Run yt-dlp from this Python environment, not a stale system binary on PATH."""
    return [sys.executable, "-m", "yt_dlp"]


def js_runtime_args() -> list[str]:
    """Pass a JS runtime to yt-dlp when available (needed for reliable YouTube extraction)."""
    if shutil.which("node"):
        return ["--js-runtimes", "node"]
    if shutil.which("deno"):
        return ["--js-runtimes", "deno"]
    return []


def ytdlp_base_args(quality: str) -> list[str]:
    return [
        "--extract-audio",
        "--audio-format",
        "mp3",
        "--audio-quality",
        quality,
        "--no-playlist",
        "--no-warnings",
        "--sleep-interval",
        "2",
        "--max-sleep-interval",
        "5",
        "--retries",
        "3",
        "--fragment-retries",
        "3",
        "--abort-on-unavailable-fragment",
        "--no-progress",
        "--write-info-json",
        *js_runtime_args(),
    ]


def ytdlp_error_message(stderr: str) -> str:
    """Pick the most useful line from yt-dlp stderr (skip deprecation noise)."""
    lines = [line.strip() for line in stderr.strip().splitlines() if line.strip()]
    errors = [line for line in lines if line.startswith("ERROR:")]
    if errors:
        return errors[-1].removeprefix("ERROR:").strip()
    for line in lines:
        if "Deprecated Feature" in line or line.startswith("WARNING:"):
            continue
        return line
    useful = [line for line in lines if "Deprecated Feature" not in line and not line.startswith("WARNING:")]
    return useful[-1] if useful else "unknown error"


def is_rate_limited(error_msg: str) -> bool:
    indicators = [
        "No such file or directory",
        "Unable to rename file",
        "HTTP Error 429",
        "Too Many Requests",
        "Sign in to confirm you're not a bot",
    ]
    return any(indicator in error_msg for indicator in indicators)


def search_youtube_videos(query: str, limit: int = SEARCH_RESULT_LIMIT) -> list[tuple[str, str]]:
    """Return [(video_id, title), ...] from YouTube search."""
    cmd = [
        *ytdlp_cmd(),
        f"ytsearch{limit}:{query}",
        "--flat-playlist",
        "--print",
        "%(id)s|||%(title)s",
        "--no-warnings",
        *js_runtime_args(),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        return []

    candidates: list[tuple[str, str]] = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if "|||" not in line:
            continue
        video_id, title = line.split("|||", 1)
        if video_id:
            candidates.append((video_id, title))
    return candidates


def download_youtube_video(video_id: str, output_template: str, quality: str) -> subprocess.CompletedProcess:
    url = f"https://www.youtube.com/watch?v={video_id}"
    cmd = [*ytdlp_cmd(), url, "--output", output_template, *ytdlp_base_args(quality)]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=300)


def read_youtube_id(info_json: Path) -> str | None:
    """Read YouTube video ID from yt-dlp info json, then delete the sidecar file."""
    if not info_json.exists():
        return None
    try:
        data = json.loads(info_json.read_text(encoding="utf-8"))
        youtube_id = data.get("id")
        return youtube_id if isinstance(youtube_id, str) and youtube_id else None
    except Exception:
        return None
    finally:
        try:
            info_json.unlink()
        except OSError:
            pass


def cleanup_partial_downloads(playlist_dir: Path, sanitized_filename: str):
    cleanup_patterns = [f"{sanitized_filename}.*", f"*{sanitized_filename.split(' - ')[-1]}*"]
    extensions = {".part", ".webm", ".m4a", ".tmp", ".f4a", ".opus", ".json"}

    for pattern in cleanup_patterns:
        for partial_file in playlist_dir.glob(pattern):
            if partial_file.suffix in extensions or partial_file.name.endswith(".webm.part"):
                try:
                    partial_file.unlink()
                    print(f"   🗑️  Cleaned: {partial_file.name}")
                except Exception:
                    pass


def download_with_search_fallback(
    search_query: str, playlist_dir: Path, sanitized_filename: str, quality: str
) -> tuple[bool, str | None, str]:
    """Try up to SEARCH_RESULT_LIMIT YouTube search results. Returns (ok, youtube_id, error)."""
    output_template = str(playlist_dir) + "/" + sanitized_filename + ".%(ext)s"
    info_json = playlist_dir / f"{sanitized_filename}.info.json"
    candidates = search_youtube_videos(search_query)

    if not candidates:
        return False, None, "no YouTube search results"

    last_error = "unknown error"
    for i, (video_id, title) in enumerate(candidates, 1):
        if i > 1:
            print(f"   ↪ Trying result {i}/{len(candidates)}: {title}")

        cleanup_partial_downloads(playlist_dir, sanitized_filename)
        result = download_youtube_video(video_id, output_template, quality)

        output_path = playlist_dir / f"{sanitized_filename}.mp3"
        if result.returncode == 0 and output_path.exists():
            youtube_id = read_youtube_id(info_json) or video_id
            return True, youtube_id, ""

        last_error = ytdlp_error_message(result.stderr)
        if is_rate_limited(last_error):
            return False, None, last_error

    cleanup_partial_downloads(playlist_dir, sanitized_filename)
    return False, None, last_error
