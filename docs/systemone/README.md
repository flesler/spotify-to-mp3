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
- d1-omni only after llama.cpp supports audio `files` on systemone.
- Liked Songs: multi-assign above threshold, not single winner.
