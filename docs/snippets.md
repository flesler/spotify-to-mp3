# Snippets

Copy-paste recipes. All scripts assume project root, `.env` loaded, and venv active (`source .venv/bin/activate` or `./scripts/run.sh`).

## Env (`.env`)

```env
SPOTIFY_CLIENT_ID=...
SPOTIFY_CLIENT_SECRET=...
MUSIC_DIR=/path/to/Music

AUDIO_ANALYSIS=1      # host only — Essentia sidecars
ESSENTIA_CPU=0        # 1 = CPU when GPU is busy
DOWNLOAD_QUALITY=192K
```

---

## Setup

```bash
./scripts/setup-venv.sh
INSTALL_AUDIO_ANALYSIS=1 ./scripts/setup-venv.sh   # + Essentia
./scripts/download-essentia-models.sh
./scripts/test.sh
```

Verify Spotify credentials:

```bash
./scripts/run.sh --verify
```

---

## Download MP3s

Full playlist (URL, ID, or folder name):

```bash
./scripts/run.sh "https://open.spotify.com/playlist/..."
./scripts/run.sh 37i9dQZF1DXcBWIGoYBM5M
./scripts/run.sh Rivotril
./scripts/run.sh /path/to/Music/Epic          # uses playlist-id.txt inside folder
```

Liked songs (incremental — skips already on disk):

```bash
./scripts/run.sh liked
./scripts/run.sh liked --limit 10
./scripts/run.sh liked --full                # re-fetch entire library
```

Dry run / export:

```bash
./scripts/run.sh Rivotril --dry-run
./scripts/run.sh Rivotril --export
```

**One track by Spotify ID** (index → download → analyze → match):

```bash
./scripts/try-track.py 4y6VbDYN3kYSDZkbOim5US
./scripts/try-track.py "https://open.spotify.com/track/622fSIVOm6SPcLPNOoYeJn"

./scripts/try-track.py <id> --no-match          # skip playlist match
./scripts/try-track.py <id> --refresh-profiles  # rebuild missing profiles first
```

Download only (no analyze/match):

```bash
./scripts/download-track.py 4y6VbDYN3kYSDZkbOim5US
```

---

## Audio analysis (Essentia)

Writes `Artist - Title.audio.yaml` next to each MP3. Host only (`AUDIO_ANALYSIS=1`).

Batch — full library or playlist folders (runs sequentially):

```bash
PYTHONUNBUFFERED=1 AUDIO_ANALYSIS=1 TF_CPP_MIN_LOG_LEVEL=3 \
  ./scripts/analyze-library.py

PYTHONUNBUFFERED=1 AUDIO_ANALYSIS=1 TF_CPP_MIN_LOG_LEVEL=3 \
  ./scripts/analyze-library.py Epic Rivotril "The Feels" Tranca Pila

./scripts/analyze-library.py ElectroMinita --limit 20   # skips don't count
./scripts/analyze-library.py Epic --force               # re-analyze
```

Single file or folder:

```bash
AUDIO_ANALYSIS=1 ./scripts/analyze-audio.py "Liked Songs/Some Artist - Song.mp3"
AUDIO_ANALYSIS=1 ./scripts/analyze-audio.py Epic/
AUDIO_ANALYSIS=1 ./scripts/analyze-audio.py path/to/song.mp3 --force
```

More detail: [essentia.md](essentia.md)

---

## Playlist profiles (local audio)

Builds vibe fingerprint from sidecars. Writes **`{playlist}/.playlist-profile.json`** (regenerate anytime).

```bash
./scripts/correlate-playlist.py Epic ElectroMinita Oldies Tranca Pila Rivotril "The Feels"

./scripts/correlate-playlist.py Epic --min-distance 0.25 --max-std 0.2
./scripts/correlate-playlist.py Epic --no-save          # print only
```

Output includes: decades (from MP3 `TDRC`), tag signature, stable dims (low spread), outliers.

**Spotify metadata** (years/genres from API — needs `.spotify.yaml`):

```bash
./scripts/profile-playlist.py Oldies
```

---

## Match a track → playlists

Requires analyzed sidecar + cached `.playlist-profile.json`. Uses **stable dims only** (consistent across songs in each playlist).

```bash
./scripts/match-playlists.py "Woodkid - Run Boy Run"
./scripts/match-playlists.py "Epic/Thomas Bergersen Two Steps from Hell - Heart of Courage.mp3"
./scripts/match-playlists.py "Daniel Pemberton Brian May - Eternia" --top 5

./scripts/match-playlists.py "Some Song" --refresh       # rebuild missing profiles
./scripts/match-playlists.py "Some Song" --refresh-all
```

---

## End-to-end: Spotify ID → playlist match

```bash
./scripts/try-track.py 4y6VbDYN3kYSDZkbOim5US
./scripts/try-track.py "https://open.spotify.com/track/622fSIVOm6SPcLPNOoYeJn" --top 8
```

Manual steps (if needed):

```bash
./scripts/download-track.py <id>
AUDIO_ANALYSIS=1 ./scripts/analyze-audio.py "Liked Songs/Artist - Title.mp3"
./scripts/match-playlists.py "Title" --refresh
```

---

## Sidecar / profile files

| File | Location | Purpose |
|------|----------|---------|
| `*.audio.yaml` | next to MP3 | Essentia analysis (`analysis_version: 3`) |
| `.playlist-profile.json` | inside playlist folder | Cached means, stable dims, tags, centroid |
| `*.spotify.yaml` | next to MP3 | Spotify API metadata (optional) |
| `playlist-id.txt` | inside playlist folder | Spotify playlist ID for re-crawl |

---

## Typical playlist batch

Analyze + profile all mood playlists:

```bash
PYTHONUNBUFFERED=1 AUDIO_ANALYSIS=1 TF_CPP_MIN_LOG_LEVEL=3 \
  ./scripts/analyze-library.py Epic Rivotril "The Feels" Tranca Pila ElectroMinita Oldies

./scripts/correlate-playlist.py Epic Rivotril "The Feels" Tranca Pila ElectroMinita Oldies
```

Log to file:

```bash
PYTHONUNBUFFERED=1 AUDIO_ANALYSIS=1 TF_CPP_MIN_LOG_LEVEL=3 \
  ./scripts/analyze-library.py Epic Rivotril 2>&1 | tee /tmp/analyze.log
```
