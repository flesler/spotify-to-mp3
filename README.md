# Spotify to MP3 Downloader

Downloads tracks from Spotify playlists as high-quality MP3s from YouTube with smart deduplication and metadata management.

## Features

- 🎵 **Smart Downloads**: Searches YouTube via `yt-dlp` for best audio quality (192K MP3)
- 🔍 **Duplicate Detection**: Scans entire music library before downloading - skips if track exists anywhere
- 🔄 **Auto-Renaming**: Renames existing files to clean "Artist - Title.mp3" format
- 🔗 **Hard Linking**: Creates hard links in playlist directories instead of duplicating files
- 🎨 **Rich Metadata**: Embeds album artwork, artist, album name, year, and ID3 tags
- 📦 **Playlist Organization**: Each playlist gets its own directory with properly named files
- ⚡ **Incremental Updates**: Only downloads new tracks on subsequent runs

## Installation

### Prerequisites

- Python 3.7+
- yt-dlp (installed via virtual environment or system package)

### Quick Setup

```bash
git clone git@github.com:flesler/spotify-to-mp3.git
cd spotify-to-mp3

# Run the setup script to create virtual environment and install dependencies
./setup-venv.sh

# Activate the virtual environment
source .venv/bin/activate
```

### Manual Setup (Alternative)

```bash
# Create virtual environment manually
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Install yt-dlp if not already available
pip install yt-dlp
```

## Usage

```bash
# Activate virtual environment first
source .venv/bin/activate

# Download a playlist
python spotify_playlist_downloader.py <playlist_url_or_id> [options]
```

### Examples

```bash
# Download a playlist
python spotify_playlist_downloader.py https://open.spotify.com/playlist/37i9p40INhoKS8ORvUy5VQ

# Download your liked/saved songs (requires OAuth authentication)
python spotify_playlist_downloader.py liked

# Export track list without downloading
python spotify_playlist_downloader.py <playlist_url> --export

# Dry run - see what would be downloaded
python spotify_playlist_downloader.py <playlist_url> --dry-run

# Test with first 3 tracks
python spotify_playlist_downloader.py <playlist_url> --limit 3

# Disable auto-renaming
python spotify_playlist_downloader.py <playlist_url> --no-rename

# Disable hard linking (copy instead)
python spotify_playlist_downloader.py <playlist_url> --no-link

# Disable metadata fixing
python spotify_playlist_downloader.py <playlist_url> --no-fix-metadata
```

### Options

- `--dry-run`: Preview what would be downloaded without actually downloading
- `--export`: Export track list to text file instead of downloading
- `--limit N`: Limit to first N tracks (useful for testing)
- `--no-rename`: Don't rename existing files to clean format
- `--no-link`: Copy files instead of creating hard links
- `--no-fix-metadata`: Skip metadata/album art updates

## How It Works

1. **Spotify API**: Fetches playlist tracks using Spotify's API (client credentials or OAuth)
2. **Duplicate Check**: Scans music directory recursively for existing tracks
   - Filename matching (case-insensitive)
   - Duration matching (±5 seconds tolerance) when Spotify provides duration
3. **Smart Actions**:
   - If track exists elsewhere → creates hard link or renames to clean format
   - If track doesn't exist → downloads from YouTube via `yt-dlp`
4. **Metadata Enhancement**: Downloads album artwork and embeds full ID3 tags
5. **Playlist Organization**: Creates M3U file for Navidrome integration

## Directory Structure

```
/mnt/ssd/Music/
├── Playlist Name 1/
│   ├── Artist 1 - Song 1.mp3
│   ├── Artist 1 - Song 2.mp3
│   └── Artist 2 - Song 3.mp3
├── Playlist Name 2/
│   ├── Artist 1 - Song 1.mp3  # Hard link to same file
│   └── Artist 3 - Song 4.mp3
└── (all files are hard-linked when shared across playlists)
```

## Configuration

### Using .env File (Recommended)

Copy `config.example.env` to `.env` and modify as needed:

```bash
cp config.example.env .env
```

Edit `.env` with your settings:

```python
MUSIC_DIR = "/mnt/ssd/Music"  # Base directory for all music
SPOTIFY_CLIENT_ID = "your_client_id"
SPOTIFY_CLIENT_SECRET = "your_client_secret"
DOWNLOAD_QUALITY = "192K"  # 192K, 256K, 320K, or best
ENABLE_AUDIO_FINGERPRINT = false  # Enable content-based deduplication
```

### Environment Variable Overrides

You can also set environment variables directly:

```bash
export SPOTIFY_CLIENT_ID="your_client_id"
export SPOTIFY_CLIENT_SECRET="your_client_secret"
export MUSIC_DIR="/path/to/music"
```

Environment variables take precedence over `.env` file values.

## Spotify API Setup

### For Public Playlists (Client Credentials)

1. Go to [Spotify Developer Dashboard](https://developer.spotify.com/dashboard)
2. Create a new app
3. Copy Client ID and Client Secret
4. Update `.env` with your credentials

**Note**: Requires Spotify Premium subscription on the account that created the app.

### For Liked Songs (OAuth Authentication)

When downloading your liked/saved songs, the script will:
1. Open your browser for Spotify authentication
2. Request permission to access your library
3. Save a refresh token at `~/.config/spotify-to-mp3/token.json`
4. Automatically use cached tokens on subsequent runs

No extra setup needed - just run `python spotify_playlist_downloader.py liked`

## Navidrome Integration

This downloader is designed for Navidrome:
- Files are stored in a flat structure that Navidrome can scan
- Album artwork embedded directly in MP3 files
- Proper ID3 tags for artist/album/title metadata
- Duplicate prevention keeps library clean

Run Navidrome's library scan after downloading:
```bash
# Navidrome will automatically detect new files
# Or trigger manual scan in admin panel
```

## Notes

- Downloads are limited to 192K MP3 quality
- Rate limiting prevents YouTube bans (2-5 second delays)
- Failed downloads are cleaned up automatically
- Already-downloaded tracks are skipped on re-runs
