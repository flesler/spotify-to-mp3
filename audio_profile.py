"""Playlist vibe profiling from Essentia sidecars + MP3 metadata."""

from __future__ import annotations

import json
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import yaml
from mutagen import File

from audio_analysis import (
    ANALYSIS_VERSION,
    BINARY_HEADS,
    CLASSIFIER_POSITIVE_CLASS,
    MODELS_DIR,
    load_cache,
)
from title_language import title_language_signature

PROFILE_FILENAME = ".playlist-profile.json"
PROFILE_VERSION = 2
STD_FLOOR = 0.05
VIOLATION_Z = 2.0

DEAM_KEYS = ("valence_mean", "arousal_mean", "arousal_peak", "arousal_ramp")
FEATURE_KEYS = ("bpm", "loudness", "dynamic_complexity")
MOOD_STEMS = frozenset({"mood_happy", "mood_sad", "mood_relaxed", "mood_aggressive", "mood_party", "mood_electronic", "mood_acoustic"})
NON_MOOD_STEMS = tuple(s for s in BINARY_HEADS if s not in MOOD_STEMS)

PROFILE_SECTIONS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("classifiers", BINARY_HEADS),
    ("deam", DEAM_KEYS),
    ("features", FEATURE_KEYS),
)

_library_cache: tuple[str, dict[str, dict[str, float]]] | None = None
_classifier_classes: dict[str, tuple[str, str]] | None = None


@dataclass
class TrackRow:
    name: str
    data: dict[str, Any]
    year: int | None = None


def _load_classifier_classes() -> dict[str, tuple[str, str]]:
    global _classifier_classes
    if _classifier_classes is not None:
        return _classifier_classes

    classes: dict[str, tuple[str, str]] = {}
    for stem in BINARY_HEADS:
        path = MODELS_DIR / f"{stem}-msd-musicnn-1.json"
        if not path.exists():
            continue
        labels = json.loads(path.read_text()).get("classes") or []
        if len(labels) != 2:
            continue
        positive = CLASSIFIER_POSITIVE_CLASS.get(stem, labels[-1])
        negative = labels[1] if labels[0] == positive else labels[0]
        classes[stem] = (negative, positive)
    _classifier_classes = classes
    return classes


def year_from_mp3(mp3: Path) -> int | None:
    try:
        from mutagen.id3 import ID3

        try:
            tags = ID3(mp3)
        except Exception:
            tags = None
        if tags:
            for key in ("TDRC", "TYER"):
                if key in tags:
                    text = str(tags[key])
                    digits = "".join(c for c in text if c.isdigit())
                    if len(digits) >= 4:
                        return int(digits[:4])

        audio = File(mp3)
        if audio is not None and audio.tags:
            for key in ("TDRC", "TYER", "DATE"):
                if key in audio.tags:
                    raw = audio.tags[key]
                    text = str(raw.text[0] if hasattr(raw, "text") else raw)
                    digits = "".join(c for c in text if c.isdigit())
                    if len(digits) >= 4:
                        return int(digits[:4])
    except Exception:
        return None
    return None


def signature_phrase(stem: str, mean: float) -> str:
    pair = _load_classifier_classes().get(stem)
    if not pair:
        positive = CLASSIFIER_POSITIVE_CLASS.get(stem, stem)
        return f"high {positive}" if mean >= 0.5 else f"low {positive}"

    negative, positive = pair
    if stem == "voice_instrumental":
        return "instrumental" if mean < 0.5 else "vocal"
    if stem == "gender":
        return "female" if mean >= 0.5 else "male"
    return positive if mean >= 0.5 else negative


def load_playlist_tracks(playlist_dir: Path) -> list[TrackRow]:
    rows: list[TrackRow] = []
    for path in sorted(playlist_dir.glob("*.audio.yaml")):
        data = yaml.safe_load(path.read_text())
        if not isinstance(data, dict) or data.get("analysis_version") != ANALYSIS_VERSION:
            continue
        name = path.name.removesuffix(".audio.yaml")
        mp3 = playlist_dir / f"{name}.mp3"
        rows.append(TrackRow(name, data, year_from_mp3(mp3) if mp3.exists() else None))
    return rows


