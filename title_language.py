"""Language guess from song title only (not artist).

Forced via playlist-language.txt (--language on sync). Otherwise lingua for Spanish
and Hebrew script in titles.
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from lingua import LanguageDetector

HEBREW_RE = re.compile(r"[\u0590-\u05FF]")
LANG_CODE_RE = re.compile(r"^[a-z]{2,3}$")
PLAYLIST_LANGUAGE_FILE = "playlist-language.txt"

_detector: LanguageDetector | None = None
MIN_ES_CONFIDENCE = 0.5


def normalize_language(code: str) -> str:
    lang = code.strip().lower()
    if not LANG_CODE_RE.match(lang):
        raise ValueError(f"Invalid language code: {code!r}")
    return lang


def playlist_language_path(playlist_dir: Path) -> Path:
    return playlist_dir / PLAYLIST_LANGUAGE_FILE


def load_playlist_language(playlist_dir: Path) -> str | None:
    path = playlist_language_path(playlist_dir)
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return None
    return normalize_language(text)


def save_playlist_language(playlist_dir: Path, code: str) -> Path:
    lang = normalize_language(code)
    path = playlist_language_path(playlist_dir)
    path.write_text(f"{lang}\n", encoding="utf-8")
    return path


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
    playlist_dir: Path | None = None,
    force_language: str | None = None,
    min_frac: float = 0.4,
) -> list[dict[str, float | str | int]]:
    """Count inferred languages across track filename stems."""
    lang = force_language
    if lang is None and playlist_dir is not None:
        lang = load_playlist_language(playlist_dir)
    if lang:
        label = normalize_language(lang)
        n = len(stems)
        if not n:
            return []
        return [{"label": label, "n": n, "frac": 1.0}]

    counts: Counter[str] = Counter()
    for stem in stems:
        detected = infer_title_language(title_from_filename(stem))
        if detected:
            counts[detected] += 1
    if not counts:
        return []
    total = len(stems)
    rows = []
    for label, n in counts.most_common():
        frac = n / total
        if frac >= min_frac:
            rows.append({"label": label, "n": n, "frac": round(frac, 3)})
    return rows
