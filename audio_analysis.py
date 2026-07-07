"""Local MP3 analysis via Essentia (host only). Writes .audio.yaml sidecars."""

from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path

import yaml

from library import LibraryIndex, link_track

SIDECAR_SUFFIX = ".audio.yaml"
SAMPLE_RATE = 16000
MODELS_DIR = Path(__file__).resolve().parent / "models" / "essentia"
ANALYSIS_VERSION = 3
MSD_TAG_TOP_K = 10
GENRE_TOP_K = 3

BINARY_HEADS = (
    "mood_happy",
    "mood_electronic",
    "mood_relaxed",
    "mood_aggressive",
    "mood_sad",
    "mood_party",
    "mood_acoustic",
    "gender",
    "danceability",
    "voice_instrumental",
    "tonal_atonal",
)

GENRE_HEADS = ("genre_dortmund", "genre_rosamerica", "genre_tzanetakis")

# Flat classifiers[key] = P(positive class). 1.0 - value is the complement (e.g. gender 0.19 → 81% male).
CLASSIFIER_POSITIVE_CLASS: dict[str, str] = {
    "mood_happy": "happy",
    "mood_electronic": "electronic",
    "mood_relaxed": "relaxed",
    "mood_aggressive": "aggressive",
    "mood_sad": "sad",
    "mood_party": "party",
    "mood_acoustic": "acoustic",
    "gender": "female",  # 0.0 = male, 1.0 = female
    "danceability": "danceable",
    "voice_instrumental": "voice",  # 0.0 = instrumental, 1.0 = voice
    "tonal_atonal": "tonal",  # 0.0 = atonal, 1.0 = tonal
}

_models: "_EssentiaModels | None" = None


def essentia_use_cpu() -> bool:
    flag = os.environ.get("ESSENTIA_CPU", "0").lower()
    return flag in ("1", "true", "yes")


def _apply_compute_env() -> None:
    os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "2")
    if essentia_use_cpu():
        os.environ.setdefault("CUDA_VISIBLE_DEVICES", "-1")
        return
    try:
        import tensorflow as tf

        for gpu in tf.config.list_physical_devices("GPU"):
            tf.config.experimental.set_memory_growth(gpu, True)
    except Exception:
        pass


def _silence_essentia_logs() -> None:
    import essentia

    essentia.log.warningActive = False
    essentia.log.infoActive = False


_apply_compute_env()


def _head_pb(stem: str) -> Path:
    return MODELS_DIR / f"{stem}-msd-musicnn-1.pb"


def _head_json(stem: str) -> Path:
    return MODELS_DIR / f"{stem}-msd-musicnn-1.json"


def _load_msd_tag_classes() -> list[str]:
    path = MODELS_DIR / "msd-musicnn-1.json"
    if not path.exists():
        raise RuntimeError(f"Missing {path.name} — run scripts/download-essentia-models.sh")
    classes = json.loads(path.read_text()).get("classes")
    if not classes:
        raise RuntimeError(f"No classes in {path.name}")
    return list(classes)


def _load_head_classes(stem: str) -> list[str]:
    path = _head_json(stem)
    if not path.exists():
        raise RuntimeError(f"Missing model metadata {path.name} — run scripts/download-essentia-models.sh")
    data = json.loads(path.read_text())
    classes = data.get("classes")
    if not classes:
        raise RuntimeError(f"No classes in {path.name}")
    return list(classes)


class _EssentiaModels:
    """MusiCNN embedder + classification heads (see docs/essentia.md)."""

    def __init__(self) -> None:
        _silence_essentia_logs()
        from essentia.standard import (  # pyright: ignore[reportAttributeAccessIssue]
            DynamicComplexity,
            KeyExtractor,
            Loudness,
            MonoLoader,
            PercivalBpmEstimator,
            TensorflowPredict2D,
            TensorflowPredictMusiCNN,
        )

        self.mono_loader = MonoLoader
        self.bpm_estimator = PercivalBpmEstimator()
        self.key_extractor = KeyExtractor()
        self.dynamic_complexity = DynamicComplexity()
        self.loudness = Loudness()

        embed_pb = str(MODELS_DIR / "msd-musicnn-1.pb")
        # batchSize=0 → one TF session per track (batched patches), not per patch
        self.embed = TensorflowPredictMusiCNN(
            graphFilename=embed_pb, output="model/dense/BiasAdd", batchSize=0
        )
        self.msd_tags = TensorflowPredictMusiCNN(graphFilename=embed_pb, output="model/Sigmoid", batchSize=0)
        self.deam = TensorflowPredict2D(
            graphFilename=str(MODELS_DIR / "deam-msd-musicnn-2.pb"), output="model/Identity"
        )
        self.heads: dict[str, object] = {}
        self.labels: dict[str, list[str]] = {"msd_tags": _load_msd_tag_classes()}
        for stem in (*BINARY_HEADS, *GENRE_HEADS):
            self.labels[stem] = _load_head_classes(stem)
            self.heads[stem] = TensorflowPredict2D(graphFilename=str(_head_pb(stem)), output="model/Softmax")
        self.head_count = len(self.heads)

    def load_audio(self, mp3: Path):
        return self.mono_loader(filename=str(mp3), sampleRate=SAMPLE_RATE, resampleQuality=4)()

    def classical_features(self, audio) -> dict:
        bpm = self.bpm_estimator(audio)
        key, scale, key_strength = self.key_extractor(audio)
        dynamic, _ = self.dynamic_complexity(audio)
        loudness = self.loudness(audio)
        return {
            "bpm": round(float(bpm), 1),
            "key": str(key),
            "scale": str(scale),
            "key_strength": round(float(key_strength), 3),
            "loudness": round(float(loudness), 2),
            "dynamic_complexity": round(float(dynamic), 3),
        }

    @classmethod
    def get(cls) -> "_EssentiaModels":
        global _models
        if _models is None:
            _models = cls()
        return _models


