# Essentia audio analysis

Host-only local MP3 analysis via [Essentia](https://essentia.upf.edu/) + TensorFlow. Writes `Artist - Title.audio.yaml` sidecars next to each MP3.

## Setup

```bash
./scripts/download-essentia-models.sh
INSTALL_AUDIO_ANALYSIS=1 ./scripts/setup-venv.sh
```

In `.env`:

```env
AUDIO_ANALYSIS=1
ESSENTIA_CPU=0   # 1 = force CPU (e.g. when Whisper holds the GPU)
```

Run:

```bash
AUDIO_ANALYSIS=1 ./scripts/analyze-library.py              # full library
AUDIO_ANALYSIS=1 ./scripts/analyze-library.py ElectroMinita  # one playlist folder
AUDIO_ANALYSIS=1 ./scripts/analyze-library.py --force      # re-analyze all
```

Pass the playlist folder name as the first argument (must match a subfolder under `MUSIC_DIR`).

## Two model families (important)

Essentia ships the same task in two places with **different filenames**:

| Folder | Example | Size | Input | Notes |
|--------|---------|------|-------|-------|
| **`classifiers/`** | `mood_happy-musicnn-msd-**2**.pb` | ~3.2 MB each | Raw audio | v1 and v2 exist; full MusiCNN graph per classifier |
| **`classification-heads/`** | `mood_happy-msd-musicnn-**1**.pb` | ~82 KB each | 200-d embeddings | Tiny heads; share one embed pass |

**We use classification-heads** (plus one MusiCNN embedder). Legacy fat `classifiers/*.pb` files may still sit in `models/essentia/` but are unused.

### v1 / v2 confusion

- **`classifiers/`**: `-musicnn-msd-1.pb` and `-musicnn-msd-2.pb` are both full models; v2 is the newer release.
- **`classification-heads/`**: most moods use `-msd-musicnn-1.pb` in the filename; JSON inside may say `"version": "2"` — metadata version ≠ filename suffix.
- **DEAM** head: `deam-msd-musicnn-2.pb` (v2 head on embeddings).

MTG recommends **classification-heads on embeddings** so many labels share one MusiCNN forward pass (~1–2 GB VRAM vs ~6 GB for fat classifiers).

## Pipeline (`analysis_version: 3`)

Per track:

1. **Load audio** once at 16 kHz (ML + classical).
2. **MusiCNN embed** (`msd-musicnn-1.pb`, `model/dense/BiasAdd`) → `[time, 200]`.
3. **Heads on embeddings** (`TensorflowPredict2D`, `model/Softmax` or `model/Identity` for DEAM).
4. **MSD 50-tag autotag** (second MusiCNN output `model/Sigmoid` on the same audio) → top 10 tags.
5. **Classical** (CPU, no TF): BPM (`PercivalBpmEstimator`), key, loudness, dynamic complexity — same 16 kHz buffer.

### Sidecar fields

| Field | Source |
|-------|--------|
| `deam` | Valence/arousal arc (DEAM head v2) |
| `deam_series` | Downsampled valence/arousal over time |
| `classifiers` | Binary moods, gender, danceability, voice/instrumental, tonal/atonal — `P(positive)` only (see `CLASSIFIER_POSITIVE_CLASS` in `audio_analysis.py`; e.g. `gender: 0.19` → 19% female, 81% male) |
| `genres` | Top-3 per taxonomy (dortmund, rosamerica, tzanetakis) |
| `tags` | Top-10 MSD Last.fm-style tags |
| `features` | BPM, key, loudness, dynamic complexity |
| `embedding_mean` | Mean-pooled 200-d MusiCNN vector (similarity / clustering) |

Old sidecars without `analysis_version: 3` are re-analyzed on the next run.

## Models we download

| File | Role |
|------|------|
| `msd-musicnn-1.pb` + `.json` | Embeddings + MSD tags |
| `deam-msd-musicnn-2.pb` | DEAM valence/arousal |
| `{task}-msd-musicnn-1.pb` + `.json` | Classification heads (see lists in `audio_analysis.py`) |

### Heads in use

**Binary:** `mood_happy`, `mood_electronic`, `mood_relaxed`, `mood_aggressive`, `mood_sad`, `mood_party`, `mood_acoustic`, `gender`, `danceability`, `voice_instrumental`, `tonal_atonal`

**Genre (multi-class):** `genre_dortmund`, `genre_rosamerica`, `genre_tzanetakis`

**Not included:** `genre_electronic` has no MusiCNN head (only `discogs-effnet` in `classification-heads/`). Would need a separate embedder or the legacy fat classifier.

## GPU / VRAM

- Essentia loads all TF graphs at process start.
- Fat classifiers only: ~6.4 GB VRAM for 7 graphs on an 8 GB card.
- Heads architecture: much lower VRAM; fits alongside other GPU workloads more easily.
- Two Python processes both using the GPU will OOM even if each alone fits.
- `ESSENTIA_CPU=1` forces CPU when the GPU is busy.

## Not implemented (later)

- **Language** — `--language` on sync writes `playlist-language.txt`; profiling uses it or infers via lingua + Hebrew script.
- **Discogs 400/519** — needs `discogs-effnet` embedder, not MusiCNN heads.
- **Jamendo tags** — separate embedder stack.
- **Whisper lyrics** — different job, GPU-heavy.

## References

- [Essentia models index](https://essentia.upf.edu/models.html)
- [Auto-tagging tutorial](https://essentia.upf.edu/tutorial_tensorflow_auto-tagging_classification_embeddings.html)
- [Classification heads vs classifiers (GitHub #1329)](https://github.com/MTG/essentia/issues/1329)
