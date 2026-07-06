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
from api import API, looks_like_playlist_ref
from library import LibraryIndex, get_txxx, link_track
from spotify_meta import cache_for_mp3
from oauth import OAuth
from ytdlp_util import download_with_search_fallback, is_rate_limited, ytdlp_cmd

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


def fix_mp3_metadata_smart(file_path, track, youtube_id=None, library_index: LibraryIndex | None = None):
    """Intelligently fix MP3 metadata only if needed"""
    try:
        audio = MP3(file_path)
        if audio.tags is None:
            album_art_data, album_info = download_album_art(track)
            set_mp3_metadata(file_path, track, album_art_data, album_info, youtube_id=youtube_id)
        else:
            title = audio.tags.get("TIT2")
            artist = audio.tags.get("TPE1")
            album = audio.tags.get("TALB")
            artwork = audio.tags.getall("APIC")
            spotify_id = get_txxx(audio.tags, "SPOTIFY_ID")
            stored_youtube_id = get_txxx(audio.tags, "YOUTUBE_ID")

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
        album_art_data, album_info = download_album_art(track)
        set_mp3_metadata(file_path, track, album_art_data, album_info, youtube_id=youtube_id)

    if library_index:
        library_index.note_file(Path(file_path), track.get("id"), youtube_id)

    if track.get("id"):
        try:
            cache_for_mp3(Path(file_path), track)
        except Exception as e:
            print(f"   ⚠️  Spotify metadata cache failed: {e}")


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


def check_if_track_exists(
    artists, title, base_music_dir, auto_rename=True, duration_ms=None, spotify_id=None, library_index=None
):
    """Check if a track already exists anywhere in the music directory

    Uses multiple strategies:
    1. Spotify ID in ID3 tags (fast, exact)
    2. Exact filename matching
    3. Duration matching (±5s tolerance)
    4. Fuzzy filename matching (handles remixes, feat., etc.)
    """
    clean_artists = sanitize_filename(artists).lower()
    clean_title = sanitize_filename(title).lower()
    clean_spotify_name = sanitize_filename(f"{artists} - {title}")
    expected_filename = f"{clean_spotify_name}.mp3"

    if spotify_id and library_index:
        match = library_index.find_by_spotify_id(spotify_id)
        if match:
            path = _finalize_match(match, clean_spotify_name, auto_rename, library_index)
            return path, "spotify id"

    # Fast path: exact filename anywhere in library (single rglob pattern)
    for mp3_file in Path(base_music_dir).rglob(expected_filename):
        if mp3_file.is_file():
            result = _finalize_match(mp3_file, clean_spotify_name, auto_rename, library_index)
            if library_index and spotify_id:
                library_index.note_file(result, spotify_id=spotify_id)
            return result, "filename"

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
    result = _finalize_match(best[0], clean_spotify_name, auto_rename, library_index)
    if library_index and spotify_id:
        library_index.note_file(result, spotify_id=spotify_id)
    reason = "fuzzy match" if best[1] == "fuzzy" else "filename"
    return result, reason


def _finalize_match(mp3_file, clean_spotify_name, auto_rename, library_index=None):
    """Rename to clean format if needed, then return the file path."""
    current_name = mp3_file.stem
    if current_name == clean_spotify_name:
        return mp3_file

    if auto_rename:
        new_filename = clean_spotify_name + ".mp3"
        new_path = mp3_file.parent / new_filename
        if not new_path.exists():
            try:
                old_key = library_index._rel_key(mp3_file) if library_index else None
                mp3_file.rename(new_path)
                print(f"🔄 Renamed: {mp3_file.name} → {new_filename}")
                if library_index and old_key:
                    library_index.rename_file(old_key, new_path)
                return new_path
            except Exception as e:
                print(f"⚠️  Rename failed: {e}")
                return mp3_file

    return mp3_file


