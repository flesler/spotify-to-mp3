#!/usr/bin/env python3
"""
Spotify Playlist to MP3 Downloader
Downloads tracks from a Spotify playlist as MP3s from YouTube using yt-dlp.

This script requires the project's virtual environment to be active.
If running directly, it will check for .venv and activate it automatically.
"""

import os
import sys

# Auto-activate venv if not already active
if "VIRTUAL_ENV" not in os.environ:
    venv_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".venv", "bin", "activate")
    if os.path.exists(venv_path):
        # Can't actually activate in Python, but we can check when called via wrapper
        pass
    else:
        print("❌ Error: Virtual environment not found at .venv/")
        print("Run: ./scripts/setup-venv.sh")
        sys.exit(1)

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv
from mutagen.id3 import APIC, TALB, TDRC, TIT2, TPE1, TPE2, TXXX, WOAR, WOAS
from mutagen.mp3 import MP3

# Import API modules
# Import API modules
from api import API
from oauth import OAuth

# Load environment variables from .env file if it exists
load_dotenv()

# Spotify API credentials (required)
SPOTIFY_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")

if not SPOTIFY_CLIENT_ID or not SPOTIFY_CLIENT_SECRET:
    print("❌ Error: SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET must be set in .env file")
    sys.exit(1)

# Download directory (required)
MUSIC_DIR: str = os.getenv("MUSIC_DIR") or ""

if not MUSIC_DIR:
    print("❌ Error: MUSIC_DIR must be set in .env file")
    sys.exit(1)

# Download quality (192K, 256K, 320K, best)
# Download quality
DOWNLOAD_QUALITY = os.getenv("DOWNLOAD_QUALITY", "192K")

# Fuzzy matching threshold (0-100, higher = stricter)
FUZZY_MATCH_THRESHOLD = 85


def ytdlp_cmd() -> list[str]:
    """Run yt-dlp from this Python environment, not a stale system binary on PATH."""
    return [sys.executable, "-m", "yt_dlp"]


def _ytdlp_error_message(stderr: str) -> str:
    """Pick the most useful line from yt-dlp stderr (skip deprecation noise)."""
    lines = [line.strip() for line in stderr.strip().splitlines() if line.strip()]
    errors = [line for line in lines if line.startswith("ERROR:")]
    if errors:
        return errors[-1].removeprefix("ERROR:").strip()
    for line in lines:
        if "Deprecated Feature" in line or line.startswith("WARNING:"):
            continue
        return line
    useful = [line for line in lines if "Deprecated Feature" not in line and not line.startswith("WARNING:")]
    return useful[-1] if useful else "unknown error"


def sanitize_filename(filename):
    """Remove/replace characters that are problematic for filenames"""
    # Replace problematic characters
    filename = re.sub(r'[<>:"/\\|?*]', "", filename)
    filename = re.sub(r"[^\w\s\-_\(\)\[\].]", "", filename)
    filename = re.sub(r"\s+", " ", filename).strip()
    return filename


def fuzzy_match_filenames(spotify_name, existing_name):
    """Check if two filenames likely refer to the same song using fuzzy matching"""
    from difflib import SequenceMatcher

    # Normalize both strings
    def normalize(s):
        s = s.lower()
        # Remove common variations
        s = re.sub(r"\s*\(.*?\)", "", s)  # Remove parentheses content (remix, etc)
        s = re.sub(r"\s*feat\.?\s+\S+", "", s, flags=re.IGNORECASE)  # Remove feat. artist
        s = re.sub(r"[^a-z0-9\s]", "", s)  # Remove special chars
        s = re.sub(r"\s+", " ", s).strip()
        return s

    norm_spotify = normalize(spotify_name)
    norm_existing = normalize(existing_name)

    # Calculate similarity
    similarity = SequenceMatcher(None, norm_spotify, norm_existing).ratio() * 100

    return similarity >= FUZZY_MATCH_THRESHOLD


