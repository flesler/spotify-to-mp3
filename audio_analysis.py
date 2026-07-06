"""Optional local MP3 analysis via Essentia (host only)."""

import os


def analysis_enabled() -> bool:
    if os.environ.get("AUDIO_ANALYSIS", "").lower() in ("0", "false", "no"):
        return False
    try:
        import essentia  # noqa: F401

        return True
    except ImportError:
        return False
