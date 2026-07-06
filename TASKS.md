# Spotify to MP3 - Task List

## Project Overview

Smart Spotify playlist downloader that:
- Downloads tracks from Spotify playlists as MP3s via YouTube (yt-dlp)
- Prevents duplicates across the entire music library
- Uses hard links for shared tracks across playlists
- Embeds rich metadata and album artwork
- Designed for Navidrome integration

## Current State

✅ Core downloader working (spotify_playlist_downloader.py)  
✅ Duplicate detection implemented  
✅ Hard linking support  
✅ Metadata/album art embedding  
✅ Playlist organization  

## TODO

### 1. Virtual Environment Setup

- [ ] Create `.venv` in project root
- [ ] Add `setup-venv.sh` script to:
  - Create venv if missing
  - Install requirements.txt
  - Verify yt-dlp is available
- [ ] Update README with venv usage instructions
- [ ] Ensure `.venv/` is in `.gitignore`

### 2. "Liked Songs" Sync Support

Currently only supports playlist URLs. Need to add:

- [ ] Detect special keyword `liked` or `saved`
- [ ] Use Spotify API endpoint `/me/tracks` to fetch liked songs
  - Requires OAuth flow instead of client credentials
  - Store refresh token securely (~/.config/spotify-to-mp3/token.json)
- [ ] Treat liked songs as a virtual playlist
- [ ] Support incremental sync (only new likes)

### 3. Smart Deduplication Enhancements

Current dedup checks filename. Improve with:

- [ ] **Audio fingerprinting** (Chromaprint/AcoustID) for content-based dedup
  - Detects same song with different metadata/filenames
  - Optional feature (requires fpcalc)
- [ ] **Duration matching** - Compare track duration from Spotify vs existing file
- [ ] **Fuzzy filename matching** - Handle variations like:
  - "Artist - Title (Remix)"
  - "Artist feat. Other - Title"
  - Different capitalization/punctuation
- [ ] **Cross-library dedup** - Check multiple base directories

### 4. Configuration File

Move hardcoded values to config:

- [ ] `config.yaml` or `.env` file with:
  - `MUSIC_DIR` (default: /mnt/ssd/Music)
  - `SPOTIFY_CLIENT_ID`
  - `SPOTIFY_CLIENT_SECRET`
  - `DOWNLOAD_QUALITY` (192K, 320K, etc.)
  - `ENABLE_AUDIO_FINGERPRINT` (true/false)
- [ ] Support environment variable overrides
- [ ] Don't commit secrets to git

### 5. Better Error Handling & Logging

- [ ] Structured logging (info/warn/error levels)
- [ ] Retry logic for failed downloads with backoff
- [ ] Summary report after sync (downloaded/skipped/failed)
- [ ] Log file output option (`--log-file`)

### 6. Testing

- [ ] Unit tests for filename sanitization
- [ ] Unit tests for duplicate detection logic
- [ ] Integration test: mock Spotify API + download single track
- [ ] Test liked songs sync end-to-end

### 7. Documentation Updates

- [ ] Add venv setup to README
- [ ] Document liked songs OAuth flow
- [ ] Add examples for config file usage
- [ ] Document audio fingerprinting setup (optional)

## Priority Order

1. **Virtual environment** (blocks deployment)
2. **Configuration file** (cleaner than hardcoded values)
3. **Liked songs sync** (core feature request)
4. **Enhanced deduplication** (nice-to-have, can iterate)
5. **Testing & docs** (polish)