def flatten_numeric(data: dict[str, Any]) -> dict[str, float]:
    out: dict[str, float] = {}
    for section, keys in PROFILE_SECTIONS:
        block = data.get(section) or {}
        if not isinstance(block, dict):
            continue
        for key in keys:
            value = block.get(key)
            if isinstance(value, (int, float)):
                out[f"{section}.{key}"] = float(value)
    return out


def feature_matrix(rows: list[TrackRow]) -> tuple[list[str], np.ndarray]:
    if not rows:
        return [], np.empty((0, 0))
    names = sorted({key for row in rows for key in flatten_numeric(row.data)})
    matrix = np.array([[flatten_numeric(row.data).get(name, np.nan) for name in names] for row in rows])
    return names, matrix


def summarize_features(names: list[str], matrix: np.ndarray) -> dict[str, dict[str, float]]:
    summary: dict[str, dict[str, float]] = {}
    for i, name in enumerate(names):
        clean = matrix[:, i][~np.isnan(matrix[:, i])]
        if len(clean) == 0:
            continue
        summary[name] = {
            "mean": float(np.mean(clean)),
            "std": float(np.std(clean)),
            "min": float(np.min(clean)),
            "max": float(np.max(clean)),
        }
    return summary


def library_fingerprint(music_dir: Path) -> dict[str, dict[str, float]]:
    global _library_cache
    key = str(music_dir.resolve())
    if _library_cache and _library_cache[0] == key:
        return _library_cache[1]

    buckets: dict[str, list[float]] = defaultdict(list)
    for path in music_dir.glob("*/*.audio.yaml"):
        try:
            data = yaml.safe_load(path.read_text())
        except (OSError, yaml.YAMLError):
            continue
        if not isinstance(data, dict) or data.get("analysis_version") != ANALYSIS_VERSION:
            continue
        for feat, value in flatten_numeric(data).items():
            buckets[feat].append(value)

    stats: dict[str, dict[str, float]] = {}
    for feat, values in buckets.items():
        arr = np.array(values)
        stats[feat] = {"mean": float(arr.mean()), "std": float(arr.std()), "n": len(arr)}
    _library_cache = (key, stats)
    return stats


def decade_counts(rows: list[TrackRow]) -> list[tuple[str, int]]:
    counts: Counter[str] = Counter()
    for row in rows:
        if row.year:
            counts[f"{row.year // 10 * 10}s"] += 1
    return counts.most_common()


def year_stats(rows: list[TrackRow]) -> dict[str, float | int]:
    years = [row.year for row in rows if row.year]
    if not years:
        return {"n": 0}
    arr = np.array(years, dtype=float)
    return {
        "n": len(years),
        "mean": float(arr.mean()),
        "min": int(arr.min()),
        "max": int(arr.max()),
    }


def tag_signature(rows: list[TrackRow], *, min_frac: float = 0.4) -> list[dict[str, float | str | int]]:
    n = len(rows)
    if n == 0:
        return []

    presence: Counter[str] = Counter()
    scores: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        seen: set[str] = set()
        for tag in row.data.get("tags") or []:
            if not isinstance(tag, dict) or not tag.get("label"):
                continue
            label = str(tag["label"])
            scores[label].append(float(tag.get("score") or 0))
            if label not in seen:
                presence[label] += 1
                seen.add(label)

    out: list[dict[str, float | str | int]] = []
    for label, count in presence.most_common():
        frac = count / n
        if frac < min_frac:
            continue
        out.append(
            {
                "label": label,
                "count": count,
                "frac": round(frac, 2),
                "avg_score": round(float(np.mean(scores[label])), 3),
            }
        )
    return out


