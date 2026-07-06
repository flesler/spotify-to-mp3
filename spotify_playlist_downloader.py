#!/usr/bin/env python3
"""
Spotify Playlist to MP3 Downloader
Downloads tracks from a Spotify playlist as MP3s from YouTube using yt-dlp.
"""

import argparse
import base64
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import requests
from dotenv import load_dotenv
from mutagen.id3 import APIC, ID3, TALB, TDRC, TIT2, TPE1, TPE2
from mutagen.id3._util import ID3NoHeaderError
from mutagen.mp3 import MP3

# Load environment variables from .env file if it exists
load_dotenv()

# Spotify API credentials (can be overridden by environment variables)
SPOTIFY_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID", "")
SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET", "")

# Download directory (can be overridden by environment variable)
MUSIC_DIR = os.getenv("MUSIC_DIR", "/mnt/ssd/Music")

# Download quality
DOWNLOAD_QUALITY = os.getenv("DOWNLOAD_QUALITY", "192K")

# Enable audio fingerprinting
ENABLE_AUDIO_FINGERPRINT = os.getenv("ENABLE_AUDIO_FINGERPRINT", "false").lower() == "true"

class SpotifyAPI:
    def __init__(self, client_id, client_secret):
        self.client_id = client_id
        self.client_secret = client_secret
        self.access_token = None
        self._get_access_token()

    def _get_access_token(self):
        """Get Spotify access token using client credentials flow"""
        url = "https://accounts.spotify.com/api/token"

        # Encode client credentials
        credentials = f"{self.client_id}:{self.client_secret}"
        encoded_credentials = base64.b64encode(credentials.encode()).decode()

        headers = {
            "Authorization": f"Basic {encoded_credentials}",
            "Content-Type": "application/x-www-form-urlencoded"
        }

        data = {"grant_type": "client_credentials"}

        response = requests.post(url, headers=headers, data=data)
        response.raise_for_status()

        token_data = response.json()
        self.access_token = token_data["access_token"]

    def _make_request(self, url):
        """Make authenticated request to Spotify API"""
        headers = {"Authorization": f"Bearer {self.access_token}"}
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        return response.json()

    def extract_playlist_id(self, playlist_input):
        """Extract playlist ID from URL or return as-is if already an ID"""
        if playlist_input.startswith("http"):
            # Extract from URL
            if "playlist/" in playlist_input:
                return playlist_input.split("playlist/")[1].split("?")[0]
            else:
                parsed = urlparse(playlist_input)
                path_parts = parsed.path.split("/")
                if "playlist" in path_parts:
                    idx = path_parts.index("playlist")
                    if idx + 1 < len(path_parts):
                        return path_parts[idx + 1]
        return playlist_input

    def get_playlist_tracks(self, playlist_id):
        """Get all tracks from a Spotify playlist"""
        playlist_id = self.extract_playlist_id(playlist_id)

        # Get playlist info
        playlist_url = f"https://api.spotify.com/v1/playlists/{playlist_id}"
        playlist_data = self._make_request(playlist_url)
        playlist_name = playlist_data["name"]

        print(f"📋 Playlist: {playlist_name}")
        print(f"🔗 ID: {playlist_id}")

        # Get tracks with pagination
        tracks = []
        url = f"https://api.spotify.com/v1/playlists/{playlist_id}/tracks"

        while url:
            data = self._make_request(url)

            for item in data["items"]:
                if item["track"] and item["track"]["type"] == "track":
                    track = item["track"]
                    artists = ", ".join([artist["name"] for artist in track["artists"]])
                    tracks.append({
                        "id": track["id"],
                        "name": track["name"],
                        "artists": artists,
                        "duration_ms": track["duration_ms"],
                        "popularity": track["popularity"],
                        "album": track.get("album", {})  # Include full album data
                    })

            url = data.get("next")

        print(f"🎵 Found {len(tracks)} tracks")
        return tracks, playlist_name

def sanitize_filename(filename):
    """Remove/replace characters that are problematic for filenames"""
    # Replace problematic characters
    filename = re.sub(r'[<>:"/\\|?*]', '', filename)
    filename = re.sub(r'[^\w\s\-_\(\)\[\].]', '', filename)
    filename = re.sub(r'\s+', ' ', filename).strip()
    return filename

def download_album_art(track):
    """Download album artwork using data already from playlist API"""
    try:
        album = track.get("album", {})
        images = album.get("images", [])

        if not images:
            print(f"   ⚠️  No album images found")
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