def download_album_art(track):
    """Download album artwork using data already from playlist API"""
    try:
        album = track.get("album", {})
        images = album.get("images", [])

        if not images:
            print("   ⚠️  No album images found")
            return None, album

        # Sort by size (width) and get the largest
        largest_image = max(images, key=lambda x: x.get("width", 0))
        image_url = largest_image["url"]
        image_size = largest_image.get("width", 0)

        # Download the image
        response = requests.get(image_url, timeout=10)
        response.raise_for_status()

        print(f"   🖼️  Downloaded {image_size}x{image_size} album art")
        return response.content, album

    except requests.exceptions.RequestException as e:
        print(f"   ⚠️  Album art download failed: Network error - {e}")
    except Exception as e:
        print(f"   ⚠️  Album art download failed: {e}")

    return None, track.get("album", {})


def fix_mp3_metadata_smart(file_path, track, youtube_id=None):
    """Intelligently fix MP3 metadata only if needed"""
    try:
        audio = MP3(file_path)
        if audio.tags is None:
            # No tags at all, need everything
            album_art_data, album_info = download_album_art(track)
            set_mp3_metadata(file_path, track, album_art_data, album_info, youtube_id=youtube_id)
            return

        # Check what's missing
        title = audio.tags.get("TIT2")
        artist = audio.tags.get("TPE1")
        album = audio.tags.get("TALB")
        artwork = audio.tags.getall("APIC")  # Get all APIC frames
        spotify_id = _get_txxx(audio.tags, "SPOTIFY_ID")
        stored_youtube_id = _get_txxx(audio.tags, "YOUTUBE_ID")

        needs_metadata = (
            not title
            or str(title[0]) != track["name"]
            or not artist
            or str(artist[0]) != track["artists"]
            or not album
            or (track.get("id") and spotify_id != track["id"])
            or (youtube_id and stored_youtube_id != youtube_id)
        )

        needs_artwork = not artwork or len(artwork) == 0

        if needs_metadata or needs_artwork:
            album_art_data, album_info = None, None
            if needs_artwork:
                album_art_data, album_info = download_album_art(track)
            else:
                album_info = track.get("album", {})
            set_mp3_metadata(file_path, track, album_art_data, album_info, youtube_id=youtube_id)
        else:
            print("   ✅ Metadata already complete")

    except Exception as e:
        print(f"   ⚠️  Metadata check failed: {e}")
        # Fallback to full update
        album_art_data, album_info = download_album_art(track)
        set_mp3_metadata(file_path, track, album_art_data, album_info, youtube_id=youtube_id)


def _get_txxx(tags, desc: str) -> str | None:
    for frame in tags.getall("TXXX"):
        if frame.desc == desc:
            return str(frame.text[0])
    return None


def set_mp3_metadata(file_path, track, album_art_data=None, album_info=None, youtube_id=None):
    """Set proper ID3 tags on MP3 file"""
    try:
        # Load the MP3 file
        audio = MP3(file_path)

        # Ensure ID3 tags exist
        if audio.tags is None:
            audio.add_tags()

        # Type assertion: tags are guaranteed to exist after add_tags()
        assert audio.tags is not None
        tags = audio.tags

        # Update/set basic metadata (overwrite existing)
        tags.setall("TIT2", [TIT2(encoding=3, text=track["name"])])  # Title
        tags.setall("TPE1", [TPE1(encoding=3, text=track["artists"])])  # Artist
        tags.setall("TPE2", [TPE2(encoding=3, text=track["artists"])])  # Album Artist

        # Set album info if available
        if album_info:
            album_name = album_info.get("name", "Unknown Album")
            tags.setall("TALB", [TALB(encoding=3, text=album_name)])  # Album

            # Release date
            release_date = album_info.get("release_date", "")
            if release_date:
                year = release_date.split("-")[0]
                tags.setall("TDRC", [TDRC(encoding=3, text=year)])  # Year

        # Embed album artwork (replace existing)
        if album_art_data:
            # Remove existing artwork
            tags.delall("APIC")
            # Add new artwork
            tags.add(
                APIC(
                    encoding=3,  # UTF-8
                    mime="image/jpeg",  # JPEG image
                    type=3,  # Cover (front)
                    desc="Cover",
                    data=album_art_data,
                )
            )

        spotify_id = track.get("id")
        if spotify_id:
            tags.setall("TXXX:SPOTIFY_ID", [TXXX(encoding=3, desc="SPOTIFY_ID", text=spotify_id)])
            tags.setall("WOAR", [WOAR(url=f"https://open.spotify.com/track/{spotify_id}")])

        if youtube_id:
            tags.setall("TXXX:YOUTUBE_ID", [TXXX(encoding=3, desc="YOUTUBE_ID", text=youtube_id)])
            tags.setall("WOAS", [WOAS(url=f"https://www.youtube.com/watch?v={youtube_id}")])

        # Save the tags
        audio.save()

        # Report what was added
        parts = []
        if album_art_data:
            parts.append("artwork")
        if album_info:
            parts.append("album info")
        parts.append("metadata")
        print(f"   🎨 Added {', '.join(parts)}")

        return True

    except Exception as e:
        print(f"   ⚠️  Metadata update failed: {e}")
        return False