def classifier_traits(
    summary: dict[str, dict[str, float]],
    *,
    min_distance: float = 0.2,
    max_std: float = 0.25,
    mood_low: float = 0.35,
    mood_high: float = 0.65,
) -> list[dict[str, float | str]]:
    traits: list[dict[str, float | str]] = []

    mood_candidates: list[tuple[str, float, float]] = []
    for stem in MOOD_STEMS:
        key = f"classifiers.{stem}"
        stats = summary.get(key)
        if not stats:
            continue
        mean = float(stats["mean"])
        std = float(stats["std"])
        if std > max_std:
            continue
        if mean >= mood_high:
            mood_candidates.append((stem, mean, abs(mean - 0.5)))
        elif mean <= mood_low:
            mood_candidates.append((stem, mean, abs(mean - 0.5)))

    mood_candidates.sort(key=lambda item: -item[2])
    for stem, mean, distance in mood_candidates[:3]:
        traits.append(_trait_row(stem, mean, summary[f"classifiers.{stem}"]["std"], distance, kind="mood"))

    for stem in NON_MOOD_STEMS:
        key = f"classifiers.{stem}"
        stats = summary.get(key)
        if not stats:
            continue
        mean = float(stats["mean"])
        std = float(stats["std"])
        distance = abs(mean - 0.5)
        if distance < min_distance or std > max_std:
            continue
        traits.append(_trait_row(stem, mean, std, distance, kind="classifier"))

    traits.sort(key=lambda row: -float(row["score"]))
    return traits


def _trait_row(stem: str, mean: float, std: float, distance: float, *, kind: str) -> dict[str, float | str]:
    direction = "high" if mean >= 0.5 else "low"
    return {
        "kind": kind,
        "stem": stem,
        "phrase": signature_phrase(stem, mean),
        "direction": direction,
        "mean": round(mean, 3),
        "std": round(std, 3),
        "distance": round(distance, 3),
        "score": round(distance * (1.0 - min(std / 0.5, 1.0)), 3),
    }


def library_traits(
    summary: dict[str, dict[str, float]],
    library: dict[str, dict[str, float]],
    *,
    min_z: float = 1.0,
    max_std: float = 0.3,
) -> list[dict[str, float | str]]:
    traits: list[dict[str, float | str]] = []
    for key, stats in summary.items():
        lib = library.get(key)
        if not lib or lib.get("std", 0) < 1e-6:
            continue
        std = float(stats["std"])
        if std > max_std:
            continue
        z = (float(stats["mean"]) - float(lib["mean"])) / float(lib["std"])
        if abs(z) < min_z:
            continue
        label = key.replace("classifiers.", "").replace("features.", "").replace("deam.", "deam ")
        traits.append(
            {
                "key": key,
                "phrase": label,
                "z": round(z, 2),
                "mean": round(stats["mean"], 3),
                "library_mean": round(lib["mean"], 3),
                "std": round(std, 3),
            }
        )
    traits.sort(key=lambda row: -abs(float(row["z"])))
    return traits


def embedding_centroid(rows: list[TrackRow]) -> list[float] | None:
    vectors = [row.data["embedding_mean"] for row in rows if row.data.get("embedding_mean")]
    if not vectors:
        return None
    return [round(float(v), 5) for v in np.array(vectors, dtype=float).mean(axis=0)]


def profile_path(playlist_dir: Path) -> Path:
    return playlist_dir / PROFILE_FILENAME


def save_playlist_profile(playlist_dir: Path, profile: dict[str, Any]) -> Path:
    path = profile_path(playlist_dir)
    path.write_text(json.dumps(profile, indent=2))
    return path


def load_playlist_profile(playlist_dir: Path) -> dict[str, Any] | None:
    path = profile_path(playlist_dir)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or data.get("profile_version") != PROFILE_VERSION:
        return None
    return data


def list_playlist_dirs(music_dir: Path) -> list[Path]:
    return sorted(p for p in music_dir.iterdir() if p.is_dir() and not p.name.startswith("."))


