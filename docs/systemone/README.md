# System One mood playlists — archived experiment

**Status:** **Tabled (2026-10-24).** Use **`match-playlists.py`** + Essentia. This folder is the record of what we tried.

**Adoption bar (if revived):** Ship LLM routing only if it **beats** math on eval **or** matches accuracy with **much simpler** input (e.g. cold MP3). **No** math+LLM hybrid.

**API:** `POST http://127.0.0.1:58261/v1/systemone` (llama.cpp router, preset id from `models.ini`). Not `jev-server` `:8792`. Contract: `~/Code/jev-server/docs/official/typesafe-api.md`.

**Production pipeline:** `docs/essentia.md`, `docs/snippets.md` — analyze, `correlate-playlist.py`, `match-playlists.py`.

---

## What we tried

- **Choice** — one pick among 7 mood folders + `none`; state = artist/title + Essentia/Spotify + [rubrics](./playlist-rubrics.yaml) + optional math top-3 (**E2c**).
- **Noul joint** — seven yes-probabilities per playlist in one call; multi-pick via threshold (e.g. ≥ 0.15).
- **Rejected:** spectrogram→d3 vision (garbage output), raw MP3 on d3, d1-omni audio (501 on our build), lyrics (E2d), Clef/Nimble as champions.

**Integrity:** Never put file path or source folder in model `state` — labels are eval-only.

---

## Rubrics & eval

- **[playlist-rubrics.yaml](./playlist-rubrics.yaml)** — human intent per folder; overlap is normal (tracks in multiple folders via library hard links).
- **Eval:** stratified samples from mood folders (weak oracle). **Liked Songs** = real backlog, no ground truth.
- **Multi-label:** truth = all mood folders from `library-index.json`; metrics included top-1, any-truth, multi-pick hit rate (prob ≥ 0.15 / math within 0.08 of top).

**E2 presets:** E2a rubrics only · E2b +Essentia/Spotify · E2c +math hint · E2d +lyrics (no win).

---

## Results (headline)

| Slice | Math top-1 | Best LLM | Notes |
|-------|------------|----------|--------|
| n=21, placeholder rubrics | 66.7% | flash E2c **tie** | early spike |
| n=70, real rubrics | **60%** | flash E2c **51%** | bar not met |
| n=21, Noul joint vs Choice | **~68%** | Noul top-1 **~52%** | Choice returned `none` on 14/21; Noul multi-hit ~95% (very permissive) vs math ~76% |

**Champion config (LLM):** `vllm-d3-flash-Q4_K_M`, E2c — still below math at scale.

---

## Ops (if you rerun)

One GGUF loaded at a time on `:58261`; don’t leave `mmproj` on d3 presets (VRAM). Build library index **once** over SSHFS `MUSIC_DIR` (Pi music).

---

## If resumed

- Fix Choice `none` rate on small samples before any new A/B.
- Tune Noul thresholds or try **isolated** Noul (one playlist per request; slower locally).
- Liked Songs: multi-assign above threshold, not single winner.

---

## LFM3-Audio-2B-GGUF (Nov 2026 — rejected)

[LiquidAI/LFM3-Audio-2B-GGUF](https://huggingface.co/LiquidAI/LFM3-Audio-2B-GGUF) was tested as a **cold MP3** path (chat API, 16 kHz clip, rubrics in system prompt, no title). We upgraded llama.cpp to **0.8.0-dev (build 1443)** for it; **weights and eval scripts were removed** after the run — only this write-up remains.

**Setup (historical):** main GGUF + **mmproj** on `:58261`; 40 s clips from Pi `/mnt/ssd/Music` via SSH/ffmpeg.

**Eval (2026-11-02):** stratified **7 × 7 mood folders** (seed 42, **n=49**), weak oracle = parent folder. Math used existing `.audio.yaml` + cached profiles (still requires `analyze-audio`, not ID3/Spotify).

| Method | Input | Top-1 |
|--------|--------|-------|
| Math full | Essentia sidecar (features + timbre + tags) | **63.3%** |
| Math no tags | Same, tags zeroed | **63.3%** |
| Math features-only | No tags, no embedding timbre | **49.0%** |
| LFM3-Audio cold | MP3 clip only | **14.3%** (22.4% if truth anywhere in model’s list) |

**Verdict:** **Do not adopt.** LFM3 collapsed to labeling most clips **Epic** (7/7 Epic correct, ~0/7 on other folders). Not “slightly worse” than math — far below even features-only Essentia (~49%). Tags in the sidecar did not move math on this slice; timbre did.

**Also skipped:** [LFM3-Omni-2B-GGUF](https://huggingface.co/LiquidAI/LFM3-Omni-2B-GGUF) — no point after Audio-only failure at 2B.
