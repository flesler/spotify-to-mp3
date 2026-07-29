"""yt-dlp helpers: JS runtime detection, search fallback, download."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

SEARCH_RESULT_LIMIT = int(os.getenv("YTDLP_SEARCH_LIMIT", "5"))
# Navidrome bar: reject clearly wrong rips (wrong video, live when studio expected, etc.).
# Live cuts / video outros within ~60s are OK. Stricter training filter lives in ace-step.
DEFAULT_DURATION_TOLERANCE_SEC = float(os.getenv("YTDLP_DURATION_TOLERANCE_SEC", "60"))


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


def duration_tolerance_sec() -> float:
    return DEFAULT_DURATION_TOLERANCE_SEC


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


def mp3_duration_sec(path: Path) -> float | None:
    try:
        from mutagen.mp3 import MP3

        return float(MP3(path).info.length)
    except Exception:
        return None


def duration_drift_sec(actual_sec: float | None, target_ms: int | None) -> float | None:
    if actual_sec is None or not target_ms:
        return None
    return abs(actual_sec - target_ms / 1000.0)


def duration_acceptable(path: Path, target_ms: int | None, tolerance_sec: float | None = None) -> bool:
    if not target_ms:
        return True
    tol = duration_tolerance_sec() if tolerance_sec is None else tolerance_sec
    drift = duration_drift_sec(mp3_duration_sec(path), target_ms)
    return drift is None or drift <= tol


def search_youtube_videos(
    query: str, limit: int = SEARCH_RESULT_LIMIT
) -> list[tuple[str, str, float | None]]:
    """Return [(video_id, title, duration_sec), ...] from YouTube search."""
    cmd = [
        *ytdlp_cmd(),
        f"ytsearch{limit}:{query}",
        "--flat-playlist",
        "--print",
        "%(id)s|||%(title)s|||%(duration)s",
        "--no-warnings",
        *js_runtime_args(),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        return []

    candidates: list[tuple[str, str, float | None]] = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if "|||" not in line:
            continue
        parts = line.split("|||", 2)
        if len(parts) < 2:
            continue
        video_id, title = parts[0], parts[1]
        duration: float | None = None
        if len(parts) == 3 and parts[2] not in ("", "NA", "None"):
            try:
                duration = float(parts[2])
            except ValueError:
                duration = None
        if video_id:
            candidates.append((video_id, title, duration))
    return candidates


def rank_candidates_by_duration(
    candidates: list[tuple[str, str, float | None]],
    target_ms: int | None,
) -> list[tuple[str, str, float | None]]:
    if not target_ms:
        return candidates

    target_sec = target_ms / 1000.0
    tol = duration_tolerance_sec()

    def sort_key(item: tuple[str, str, float | None]) -> tuple[int, float]:
        _vid, _title, duration = item
        if duration is None:
            return (1, tol + 1.0)
        return (0, abs(duration - target_sec))

    return sorted(candidates, key=sort_key)


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
    search_query: str,
    playlist_dir: Path,
    sanitized_filename: str,
    quality: str,
    *,
    target_duration_ms: int | None = None,
    tolerance_sec: float | None = None,
) -> tuple[bool, str | None, str]:
    """Try YouTube search results; reject rips that are clearly the wrong song.

    Candidates are ranked by duration proximity to Spotify. After download, reject
    when drift exceeds tolerance (default 60s — wrong video / wrong version).
    ACE-Step training applies a separate stricter filter in the colabs repo.
    """
    output_template = str(playlist_dir) + "/" + sanitized_filename + ".%(ext)s"
    info_json = playlist_dir / f"{sanitized_filename}.info.json"
    candidates = search_youtube_videos(search_query)
    candidates = rank_candidates_by_duration(candidates, target_duration_ms)

    if not candidates:
        return False, None, "no YouTube search results"

    tol = tolerance_sec if tolerance_sec is not None else duration_tolerance_sec()
    last_error = "unknown error"
    for i, (video_id, title, search_duration) in enumerate(candidates, 1):
        if i > 1:
            hint = ""
            if search_duration is not None and target_duration_ms:
                hint = f" ({int(search_duration)}s vs spotify {target_duration_ms // 1000}s)"
            print(f"   ↪ Trying result {i}/{len(candidates)}: {title}{hint}")

        cleanup_partial_downloads(playlist_dir, sanitized_filename)
        result = download_youtube_video(video_id, output_template, quality)

        output_path = playlist_dir / f"{sanitized_filename}.mp3"
        if result.returncode == 0 and output_path.exists():
            drift = duration_drift_sec(mp3_duration_sec(output_path), target_duration_ms)
            if drift is not None and drift > tol:
                print(
                    f"   ✗ Rejected duration drift {drift:.0f}s "
                    f"(spotify {target_duration_ms // 1000}s, got {mp3_duration_sec(output_path):.0f}s)"
                )
                try:
                    output_path.unlink()
                except OSError:
                    pass
                cleanup_partial_downloads(playlist_dir, sanitized_filename)
                last_error = f"duration drift {drift:.0f}s exceeds {tol:.0f}s tolerance"
                continue

            youtube_id = read_youtube_id(info_json) or video_id
            return True, youtube_id, ""

        last_error = ytdlp_error_message(result.stderr)
        if is_rate_limited(last_error):
            return False, None, last_error

    cleanup_partial_downloads(playlist_dir, sanitized_filename)
    return False, None, last_error