def load_cached_profiles(
    music_dir: Path,
    *,
    playlists: list[str] | None = None,
    refresh_missing: bool = False,
) -> list[dict[str, Any]]:
    dirs = list_playlist_dirs(music_dir)
    if playlists:
        wanted = set(playlists)
        dirs = [d for d in dirs if d.name in wanted]

    profiles: list[dict[str, Any]] = []
    for folder in dirs:
        cached = load_playlist_profile(folder)
        if cached is None and refresh_missing:
            cached = profile_playlist(folder, music_dir)
            save_playlist_profile(folder, cached)
        if cached is not None:
            profiles.append(cached)
    return profiles


def resolve_track_mp3(music_dir: Path, query: str) -> Path | None:
    raw = query.strip()
    if not raw:
        return None

    candidate = Path(raw)
    if candidate.is_file():
        return candidate.resolve()
    if not candidate.is_absolute():
        for base in (Path.cwd(), music_dir):
            direct = base / raw
            if direct.is_file():
                return direct.resolve()
            if not raw.lower().endswith(".mp3"):
                with_mp3 = base / f"{raw}.mp3"
                if with_mp3.is_file():
                    return with_mp3.resolve()

    needle = raw.lower().removesuffix(".mp3")
    matches = [p for p in music_dir.glob("*/*.mp3") if needle in p.stem.lower()]
    if not matches:
        return None
    exact = [p for p in matches if p.stem.lower() == needle]
    if exact:
        return sorted(exact, key=str)[0].resolve()
    if len(matches) == 1:
        return matches[0].resolve()
    return None


def load_track_features(mp3: Path) -> dict[str, Any]:
    data = load_cache(mp3)
    if not data or data.get("analysis_version") != ANALYSIS_VERSION:
        raise RuntimeError(f"No v3 audio sidecar for {mp3.name} — run analyze-audio first")

    tags = [str(t["label"]) for t in (data.get("tags") or []) if isinstance(t, dict) and t.get("label")]
    return {
        "name": mp3.stem,
        "path": str(mp3),
        "features": flatten_numeric(data),
        "tags": tags,
        "embedding": data.get("embedding_mean"),
        "classifiers": data.get("classifiers") or {},
    }


def stable_features(
    summary: dict[str, dict[str, float]],
    *,
    classifier_max_std: float = 0.25,
    classifier_min_distance: float = 0.2,
    signal_max_cv: float = 0.20,
) -> list[dict[str, Any]]:
    """Features consistent within a playlist — only these define its fingerprint for matching."""
    stable: list[dict[str, Any]] = []
    for key, stats in summary.items():
        mean = float(stats["mean"])
        std = float(stats["std"])
        if key.startswith("classifiers."):
            stem = key.removeprefix("classifiers.")
            if std > classifier_max_std or abs(mean - 0.5) < classifier_min_distance:
                continue
            stable.append(
                {
                    "key": key,
                    "mean": round(mean, 3),
                    "std": round(std, 3),
                    "label": signature_phrase(stem, mean),
                }
            )
            continue

        cv = std / max(abs(mean), 0.5)
        if cv > signal_max_cv:
            continue
        label = key.split(".", 1)[-1]
        stable.append({"key": key, "mean": round(mean, 3), "std": round(std, 3), "cv": round(cv, 3), "label": label})

    stable.sort(
        key=lambda row: (
            -abs(float(row["mean"]) - 0.5) if str(row["key"]).startswith("classifiers.") else -1 / float(row.get("cv") or 1)
        )
    )
    return stable


def _geo_mean(values: list[float]) -> float:
    if not values:
        return 0.0
    arr = np.clip(np.array(values, dtype=float), 1e-9, 1.0)
    return float(np.exp(np.mean(np.log(arr))))