def check_if_track_exists(artists, title, base_music_dir, auto_rename=True, duration_ms=None):
    """Check if a track already exists anywhere in the music directory

    Uses multiple strategies:
    1. Exact filename matching (fast path)
    2. Duration matching (±5s tolerance)
    3. Fuzzy filename matching (handles remixes, feat., etc.)
    """
    # Clean up artists and title for better matching
    clean_artists = sanitize_filename(artists).lower()
    clean_title = sanitize_filename(title).lower()
    clean_spotify_name = sanitize_filename(f"{artists} - {title}")
    expected_filename = f"{clean_spotify_name}.mp3"

    # Fast path: exact filename anywhere in library (single rglob pattern)
    for mp3_file in Path(base_music_dir).rglob(expected_filename):
        if mp3_file.is_file():
            return _finalize_match(mp3_file, clean_spotify_name, auto_rename)

    candidates = []

    # Phase 1: Collect all potential matches
    for pattern in [clean_artists, clean_title]:
        if len(pattern) < 3:  # Skip very short patterns
            continue
        for mp3_file in Path(base_music_dir).rglob("*.mp3"):
            filename_lower = mp3_file.name.lower()

            # Strategy 1: Exact match (both artist and title in filename)
            if clean_artists in filename_lower and clean_title in filename_lower:
                candidates.append((mp3_file, "exact", 100))
                continue

            # Strategy 2: Fuzzy matching
            if fuzzy_match_filenames(clean_spotify_name, mp3_file.stem):
                candidates.append((mp3_file, "fuzzy", None))

    # Deduplicate candidates by filepath (keep highest confidence)
    seen_files = {}
    for filepath, match_type, confidence in candidates:
        if filepath not in seen_files or (confidence and confidence > seen_files[filepath][2]):
            seen_files[filepath] = (filepath, match_type, confidence)

    candidates = list(seen_files.values())

    # Phase 2: Verify candidates with duration
    verified = []
    for filepath, match_type, confidence in candidates:
        file_duration_ms = None

        # Duration matching
        if duration_ms:
            try:
                audio = MP3(filepath)
                file_duration_ms = int(audio.info.length * 1000)
                # Allow 5 second tolerance
                if abs(file_duration_ms - duration_ms) > 5000:
                    continue  # Duration doesn't match
            except Exception:
                pass  # Can't read duration, skip duration check

        verified.append((filepath, match_type, confidence, file_duration_ms))

    # Phase 3: Return best match
    if not verified:
        return None

    # Prefer exact matches over fuzzy
    best = max(verified, key=lambda x: (x[1] == "exact", x[2] or 0))
    return _finalize_match(best[0], clean_spotify_name, auto_rename)


def _finalize_match(mp3_file, clean_spotify_name, auto_rename):
    """Rename to clean format if needed, then return the file path."""
    current_name = mp3_file.stem
    if current_name == clean_spotify_name:
        return mp3_file

    if auto_rename:
        new_filename = clean_spotify_name + ".mp3"
        new_path = mp3_file.parent / new_filename
        if not new_path.exists():
            try:
                mp3_file.rename(new_path)
                print(f"🔄 Renamed: {mp3_file.name} → {new_filename}")
                return new_path
            except Exception as e:
                print(f"⚠️  Rename failed: {e}")
                return mp3_file

    return mp3_file