def fix_mp3_metadata_smart(file_path, track):
    """Intelligently fix MP3 metadata only if needed"""
    try:
        audio = MP3(file_path)
        if audio.tags is None:
            # No tags at all, need everything
            album_art_data, album_info = download_album_art(track)
            set_mp3_metadata(file_path, track, album_art_data, album_info)
            return

        # Check what's missing
        title = audio.tags.get("TIT2")
        artist = audio.tags.get("TPE1")
        album = audio.tags.get("TALB")
        artwork = audio.tags.getall("APIC")  # Get all APIC frames

        needs_metadata = (
            not title or str(title[0]) != track["name"] or
            not artist or str(artist[0]) != track["artists"] or
            not album
        )

        needs_artwork = not artwork or len(artwork) == 0

        if needs_metadata or needs_artwork:
            album_art_data, album_info = None, None
            if needs_artwork:
                album_art_data, album_info = download_album_art(track)
            else:
                album_info = track.get("album", {})
            set_mp3_metadata(file_path, track, album_art_data, album_info)
        else:
            print(f"   ✅ Metadata already complete")

    except Exception as e:
        print(f"   ⚠️  Metadata check failed: {e}")
        # Fallback to full update
        album_art_data, album_info = download_album_art(track)
        set_mp3_metadata(file_path, track, album_art_data, album_info)

def set_mp3_metadata(file_path, track, album_art_data=None, album_info=None):
    """Set proper ID3 tags on MP3 file"""
    try:
        # Load the MP3 file
        audio = MP3(file_path)

        # Ensure ID3 tags exist
        if audio.tags is None:
            audio.add_tags()

        # Update/set basic metadata (overwrite existing)
        audio.tags.setall("TIT2", [TIT2(encoding=3, text=track["name"])])  # Title
        audio.tags.setall("TPE1", [TPE1(encoding=3, text=track["artists"])])  # Artist
        audio.tags.setall("TPE2", [TPE2(encoding=3, text=track["artists"])])  # Album Artist

        # Set album info if available
        if album_info:
            album_name = album_info.get("name", "Unknown Album")
            audio.tags.setall("TALB", [TALB(encoding=3, text=album_name)])  # Album

            # Release date
            release_date = album_info.get("release_date", "")
            if release_date:
                year = release_date.split("-")[0]
                audio.tags.setall("TDRC", [TDRC(encoding=3, text=year)])  # Year

        # Embed album artwork (replace existing)
        if album_art_data:
            # Remove existing artwork
            audio.tags.delall("APIC")
            # Add new artwork
            audio.tags.add(APIC(
                encoding=3,  # UTF-8
                mime='image/jpeg',  # JPEG image
                type=3,  # Cover (front)
                desc='Cover',
                data=album_art_data
            ))

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

def check_if_track_exists(artists, title, base_music_dir, auto_rename=True):
    """Check if a track already exists anywhere in the music directory"""
    # Clean up artists and title for better matching
    clean_artists = sanitize_filename(artists).lower()
    clean_title = sanitize_filename(title).lower()
    clean_spotify_name = sanitize_filename(f"{artists} - {title}")

    # Search recursively in all subdirectories
    for pattern in [clean_artists, clean_title]:
        if len(pattern) < 3:  # Skip very short patterns
            continue
        for mp3_file in Path(base_music_dir).rglob("*.mp3"):
            filename_lower = mp3_file.name.lower()
            # Check if both artist and title appear in the filename
            if clean_artists in filename_lower and clean_title in filename_lower:
                # Check if it's already in clean format
                current_name = mp3_file.stem  # filename without extension
                if current_name == clean_spotify_name:
                    return mp3_file  # Already clean, no rename needed

                # Auto-rename to clean Spotify format
                if auto_rename:
                    new_filename = clean_spotify_name + ".mp3"
                    new_path = mp3_file.parent / new_filename

                    # Avoid overwriting existing clean files
                    if not new_path.exists():
                        try:
                            mp3_file.rename(new_path)
                            print(f"🔄 Renamed: {mp3_file.name} → {new_filename}")
                            return new_path
                        except Exception as e:
                            print(f"⚠️  Rename failed: {e}")
                            return mp3_file

                return mp3_file

    return None