def _feature_fit(
    song: dict[str, float],
    fingerprint: dict[str, dict[str, float]],
    stable_keys: set[str] | None = None,
    *,
    stable_labels: dict[str, str] | None = None,
) -> tuple[float, list[dict[str, float | str]], list[dict[str, float | str]], int]:
    overlaps = sorted(set(song) & set(fingerprint))
    if stable_keys is not None:
        overlaps = [k for k in overlaps if k in stable_keys]
    if not overlaps:
        return 0.0, [], [], 0

    classifier_fits: list[float] = []
    signal_fits: list[float] = []
    details: list[dict[str, float | str]] = []
    violations: list[dict[str, float | str]] = []
    labels = stable_labels or {}

    for key in overlaps:
        mean = float(fingerprint[key]["mean"])
        std = max(float(fingerprint[key]["std"]), STD_FLOOR)
        value = float(song[key])
        z = (value - mean) / std
        fit = float(np.exp(-0.5 * z * z))
        if key.startswith("classifiers."):
            classifier_fits.append(fit)
            if abs(z) >= VIOLATION_Z:
                violations.append(
                    {
                        "key": key,
                        "label": labels.get(key, key.split(".", 1)[-1]),
                        "value": round(value, 3),
                        "mean": round(mean, 3),
                        "z": round(z, 2),
                    }
                )
        else:
            signal_fits.append(fit)
        if abs(z) >= 1.0:
            details.append({"key": key, "value": round(value, 3), "mean": round(mean, 3), "z": round(z, 2)})

    parts: list[float] = []
    if classifier_fits:
        parts.append(_geo_mean(classifier_fits))
    if signal_fits:
        parts.append(float(np.mean(signal_fits)))
    score = float(np.mean(parts)) if parts else 0.0

    violations.sort(key=lambda row: -abs(float(row["z"])))
    details.sort(key=lambda row: -abs(float(row["z"])))
    return score, details[:6], violations[:4], len(overlaps)


def _tag_fit(song_tags: list[str], profile_tags: list[dict[str, Any]]) -> float:
    if not song_tags or not profile_tags:
        return 0.0
    signature = {str(row["label"]) for row in profile_tags}
    hits = sum(1 for tag in song_tags[:10] if tag in signature)
    return hits / min(len(song_tags[:10]), len(signature))


def _timbre_fit(song_embedding: list[float] | None, centroid: list[float] | None) -> float | None:
    if not song_embedding or not centroid:
        return None
    a = np.array(song_embedding, dtype=float)
    b = np.array(centroid, dtype=float)
    a = a / np.clip(np.linalg.norm(a), 1e-9, None)
    b = b / np.clip(np.linalg.norm(b), 1e-9, None)
    return float(np.clip(a @ b, -1.0, 1.0))


def match_track_to_playlists(
    track: dict[str, Any],
    profiles: list[dict[str, Any]],
    *,
    min_score: float = 0.0,
) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    for profile in profiles:
        stable_rows = profile.get("stable_features") or []
        stable_keys = {str(row["key"]) for row in stable_rows}
        stable_labels = {str(row["key"]): str(row["label"]) for row in stable_rows}
        feature_score, mismatches, violations, n_stable = _feature_fit(
            track["features"],
            profile.get("fingerprint") or {},
            stable_keys,
            stable_labels=stable_labels,
        )
        tag_score = _tag_fit(track.get("tags") or [], profile.get("tags") or [])
        timbre = _timbre_fit(track.get("embedding"), profile.get("embedding_centroid"))

        if n_stable == 0:
            if timbre is None:
                score = tag_score
            else:
                score = 0.6 * timbre + 0.4 * tag_score
        elif timbre is None:
            score = 0.70 * feature_score + 0.30 * tag_score
        else:
            score = 0.55 * feature_score + 0.30 * timbre + 0.15 * tag_score

        if score < min_score:
            continue
        matches.append(
            {
                "playlist": profile["playlist"],
                "score": round(score, 3),
                "feature_fit": round(feature_score, 3),
                "stable_dims": n_stable,
                "timbre": round(timbre, 3) if timbre is not None else None,
                "tag_fit": round(tag_score, 3),
                "mismatches": mismatches,
                "violations": violations,
                "track_count": profile.get("track_count", 0),
            }
        )

    matches.sort(key=lambda row: -float(row["score"]))
    return matches