def _get_models() -> _EssentiaModels:
    if not MODELS_DIR.joinpath("msd-musicnn-1.pb").exists():
        raise RuntimeError(f"Essentia models missing in {MODELS_DIR} — run scripts/download-essentia-models.sh")
    return _EssentiaModels.get()


def analysis_enabled() -> bool:
    flag = os.environ.get("AUDIO_ANALYSIS", "0").lower()
    if flag in ("0", "false", "no", ""):
        return False
    try:
        import essentia  # noqa: F401

        return True
    except ImportError:
        return False


def sidecar_path(mp3: Path) -> Path:
    return mp3.with_suffix(SIDECAR_SUFFIX)


def cache_is_fresh(existing: dict | None) -> bool:
    return existing is not None and existing.get("analysis_version") == ANALYSIS_VERSION


def load_cache(mp3: Path) -> dict | None:
    path = sidecar_path(mp3)
    if not path.exists():
        return None
    try:
        return yaml.safe_load(path.read_text())
    except (OSError, yaml.YAMLError):
        return None


def save_cache(mp3: Path, data: dict) -> Path:
    path = sidecar_path(mp3)
    path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True))
    return path


def _head_scores(predictions, labels: list[str]) -> dict[str, float]:
    import numpy as np

    arr = np.array(predictions)
    if arr.ndim == 1:
        arr = arr.reshape(1, -1)
    means = arr.mean(axis=0)
    return {labels[i]: float(means[i]) for i in range(min(len(labels), len(means)))}


def _positive_label(stem: str, labels: list[str]) -> str:
    if stem in CLASSIFIER_POSITIVE_CLASS:
        return CLASSIFIER_POSITIVE_CLASS[stem]
    if stem.startswith("mood_"):
        return stem.removeprefix("mood_")
    return labels[0]


def _binary_positive_score(predictions, stem: str, labels: list[str]) -> float:
    scores = _head_scores(predictions, labels)
    return round(scores[_positive_label(stem, labels)], 4)


def _top_k(scores: dict[str, float], k: int) -> list[dict[str, float | str]]:
    ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)[:k]
    return [{"label": label, "score": round(score, 4)} for label, score in ranked]


