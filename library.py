"""Index MP3 files by Spotify/YouTube IDs. JSON cache is authoritative when mtime matches."""

import json
import time
from pathlib import Path

from mutagen.mp3 import MP3

INDEX_FILENAME = "library-index.json"


def get_txxx(tags, desc: str) -> str | None:
    if tags is None:
        return None
    for frame in tags.getall("TXXX"):
        if frame.desc == desc:
            return str(frame.text[0])
    return None


def read_ids_from_mp3(path: Path) -> tuple[str | None, str | None]:
    try:
        tags = MP3(path).tags
        return get_txxx(tags, "SPOTIFY_ID"), get_txxx(tags, "YOUTUBE_ID")
    except Exception:
        return None, None


class LibraryIndex:
    """Maps Spotify IDs to file paths. Trusts library-index.json when mtime is fresh."""

    def __init__(self, music_dir: Path):
        self.music_dir = Path(music_dir).resolve()
        self.cache_file = self.music_dir / INDEX_FILENAME
        self.by_spotify_id: dict[str, str] = {}
        self._entries: dict[str, dict] = {}
        self._dirty = False

    def _rel_key(self, path: Path) -> str:
        return str(path.resolve().relative_to(self.music_dir))

    def build(self):
        cached = self._load_cache()
        entries: dict[str, dict] = {}
        scanned = 0

        for mp3_file in self.music_dir.rglob("*.mp3"):
            if not mp3_file.is_file():
                continue

            scanned += 1
            key = self._rel_key(mp3_file)
            mtime = mp3_file.stat().st_mtime
            cached_entry = cached.get(key)

            if cached_entry and cached_entry.get("mtime") == mtime:
                spotify_id = cached_entry.get("spotify_id")
                youtube_id = cached_entry.get("youtube_id")
            else:
                spotify_id, youtube_id = read_ids_from_mp3(mp3_file)

            entries[key] = {"mtime": mtime, "spotify_id": spotify_id, "youtube_id": youtube_id}

        self._entries = entries
        self._rebuild_spotify_map()
        if entries != cached:
            self._dirty = True
        print(f"📇 Library index: {len(self.by_spotify_id)} tagged / {scanned} mp3s")

    def spotify_ids(self) -> set[str]:
        return set(self.by_spotify_id.keys())

    def find_by_spotify_id(self, spotify_id: str) -> Path | None:
        rel_key = self.by_spotify_id.get(spotify_id)
        if not rel_key:
            return None
        path = self.music_dir / rel_key
        if path.exists():
            return path
        return None

    def note_file(self, path: Path, spotify_id: str | None = None, youtube_id: str | None = None):
        if not path.exists():
            return

        if spotify_id is None or youtube_id is None:
            read_spotify, read_youtube = read_ids_from_mp3(path)
            spotify_id = spotify_id or read_spotify
            youtube_id = youtube_id or read_youtube

        key = self._rel_key(path)
        entry = {"mtime": path.stat().st_mtime, "spotify_id": spotify_id, "youtube_id": youtube_id}
        if self._entries.get(key) == entry:
            return

        self._entries[key] = entry
        self._rebuild_spotify_map()
        self._dirty = True

    def rename_file(self, old_key: str, new_path: Path):
        entry = self._entries.pop(old_key, None)
        if entry is None:
            self.note_file(new_path)
            return

        new_key = self._rel_key(new_path)
        entry = {**entry, "mtime": new_path.stat().st_mtime}
        self._entries[new_key] = entry
        self._rebuild_spotify_map()
        self._dirty = True

    def save(self):
        if not self._dirty:
            return
        self._write_cache()
        self._dirty = False

    def _rebuild_spotify_map(self):
        self.by_spotify_id = {}
        for key, entry in self._entries.items():
            spotify_id = entry.get("spotify_id")
            if spotify_id:
                self.by_spotify_id[spotify_id] = key

    def _load_cache(self) -> dict[str, dict]:
        try:
            with open(self.cache_file, "r") as f:
                return json.load(f).get("entries", {})
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def _write_cache(self):
        self.music_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.cache_file.with_suffix(".json.tmp")
        with open(tmp, "w") as f:
            json.dump({"entries": self._entries, "updated_at": time.time()}, f)
        tmp.replace(self.cache_file)