def embedding_cohesion(rows: list[TrackRow]) -> float | None:
    vectors = [row.data["embedding_mean"] for row in rows if row.data.get("embedding_mean")]
    if len(vectors) < 2:
        return None
    arr = _normalize_rows(np.array(vectors, dtype=float))
    sim = arr @ arr.T
    return float(sim[np.triu_indices(len(vectors), k=1)].mean())


def _normalize_rows(arr: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(arr, axis=1, keepdims=True)
    return arr / np.clip(norms, 1e-9, None)


def embedding_outliers(rows: list[TrackRow], *, top_k: int = 4) -> list[dict[str, float | str]]:
    vectors = []
    names = []
    for row in rows:
        emb = row.data.get("embedding_mean")
        if emb:
            vectors.append(emb)
            names.append(row.name)
    if len(vectors) < 3:
        return []

    arr = _normalize_rows(np.array(vectors, dtype=float))
    centroid = arr.mean(axis=0)
    centroid = centroid / np.linalg.norm(centroid)
    sims = arr @ centroid
    ranked = sorted(zip(names, sims), key=lambda item: item[1])
    out = []
    for name, sim in ranked[:top_k]:
        out.append({"name": name, "similarity": round(float(sim), 3)})
    return out


def profile_playlist(
    playlist_dir: Path,
    music_dir: Path | None = None,
    *,
    min_distance: float = 0.2,
    max_std: float = 0.25,
    min_z: float = 1.0,
    tag_min_frac: float = 0.4,
    force_language: str | None = None,
) -> dict[str, Any]:
    rows = load_playlist_tracks(playlist_dir)
    names, matrix = feature_matrix(rows)
    summary = summarize_features(names, matrix)
    library = library_fingerprint(music_dir) if music_dir else {}

    traits = classifier_traits(summary, min_distance=min_distance, max_std=max_std)
    stable = stable_features(
        summary,
        classifier_max_std=max_std,
        classifier_min_distance=min_distance,
    )
    vs_library = library_traits(summary, library, min_z=min_z, max_std=max_std + 0.05) if library else []
    tags = tag_signature(rows, min_frac=tag_min_frac)
    outliers = embedding_outliers(rows)
    title_langs = title_language_signature(
        [row.name for row in rows],
        playlist_dir=playlist_dir,
        force_language=force_language,
        min_frac=tag_min_frac,
    )

    return {
        "profile_version": PROFILE_VERSION,
        "generated_at": time.time(),
        "playlist": playlist_dir.name,
        "track_count": len(rows),
        "years": year_stats(rows),
        "decades": decade_counts(rows),
        "embedding_cohesion": embedding_cohesion(rows),
        "embedding_centroid": embedding_centroid(rows),
        "fingerprint": summary,
        "traits": traits,
        "stable_features": stable,
        "vs_library": vs_library,
        "tags": tags,
        "title_languages": title_langs,
        "genres_dortmund": _genre_counts(rows),
        "outliers": outliers,
    }


def _genre_counts(rows: list[TrackRow], head: str = "genre_dortmund", *, top_k: int = 8) -> list[tuple[str, int]]:
    counts: Counter[str] = Counter()
    for row in rows:
        genres = (row.data.get("genres") or {}).get(head) or []
        if genres and isinstance(genres[0], dict) and genres[0].get("label"):
            counts[str(genres[0]["label"])] += 1
    return counts.most_common(top_k)


def compare_playlists(profiles: list[dict[str, Any]], keys: tuple[str, ...]) -> dict[str, dict[str, float]]:
    table: dict[str, dict[str, float]] = {}
    for key in keys:
        table[key] = {}
        for profile in profiles:
            mean = profile.get("fingerprint", {}).get(key, {}).get("mean")
            if mean is not None:
                table[key][profile["playlist"]] = round(mean, 3)
    return table
