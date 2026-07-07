"""Language guess from song title only (not artist).

Uses lingua for Spanish. Hebrew script in titles, or playlist overrides (Hebreo/Español).
"""

from __future__ import annotations

import re
from collections import Counter
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from lingua import LanguageDetector

HEBREW_RE = re.compile(r"[\u0590-\u05FF]")

_detector: LanguageDetector | None = None
MIN_ES_CONFIDENCE = 0.5

# Playlist folder name → forced title language (skip per-title inference).
PLAYLIST_TITLE_LANGUAGE: dict[str, str] = {
    "Hebreo": "he",
    "Español": "es",
}


def title_from_filename(stem: str) -> str:
    """Artist - Title.mp3 stem → title part only."""
    if " - " in stem:
        return stem.split(" - ", 1)[1].strip()
    return stem.strip()


def _lingua_detector() -> LanguageDetector:
    global _detector
    if _detector is None:
        from lingua import Language, LanguageDetectorBuilder

        _detector = LanguageDetectorBuilder.from_languages(
            Language.SPANISH,
            Language.HEBREW,
            Language.ENGLISH,
        ).build()
    return _detector


def infer_title_language(title: str) -> str | None:
    """Return ISO-ish code (he, es) or None if unknown."""
    text = title.strip()
    if len(text) < 2:
        return None
    if HEBREW_RE.search(text):
        return "he"

    from lingua import Language

    detector = _lingua_detector()
    if detector.detect_language_of(text) != Language.SPANISH:
        return None
    es_conf = next(
        (c.value for c in detector.compute_language_confidence_values(text) if c.language == Language.SPANISH),
        0.0,
    )
    if es_conf >= MIN_ES_CONFIDENCE:
        return "es"
    return None


def title_language_signature(
    stems: list[str],
    *,
    playlist: str | None = None,
    min_frac: float = 0.4,
) -> list[dict[str, float | str | int]]:
    """Count inferred languages across track filename stems."""
    if playlist and playlist in PLAYLIST_TITLE_LANGUAGE:
        label = PLAYLIST_TITLE_LANGUAGE[playlist]
        n = len(stems)
        if not n:
            return []
        return [{"label": label, "n": n, "frac": 1.0}]

    counts: Counter[str] = Counter()
    for stem in stems:
        lang = infer_title_language(title_from_filename(stem))
        if lang:
            counts[lang] += 1
    if not counts:
        return []
    total = len(stems)
    rows = []
    for label, n in counts.most_common():
        frac = n / total
        if frac >= min_frac:
            rows.append({"label": label, "n": n, "frac": round(frac, 3)})
    return rows