def download_track(
    track, playlist_dir, base_music_dir, spotify_api, dry_run=False, auto_rename=True, auto_link=True, fix_metadata=True
):
    """Download a single track using yt-dlp"""
    artists = track["artists"]
    title = track["name"]

    # Create search query
    search_query = f"{artists} - {title}"
    sanitized_filename = sanitize_filename(f"{artists} - {title}")

    # Check if file already exists anywhere
    existing_file = check_if_track_exists(artists, title, base_music_dir, auto_rename, track.get("duration_ms"))
    if existing_file:
        # Check if it's already in the target playlist directory
        target_path = Path(playlist_dir) / f"{sanitized_filename}.mp3"
        if existing_file.parent != Path(playlist_dir) and auto_link:
            # File exists elsewhere, create hard link in playlist directory
            if not target_path.exists():
                if dry_run:
                    print(f"🔗 Would link: {existing_file.relative_to(base_music_dir)} → {target_path.name}")
                    if fix_metadata:
                        print("   🎨 Would fix metadata")
                    return "skipped"
                try:
                    target_path.hardlink_to(existing_file)
                    print(f"🔗 Linked: {existing_file.relative_to(base_music_dir)} → {target_path.name}")

                    # Fix metadata on the linked file
                    if fix_metadata:
                        fix_mp3_metadata_smart(target_path, track)

                    return "skipped"
                except Exception:
                    # Fall back to copy if hard link fails
                    try:
                        import shutil

                        shutil.copy2(existing_file, target_path)
                        print(f"📋 Copied: {existing_file.relative_to(base_music_dir)} → {target_path.name}")

                        # Fix metadata on the copied file
                        if fix_metadata:
                            fix_mp3_metadata_smart(target_path, track)

                        return "skipped"
                    except Exception as e2:
                        print(f"⚠️  Link/copy failed: {e2}")
            else:
                print(f"⏭️  Already in playlist: {target_path.name}")

                # Still fix metadata if needed
                if fix_metadata and not dry_run:
                    fix_mp3_metadata_smart(target_path, track)
                elif fix_metadata and dry_run:
                    print("   🎨 Would fix metadata")

                return "skipped"
        else:
            print(f"⏭️  Exists: {existing_file.relative_to(base_music_dir)}")

            # Fix metadata on existing file
            if fix_metadata and not dry_run:
                fix_mp3_metadata_smart(existing_file, track)
            elif fix_metadata and dry_run:
                print("   🎨 Would fix metadata")

            return "skipped"

    # Check if file exists in the target playlist directory
    output_path = Path(playlist_dir) / f"{sanitized_filename}.mp3"
    if output_path.exists():
        print(f"⏭️  Exists in playlist dir: {sanitized_filename}")

        # Fix metadata if needed
        if fix_metadata and not dry_run:
            fix_mp3_metadata_smart(output_path, track)
        elif fix_metadata and dry_run:
            print("   🎨 Would fix metadata")

        return "skipped"

    if dry_run:
        print(f"🎵 {search_query}")
        return True

    print(f"🔍 Searching: {search_query}")

    try:
        # yt-dlp command to search and download from YouTube
        cmd = [
            *ytdlp_cmd(),
            f"ytsearch1:{search_query}",  # Search for 1 result
            "--extract-audio",
            "--audio-format",
            "mp3",
            "--audio-quality",
            DOWNLOAD_QUALITY,
            "--output",
            str(playlist_dir) + "/" + sanitized_filename + ".%(ext)s",
            "--no-playlist",
            "--ignore-errors",
            "--no-warnings",
            "--sleep-interval",
            "2",  # Sleep 2 seconds between downloads
            "--max-sleep-interval",
            "5",  # Random sleep up to 5 seconds
            "--retries",
            "3",  # Retry failed downloads 3 times
            "--fragment-retries",
            "3",  # Retry failed fragments
            "--abort-on-unavailable-fragment",  # Skip corrupted videos
            "--no-progress",
            "--write-info-json",
        ]

        # Run yt-dlp
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)

        if result.returncode == 0:
            output_path = Path(playlist_dir) / f"{sanitized_filename}.mp3"
            info_json = Path(playlist_dir) / f"{sanitized_filename}.info.json"
            youtube_id = _read_youtube_id(info_json)
            print(f"✅ Downloaded: {sanitized_filename}")

            if output_path.exists() and fix_metadata:
                fix_mp3_metadata_smart(output_path, track, youtube_id=youtube_id)

            return True
        else:
            # Clean up any partial downloads - be more aggressive with cleanup
            cleanup_patterns = [
                f"{sanitized_filename}.*",  # Exact filename matches
                f"*{sanitized_filename.split(' - ')[-1]}*",  # Match by song title
            ]

            for pattern in cleanup_patterns:
                partial_files = list(Path(playlist_dir).glob(pattern))
                for partial_file in partial_files:
                    # Remove any non-mp3 files that might be leftover
                    if partial_file.suffix in [
                        ".part",
                        ".webm",
                        ".m4a",
                        ".tmp",
                        ".f4a",
                        ".opus",
                        ".json",
                    ] or partial_file.name.endswith(".webm.part"):
                        try:
                            partial_file.unlink()
                            print(f"   🗑️  Cleaned: {partial_file.name}")
                        except Exception:
                            pass

            print(f"❌ Failed: {sanitized_filename}")
            error_msg = _ytdlp_error_message(result.stderr)
            if error_msg:
                print(f"   Error: {error_msg}")

                blocking_indicators = [
                    "No such file or directory",
                    "Unable to rename file",
                    "HTTP Error 429",
                    "Too Many Requests",
                    "Sign in to confirm you're not a bot",
                    "This video is not available",
                    "Private video",
                    "Video unavailable",
                ]

                if any(indicator in error_msg for indicator in blocking_indicators[:4]):
                    print("\n🛑 Detected rate limiting/blocking. Stopping to avoid further issues.")
                    print("💡 Try again in 10-15 minutes, or run one playlist at a time.")
                    raise KeyboardInterrupt("Rate limited")

            return False

    except subprocess.TimeoutExpired:
        print(f"⏰ Timeout: {sanitized_filename}")
        return False
    except Exception as e:
        print(f"💥 Error downloading {sanitized_filename}: {e}")
        return False