def download_track(track, playlist_dir, base_music_dir, spotify_api, dry_run=False, auto_rename=True, auto_link=True, fix_metadata=True):
    """Download a single track using yt-dlp"""
    artists = track["artists"]
    title = track["name"]

    # Create search query
    search_query = f"{artists} - {title}"
    sanitized_filename = sanitize_filename(f"{artists} - {title}")

    # Check if file already exists anywhere
    existing_file = check_if_track_exists(artists, title, base_music_dir, auto_rename)
    if existing_file:
        # Check if it's already in the target playlist directory
        target_path = Path(playlist_dir) / f"{sanitized_filename}.mp3"
        if existing_file.parent != Path(playlist_dir) and auto_link:
            # File exists elsewhere, create hard link in playlist directory
            if not target_path.exists():
                if dry_run:
                    print(f"🔗 Would link: {existing_file.relative_to(base_music_dir)} → {target_path.name}")
                    if fix_metadata:
                        print(f"   🎨 Would fix metadata")
                    return True
                try:
                    target_path.hardlink_to(existing_file)
                    print(f"🔗 Linked: {existing_file.relative_to(base_music_dir)} → {target_path.name}")

                    # Fix metadata on the linked file
                    if fix_metadata:
                        fix_mp3_metadata_smart(target_path, track)

                    return True
                except Exception as e:
                    # Fall back to copy if hard link fails
                    try:
                        import shutil
                        shutil.copy2(existing_file, target_path)
                        print(f"📋 Copied: {existing_file.relative_to(base_music_dir)} → {target_path.name}")

                        # Fix metadata on the copied file
                        if fix_metadata:
                            fix_mp3_metadata_smart(target_path, track)

                        return True
                    except Exception as e2:
                        print(f"⚠️  Link/copy failed: {e2}")
            else:
                print(f"⏭️  Already in playlist: {target_path.name}")

                # Still fix metadata if needed
                if fix_metadata and not dry_run:
                    fix_mp3_metadata_smart(target_path, track)
                elif fix_metadata and dry_run:
                    print(f"   🎨 Would fix metadata")

                return True
        else:
            print(f"⏭️  Exists: {existing_file.relative_to(base_music_dir)}")

            # Fix metadata on existing file
            if fix_metadata and not dry_run:
                fix_mp3_metadata_smart(existing_file, track)
            elif fix_metadata and dry_run:
                print(f"   🎨 Would fix metadata")

            return True

    # Check if file exists in the target playlist directory
    output_path = Path(playlist_dir) / f"{sanitized_filename}.mp3"
    if output_path.exists():
        print(f"⏭️  Exists in playlist dir: {sanitized_filename}")

        # Fix metadata if needed
        if fix_metadata and not dry_run:
            fix_mp3_metadata_smart(output_path, track)
        elif fix_metadata and dry_run:
            print(f"   🎨 Would fix metadata")

        return True

    if dry_run:
        print(f"🎵 {search_query}")
        return True

    print(f"🔍 Searching: {search_query}")

    try:
        # yt-dlp command to search and download from YouTube
        cmd = [
            "/home/pi/.local/bin/yt-dlp",
            f"ytsearch1:{search_query}",  # Search for 1 result
            "--extract-audio",
            "--audio-format", "mp3",
            "--audio-quality", "192K",
            "--output", str(playlist_dir) + "/" + sanitized_filename + ".%(ext)s",
            "--no-playlist",
            "--ignore-errors",
            "--sleep-interval", "2",  # Sleep 2 seconds between downloads
            "--max-sleep-interval", "5",  # Random sleep up to 5 seconds
            "--retries", "3",  # Retry failed downloads 3 times
            "--fragment-retries", "3",  # Retry failed fragments
            "--abort-on-unavailable-fragment",  # Skip corrupted videos
        ]

        # Run yt-dlp
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)

        if result.returncode == 0:
            print(f"✅ Downloaded: {sanitized_filename}")

            # Add proper metadata and album art
            output_path = Path(playlist_dir) / f"{sanitized_filename}.mp3"
            if output_path.exists() and fix_metadata:
                # Smart metadata fixing
                fix_mp3_metadata_smart(output_path, track)

            return True
        else:
            # Clean up any partial downloads - be more aggressive with cleanup
            cleanup_patterns = [
                f"{sanitized_filename}.*",  # Exact filename matches
                f"*{sanitized_filename.split(' - ')[-1]}*"  # Match by song title
            ]

            for pattern in cleanup_patterns:
                partial_files = list(Path(playlist_dir).glob(pattern))
                for partial_file in partial_files:
                    # Remove any non-mp3 files that might be leftover
                    if (partial_file.suffix in ['.part', '.webm', '.m4a', '.tmp', '.f4a', '.opus'] or
                        partial_file.name.endswith('.webm.part')):
                        try:
                            partial_file.unlink()
                            print(f"   🗑️  Cleaned: {partial_file.name}")
                        except:
                            pass

            print(f"❌ Failed: {sanitized_filename}")
            # Only show first line of error to avoid spam
            error_lines = result.stderr.strip().split('\n')
            if error_lines:
                error_msg = error_lines[0]
                print(f"   Error: {error_msg}")

                # Check for rate limiting / blocking indicators
                blocking_indicators = [
                    "No such file or directory",
                    "Unable to rename file",
                    "HTTP Error 429",
                    "Too Many Requests",
                    "Sign in to confirm you're not a bot",
                    "This video is not available",
                    "Private video",
                    "Video unavailable"
                ]

                if any(indicator in error_msg for indicator in blocking_indicators[:4]):  # Only critical errors
                    print(f"\n🛑 Detected rate limiting/blocking. Stopping to avoid further issues.")
                    print(f"💡 Try again in 10-15 minutes, or run one playlist at a time.")
                    raise KeyboardInterrupt("Rate limited")

            return False

    except subprocess.TimeoutExpired:
        print(f"⏰ Timeout: {sanitized_filename}")
        return False
    except Exception as e:
        print(f"💥 Error downloading {sanitized_filename}: {e}")
        return False