def download_track(
    track,
    playlist_dir,
    base_music_dir,
    spotify_api,
    dry_run=False,
    auto_rename=True,
    auto_link=True,
    fix_metadata=True,
    library_index: LibraryIndex | None = None,
):
    """Download a single track using yt-dlp"""
    artists = track["artists"]
    title = track["name"]

    # Create search query
    search_query = f"{artists} - {title}"
    sanitized_filename = sanitize_filename(f"{artists} - {title}")

    # Check if file already exists anywhere
    match = check_if_track_exists(
        artists,
        title,
        base_music_dir,
        auto_rename,
        track.get("duration_ms"),
        spotify_id=track.get("id"),
        library_index=library_index,
    )
    if match:
        existing_file, match_reason = match
        rel_path = existing_file.relative_to(base_music_dir)
        # Check if it's already in the target playlist directory
        target_path = Path(playlist_dir) / f"{sanitized_filename}.mp3"
        if existing_file.parent != Path(playlist_dir) and auto_link:
            # File exists elsewhere, create hard link in playlist directory
            target_rel = str(target_path.relative_to(base_music_dir))
            already_linked = library_index and track.get("id") and library_index.has_path(track["id"], target_rel)
            if not target_path.exists() and not already_linked:
                if dry_run:
                    print(f"⏭️  Skipped ({match_reason}): {rel_path}")
                    print(f"🔗 Would link → {target_path.name}")
                    if fix_metadata:
                        print("   🎨 Would fix metadata")
                    return "skipped"
                try:
                    link_track(existing_file, target_path)
                    print(f"⏭️  Skipped ({match_reason}): {rel_path}")
                    print(f"🔗 Linked → {target_path.name}")
                    if library_index and track.get("id"):
                        library_index.note_file(target_path, track.get("id"))

                    if fix_metadata:
                        fix_mp3_metadata_smart(target_path, track, library_index=library_index)

                    return "skipped"
                except Exception:
                    try:
                        import shutil

                        shutil.copy2(existing_file, target_path)
                        print(f"⏭️  Skipped ({match_reason}): {rel_path}")
                        print(f"📋 Copied → {target_path.name}")

                        if library_index and track.get("id"):
                            library_index.note_file(target_path, track.get("id"))

                        if fix_metadata:
                            fix_mp3_metadata_smart(target_path, track, library_index=library_index)

                        return "skipped"
                    except Exception as e2:
                        print(f"⚠️  Link/copy failed: {e2}")
            else:
                print(f"⏭️  Skipped ({match_reason}): {rel_path} (already in playlist)")

                # Still fix metadata if needed
                if fix_metadata and not dry_run:
                    fix_mp3_metadata_smart(target_path, track, library_index=library_index)
                elif fix_metadata and dry_run:
                    print("   🎨 Would fix metadata")

                return "skipped"
        else:
            print(f"⏭️  Skipped ({match_reason}): {rel_path}")

            # Fix metadata on existing file
            if fix_metadata and not dry_run:
                fix_mp3_metadata_smart(existing_file, track, library_index=library_index)
            elif fix_metadata and dry_run:
                print("   🎨 Would fix metadata")

            return "skipped"

    # Check if file exists in the target playlist directory
    output_path = Path(playlist_dir) / f"{sanitized_filename}.mp3"
    if output_path.exists():
        print(f"⏭️  Skipped (in playlist dir): {sanitized_filename}.mp3")

        # Fix metadata if needed
        if fix_metadata and not dry_run:
            fix_mp3_metadata_smart(output_path, track, library_index=library_index)
        elif fix_metadata and dry_run:
            print("   🎨 Would fix metadata")

        return "skipped"

    if dry_run:
        print(f"🎵 {search_query}")
        return True

    print(f"🔍 Searching: {search_query}")

    try:
        ok, youtube_id, error_msg = download_with_search_fallback(
            search_query, Path(playlist_dir), sanitized_filename, DOWNLOAD_QUALITY
        )

        if ok:
            output_path = Path(playlist_dir) / f"{sanitized_filename}.mp3"
            print(f"✅ Downloaded: {sanitized_filename}")

            if output_path.exists() and fix_metadata:
                fix_mp3_metadata_smart(output_path, track, youtube_id=youtube_id, library_index=library_index)

            return True

        print(f"❌ Failed: {sanitized_filename}")
        if error_msg:
            print(f"   Error: {error_msg}")

        if is_rate_limited(error_msg):
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

    is_liked_songs = playlist_input.lower() in ["liked", "saved", "likes"]
    if not is_liked_songs and not looks_like_playlist_ref(playlist_input):
        print(f"🔍 Resolving playlist name: {playlist_input}")
        oauth = OAuth(SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET)
        try:
            playlist_id, resolved_name = oauth.resolve_playlist_by_name(playlist_input)
            print(f"✅ Matched playlist: {resolved_name} ({playlist_id})")
            playlist_input = playlist_id
        except Exception as e:
            print(f"❌ {e}")
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

    library_index = LibraryIndex(music_dir)
    library_index.build()

    try:
        # Check if user wants liked songs
        spotify: API | None = None

        if is_liked_songs:
            # Use OAuth for liked songs
            print("🔐 Requesting access to liked songs...")
            oauth = OAuth(SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET)

            # Get liked songs (incremental by default)
            tracks = oauth.get_all_liked_songs(
                incremental=not full_sync, max_tracks=limit, downloaded_ids=library_index.spotify_ids()
            )
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
            print(f"\n[{i}/{len(tracks)}] {track['artists']} - {track['name']}")
            result = download_track(
                track,
                playlist_dir,
                MUSIC_DIR,
                spotify,
                dry_run,
                auto_rename,
                auto_link,
                fix_metadata,
                library_index=library_index,
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
    finally:
        library_index.save()


if __name__ == "__main__":
    main()