def _read_youtube_id(info_json: Path) -> str | None:
    """Read YouTube video ID from yt-dlp info json, then delete the sidecar file."""
    if not info_json.exists():
        return None
    try:
        import json

        data = json.loads(info_json.read_text(encoding="utf-8"))
        youtube_id = data.get("id")
        return youtube_id if isinstance(youtube_id, str) and youtube_id else None
    except Exception:
        return None
    finally:
        try:
            info_json.unlink()
        except OSError:
            pass


def main():
    # Ensure we're running in a virtual environment
    if not os.environ.get("VIRTUAL_ENV"):
        print("⚠️  Warning: Not running in a virtual environment!")
        print("   This may cause dependency conflicts.")
        print("   Please activate the venv first:")
        print("     source .venv/bin/activate")
        print("   Or use the wrapper script:")
        print("     ./scripts/run.sh")
        print()

    parser = argparse.ArgumentParser(
        description="Download Spotify playlist tracks as MP3s from YouTube using yt-dlp",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Examples:
  python3 main.py https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M
  python3 main.py 37i9dQZF1DXcBWIGoYBM5M --dry-run
  python3 main.py "My Playlist" --dry-run
  python3 main.py /mnt/ssd/Music/Rivotril --dry-run
  python3 main.py liked --dry-run""",
    )

    parser.add_argument(
        "playlist",
        nargs="?",
        default=None,
        help="Spotify playlist URL, ID, name, folder path with playlist-id.txt, or 'liked' for liked songs",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Show what would be downloaded without actually downloading"
    )
    parser.add_argument(
        "--no-rename", action="store_true", help="Don't auto-rename existing files to clean Spotify format"
    )
    parser.add_argument(
        "--no-link", action="store_true", help="Don't create hard links/copies for songs found in other folders"
    )
    parser.add_argument("--no-metadata", action="store_true", help="Don't fix metadata and artwork (default: enabled)")
    parser.add_argument("--limit", type=int, help="Limit number of tracks to download (useful for testing)")
    parser.add_argument("--export", action="store_true", help="Export track list to text file instead of downloading")
    parser.add_argument(
        "--verify", action="store_true", help="Verify Spotify credentials work without downloading anything"
    )
    parser.add_argument(
        "--full", action="store_true", help="Full re-sync for liked songs (fetch entire library instead of incremental)"
    )

    args = parser.parse_args()
    playlist_input = args.playlist

    # --verify doesn't need playlist argument
    if not args.verify and not playlist_input:
        parser.error("playlist is required unless using --verify")

    dry_run = args.dry_run
    auto_rename = not args.no_rename
    auto_link = not args.no_link
    fix_metadata = not args.no_metadata
    limit = args.limit
    export_only = args.export
    verify_only = args.verify
    full_sync = args.full

    # Handle --verify mode (no playlist needed)
    if verify_only:
        print("🔐 Verifying Spotify credentials...\n")

        # Test with actual playlist fetch to verify Premium access
        print("1️⃣  Testing client credentials with real playlist...")
        try:
            spotify = API(SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET)
            # Use your test playlist
            tracks, name = spotify.get_playlist_tracks("1lEAC324ya2EEQhpIcv0ai")
            print(f"✅ Successfully fetched playlist: {name}")
            print(f"   Found {len(tracks)} tracks")
            client_ok = True
        except Exception as e:
            print(f"❌ Failed to fetch playlist: {e}")
            client_ok = False

        # Verify OAuth credentials (for liked songs)
        print("\n2️⃣  Testing OAuth credentials (liked songs)...")
        try:
            oauth = OAuth(SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET)
            if oauth.token_file.exists():
                oauth.authenticate(interactive=False)
                test_data = oauth.get_liked_songs(limit=1)
                if test_data and test_data.get("items"):
                    print("✅ Can access liked songs")
                else:
                    print("✅ OAuth works (no liked songs in library)")
                oauth_ok = True
            else:
                print("⚠️  No OAuth token found")
                print("   Run: python main.py liked")
                oauth_ok = False
        except Exception as e:
            print(f"❌ OAuth verification failed: {e}")
            oauth_ok = False

        # Summary
        print("\n" + "=" * 50)
        if client_ok and oauth_ok:
            print("✅ All credentials verified! Ready to download.")
        elif client_ok:
            print("✅ Client credentials work (public playlists available)")
            print("⚠️  OAuth not configured (run: python main.py liked)")
        else:
            print("❌ Credentials invalid - check .env or upgrade account to Premium")
        sys.exit(0)

    # Check if it's a folder path with playlist-id.txt
    input_path = Path(playlist_input)
    if input_path.is_dir():
        playlist_id_file = input_path / "playlist-id.txt"
        if playlist_id_file.exists():
            playlist_input = playlist_id_file.read_text().strip()
            print(f"📂 Found playlist ID in folder: {playlist_input}")
        else:
            print(f"❌ No playlist-id.txt found in {input_path}")
            sys.exit(1)

    # Check if yt-dlp is installed (skip in dry-run mode)
    if not dry_run:
        try:
            subprocess.run([*ytdlp_cmd(), "--version"], capture_output=True, check=True)
        except (subprocess.CalledProcessError, FileNotFoundError):
            print("❌ yt-dlp not found. Please install it:")
            print("   ./scripts/setup-venv.sh")
            sys.exit(1)

    # Create music directory if it doesn't exist
    music_dir = Path(MUSIC_DIR)
    music_dir.mkdir(parents=True, exist_ok=True)

    try:
        # Check if user wants liked songs
        is_liked_songs = playlist_input.lower() in ["liked", "saved", "likes"]
        spotify: API | None = None

        if is_liked_songs:
            # Use OAuth for liked songs
            print("🔐 Requesting access to liked songs...")
            oauth = OAuth(SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET)

            # Get liked songs (incremental by default)
            tracks = oauth.get_all_liked_songs(incremental=not full_sync, max_tracks=limit)
            playlist_name = "Liked Songs"

            if not tracks:
                if full_sync:
                    print("❌ No liked songs found")
                else:
                    print("✅ No new liked songs to download")
                sys.exit(0)
        else:
            # Initialize Spotify API with client credentials
            print("🔐 Authenticating with Spotify...")
            spotify = API(SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET)

            # Get playlist tracks
            print("📡 Fetching playlist...")
            tracks, playlist_name = spotify.get_playlist_tracks(playlist_input)

            if not tracks:
                print("❌ No tracks found in playlist")
                sys.exit(1)

        # Apply limit if specified
        if limit:
            print(f"📊 Limiting to first {limit} tracks")
            tracks = tracks[:limit]

        # Export track list if requested
        if export_only:
            export_file = Path(f"{sanitize_filename(playlist_name)}_tracks.txt")
            with open(export_file, "w", encoding="utf-8") as f:
                f.write(f"# {playlist_name}\n")
                f.write(f"# Total tracks: {len(tracks)}\n\n")
                for i, track in enumerate(tracks, 1):
                    f.write(f"{i}. {track['artists']} - {track['name']}\n")
            print(f"📄 Exported track list to: {export_file}")
            sys.exit(0)

        # Create playlist directory
        playlist_dir = Path(MUSIC_DIR) / sanitize_filename(playlist_name)
        playlist_dir.mkdir(parents=True, exist_ok=True)

        # Save playlist ID for easy re-crawling (only for regular playlists)
        if not is_liked_songs and spotify is not None:
            playlist_id_file = playlist_dir / "playlist-id.txt"
            if not playlist_id_file.exists():
                # Extract the clean playlist ID
                clean_playlist_id = spotify.extract_playlist_id(playlist_input)
                playlist_id_file.write_text(clean_playlist_id)
                print(f"💾 Saved playlist ID to: {playlist_id_file}")

        # Clean up any existing temporary files in the Music directory
        print("🧹 Cleaning up old temporary files...")
        cleanup_count = 0
        cleanup_extensions = ["*.webm", "*.webm.part", "*.part", "*.tmp", "*.m4a", "*.f4a", "*.opus"]

        for ext in cleanup_extensions:
            for temp_file in Path(MUSIC_DIR).rglob(ext):
                try:
                    temp_file.unlink()
                    cleanup_count += 1
                except Exception:
                    pass

        if cleanup_count > 0:
            print(f"   Removed {cleanup_count} temporary files")

        # Download tracks
        if dry_run:
            print(f"\n🔍 DRY RUN - Would download to: {playlist_dir}")
        else:
            print(f"\n🎵 Starting downloads to: {playlist_dir}")
        print("=" * 50)

        successful = 0
        failed = 0
        skipped = 0

        for i, track in enumerate(tracks, 1):
            print(f"\n[{i}/{len(tracks)}] ", end="")
            result = download_track(
                track, playlist_dir, MUSIC_DIR, spotify, dry_run, auto_rename, auto_link, fix_metadata
            )
            if result == "skipped":
                skipped += 1
            elif result:
                successful += 1
            else:
                failed += 1

        # Summary
        print("\n" + "=" * 50)
        print("🎉 Download Summary:")
        print(f"   ✅ Successful: {successful}")
        if skipped > 0:
            print(f"   ⏭️  Skipped (exists): {skipped}")
        print(f"   ❌ Failed: {failed}")
        print(f"   📁 Location: {playlist_dir}")

        if not dry_run and (successful + skipped) > 0:
            print("\n💡 Tip: Run Navidrome library scan to index new files")

        # Generate M3U playlist file for Navidrome (only if not dry-run)
        if not dry_run:
            m3u_file = Path(MUSIC_DIR) / f"{sanitize_filename(playlist_name)}.m3u"
            try:
                with open(m3u_file, "w", encoding="utf-8") as f:
                    f.write(f"# {playlist_name}\n")
                    # Find all MP3 files in the playlist directory
                    for mp3_file in playlist_dir.glob("*.mp3"):
                        # Write relative path from MUSIC_DIR
                        relative_path = mp3_file.relative_to(Path(MUSIC_DIR))
                        f.write(f"{relative_path}\n")
                print(f"📋 Generated M3U playlist: {m3u_file}")

                # Trigger Navidrome rescan
                rescan_file = Path(MUSIC_DIR) / ".rescan"
                rescan_file.touch()
                print(f"🔄 Triggered Navidrome rescan: {rescan_file}")

            except Exception as e:
                print(f"⚠️  Failed to generate M3U or trigger rescan: {e}")

    except KeyboardInterrupt:
        print("\n⏹️  Download interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"💥 Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