def main():
    parser = argparse.ArgumentParser(
        description="Download Spotify playlist tracks as MP3s from YouTube using yt-dlp",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Examples:
  python3 spotify_playlist_downloader.py https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M
  python3 spotify_playlist_downloader.py 37i9dQZF1DXcBWIGoYBM5M --dry-run
  python3 spotify_playlist_downloader.py "My Playlist" --dry-run
  python3 spotify_playlist_downloader.py /mnt/ssd/Music/Rivotril --dry-run
  python3 spotify_playlist_downloader.py liked --dry-run"""
    )

    parser.add_argument("playlist", help="Spotify playlist URL, ID, name, folder path with playlist-id.txt, or 'liked' for liked songs")
    parser.add_argument("--dry-run", action="store_true",
                       help="Show what would be downloaded without actually downloading")
    parser.add_argument("--no-rename", action="store_true",
                       help="Don't auto-rename existing files to clean Spotify format")
    parser.add_argument("--no-link", action="store_true",
                       help="Don't create hard links/copies for songs found in other folders")
    parser.add_argument("--no-metadata", action="store_true",
                       help="Don't fix metadata and artwork (default: enabled)")

    args = parser.parse_args()
    playlist_input = args.playlist
    dry_run = args.dry_run
    auto_rename = not args.no_rename
    auto_link = not args.no_link
    fix_metadata = not args.no_metadata

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
            subprocess.run(["/home/pi/.local/bin/yt-dlp", "--version"], capture_output=True, check=True)
        except (subprocess.CalledProcessError, FileNotFoundError):
            print("❌ yt-dlp not found. Please install it:")
            print("   pip3 install yt-dlp")
            sys.exit(1)

    # Create music directory if it doesn't exist
    music_dir = Path(MUSIC_DIR)
    music_dir.mkdir(parents=True, exist_ok=True)

    try:
        # Check if user wants liked songs
        is_liked_songs = playlist_input.lower() in ['liked', 'saved', 'likes']

        if is_liked_songs:
            # Use OAuth for liked songs
            print("🔐 Requesting access to liked songs...")
            from spotify_oauth import SpotifyOAuth
            oauth = SpotifyOAuth(SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET)

            # Get all liked songs
            tracks = oauth.get_all_liked_songs()
            playlist_name = "Liked Songs"

            if not tracks:
                print("❌ No liked songs found")
                sys.exit(1)
        else:
            # Initialize Spotify API with client credentials
            print("🔐 Authenticating with Spotify...")
            spotify = SpotifyAPI(SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET)

            # Get playlist tracks
            print("📡 Fetching playlist...")
            tracks, playlist_name = spotify.get_playlist_tracks(playlist_input)

            if not tracks:
                print("❌ No tracks found in playlist")
                sys.exit(1)

        # Create playlist directory
        playlist_dir = Path(MUSIC_DIR) / sanitize_filename(playlist_name)
        playlist_dir.mkdir(parents=True, exist_ok=True)

        # Save playlist ID for easy re-crawling (only for regular playlists)
        if not is_liked_songs:
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
                except:
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

        for i, track in enumerate(tracks, 1):
            print(f"\n[{i}/{len(tracks)}] ", end="")
            if download_track(track, playlist_dir, MUSIC_DIR, spotify, dry_run, auto_rename, auto_link, fix_metadata):
                successful += 1
            else:
                failed += 1

        # Summary
        print("\n" + "=" * 50)
        print(f"🎉 Download Summary:")
        print(f"   ✅ Successful: {successful}")
        print(f"   ❌ Failed: {failed}")
        print(f"   📁 Location: {playlist_dir}")

        # Generate M3U playlist file for Navidrome (only if not dry-run)
        if not dry_run:
            m3u_file = Path(MUSIC_DIR) / f"{sanitize_filename(playlist_name)}.m3u"
            try:
                with open(m3u_file, 'w', encoding='utf-8') as f:
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