def _arc_profile(deam) -> dict[str, float]:
    import numpy as np

    arr = np.array(deam)
    valence = arr[:, 0]
    arousal = arr[:, 1]
    quarter = max(1, len(arr) // 4)
    start_a = float(arousal[:quarter].mean())
    end_a = float(arousal[-quarter:].mean())
    peak_a = float(arousal.max())
    return {
        "valence_mean": float(valence.mean()),
        "arousal_mean": float(arousal.mean()),
        "arousal_start": start_a,
        "arousal_end": end_a,
        "arousal_peak": peak_a,
        "arousal_ramp": end_a - start_a,
    }


def _downsample_series(values, labels: tuple[str, str], max_points: int = 40) -> list[dict]:
    import numpy as np

    arr = np.array(values)
    if len(arr) <= max_points:
        step = 1
    else:
        step = len(arr) // max_points
    out = []
    for i in range(0, len(arr), step):
        row = arr[i]
        out.append({"t": i, labels[0]: float(row[0]), labels[1]: float(row[1])})
    return out


def _mean_embedding(embeddings) -> list[float]:
    import numpy as np

    arr = np.array(embeddings)
    mean = arr.mean(axis=0)
    return [round(float(v), 5) for v in mean]


def analyze_mp3(mp3: Path, *, include_series: bool = True) -> dict:
    if not analysis_enabled():
        raise RuntimeError("Audio analysis disabled (set AUDIO_ANALYSIS=1 and install requirements-analysis.txt)")

    models = _get_models()
    mp3 = Path(mp3)
    audio = models.load_audio(mp3)
    embeddings = models.embed(audio)
    deam = models.deam(embeddings)

    classifiers: dict[str, float] = {}
    for stem in BINARY_HEADS:
        preds = models.heads[stem](embeddings)
        classifiers[stem] = _binary_positive_score(preds, stem, models.labels[stem])

    genres: dict[str, list[dict[str, float | str]]] = {}
    for stem in GENRE_HEADS:
        scores = _head_scores(models.heads[stem](embeddings), models.labels[stem])
        genres[stem] = _top_k(scores, GENRE_TOP_K)

    msd_scores = _head_scores(models.msd_tags(audio), models.labels["msd_tags"])

    payload: dict = {
        "analysis_version": ANALYSIS_VERSION,
        "fetched_at": time.time(),
        "source": str(mp3),
        "duration_sec": round(len(audio) / SAMPLE_RATE, 1),
        "features": models.classical_features(audio),
        "deam": _arc_profile(deam),
        "classifiers": classifiers,
        "genres": genres,
        "tags": _top_k(msd_scores, MSD_TAG_TOP_K),
        "embedding_mean": _mean_embedding(embeddings),
    }
    if include_series:
        payload["deam_series"] = _downsample_series(deam, ("valence", "arousal"))
    return payload


def cache_for_mp3(mp3: Path, *, force: bool = False, include_series: bool = True) -> dict | None:
    if not analysis_enabled():
        return None

    mp3 = Path(mp3)
    if not mp3.exists():
        return None

    existing = load_cache(mp3)
    if cache_is_fresh(existing) and not force:
        return existing

    payload = analyze_mp3(mp3, include_series=include_series)
    save_cache(mp3, payload)
    return payload


def _same_inode(a: Path, b: Path) -> bool:
    try:
        return a.resolve().stat().st_ino == b.resolve().stat().st_ino
    except OSError:
        return False


def propagate_sidecar(primary_mp3: Path, other_mp3s: list[Path]) -> int:
    """Hard link (or copy) sidecar from primary to sibling MP3 paths. Returns links created."""
    primary_mp3 = primary_mp3.resolve()
    primary_sc = sidecar_path(primary_mp3)
    if not primary_sc.exists():
        return 0

    linked = 0
    for mp3 in other_mp3s:
        mp3 = Path(mp3).resolve()
        if mp3 == primary_mp3:
            continue
        target_sc = sidecar_path(mp3)
        if target_sc.exists() and _same_inode(primary_sc, target_sc):
            continue
        target_sc.parent.mkdir(parents=True, exist_ok=True)
        if target_sc.exists():
            target_sc.unlink()
        try:
            link_track(primary_sc, target_sc)
        except OSError:
            shutil.copy2(primary_sc, target_sc)
        linked += 1
    return linked


def iter_track_groups(index: LibraryIndex, *, playlist: str | None = None) -> list[tuple[Path, list[Path]]]:
    """Unique tracks by inode: (primary_mp3, all_mp3_paths). Optional playlist filter."""
    prefix = f"{playlist}/" if playlist else None
    seen_inodes: set[int] = set()
    groups: list[tuple[Path, list[Path]]] = []

    def add_group(rel_paths: list[str]) -> None:
        paths = [index.music_dir / p for p in rel_paths if (index.music_dir / p).is_file()]
        if not paths:
            return
        paths = sorted(paths, key=lambda p: (p.is_symlink(), str(p)))
        primary = paths[0].resolve()
        try:
            inode = primary.stat().st_ino
        except OSError:
            return
        if inode in seen_inodes:
            return
        seen_inodes.add(inode)
        groups.append((primary, paths))

    for entry in index.tracks.values():
        rel_paths = index._sort_paths(entry.get("paths", []))
        rel_paths = [p for p in rel_paths if (index.music_dir / p).is_file()]
        if not rel_paths:
            continue
        if prefix and not any(p.startswith(prefix) for p in rel_paths):
            continue
        add_group(rel_paths)

    for key in index.untagged:
        if prefix and not key.startswith(prefix):
            continue
        add_group([key])

    return groups


def analyze_library(
    music_dir: Path,
    *,
    playlist: str | None = None,
    limit: int | None = None,
    force: bool = False,
    include_series: bool = True,
) -> dict[str, int]:
    index = LibraryIndex(music_dir)
    index.build()

    groups = iter_track_groups(index, playlist=playlist)
    total = len(groups)
    stats = {"analyzed": 0, "skipped": 0, "linked": 0, "failed": 0}

    print(f"📀 {total} unique tracks", flush=True)
    models = _get_models()
    device = "CPU" if essentia_use_cpu() else "GPU"
    print(f"🧠 MusiCNN + {models.head_count} heads loaded once ({device})", flush=True)

    for i, (primary, paths) in enumerate(groups, 1):
        if limit is not None and stats["analyzed"] >= limit:
            break

        cached = load_cache(primary)
        fresh = cache_is_fresh(cached)
        try:
            if fresh and not force:
                stats["skipped"] += 1
                print(f"  [{i}/{total}] skip {primary.name}", flush=True)
            else:
                print(f"  [{i}/{total}] analyze {primary.name}", flush=True)
                cache_for_mp3(primary, force=force, include_series=include_series)
                stats["analyzed"] += 1
            stats["linked"] += propagate_sidecar(primary, paths)
        except Exception as e:
            stats["failed"] += 1
            print(f"❌ {primary.name}: {e}")

    return stats
