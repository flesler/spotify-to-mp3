"""Index MP3 files by Spotify ID. JSON cache is authoritative when paths[0] mtime matches."""

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
    """Global map: spotify_id -> {paths[], youtube_id, mtime}. paths[0] is the primary file."""

    def __init__(self, music_dir: Path):
        self.music_dir = Path(music_dir).resolve()
        self.cache_file = self.music_dir / INDEX_FILENAME
        self.tracks: dict[str, dict] = {}
        self.untagged: dict[str, dict] = {}
        self._dirty = False

    def _rel_key(self, path: Path) -> str:
        return str(path.relative_to(self.music_dir))

    def _sort_paths(self, paths: list[str]) -> list[str]:
        return sorted(set(paths), key=lambda p: ((self.music_dir / p).is_symlink(), p))

    def build(self):
        cached = self._load_cache()
        cached_tracks = cached.get("tracks", {})
        cached_untagged = cached.get("untagged", {})

        by_spotify: dict[str, list[str]] = {}
        untagged_paths: list[str] = []
        scanned = 0

        for mp3_file in self.music_dir.rglob("*.mp3"):
            if not mp3_file.is_file():
                continue
            scanned += 1
            key = self._rel_key(mp3_file)
            spotify_id, _ = read_ids_from_mp3(mp3_file.resolve())
            if spotify_id:
                by_spotify.setdefault(spotify_id, []).append(key)
            else:
                untagged_paths.append(key)

        tracks: dict[str, dict] = {}
        for spotify_id, paths in by_spotify.items():
            paths = self._sort_paths(paths)
            primary = self.music_dir / paths[0]
            mtime = primary.resolve().stat().st_mtime
            cached_track = cached_tracks.get(spotify_id, {})
            cached_paths = cached_track.get("paths", [])

            if cached_paths and cached_paths[0] == paths[0] and cached_track.get("mtime") == mtime:
                youtube_id = cached_track.get("youtube_id")
            else:
                _, youtube_id = read_ids_from_mp3(primary.resolve())

            tracks[spotify_id] = {"paths": paths, "mtime": mtime, "youtube_id": youtube_id}

        untagged: dict[str, dict] = {}
        for key in untagged_paths:
            path = self.music_dir / key
            mtime = path.stat().st_mtime
            cached_entry = cached_untagged.get(key)
            if cached_entry and cached_entry.get("mtime") == mtime:
                youtube_id = cached_entry.get("youtube_id")
            else:
                _, youtube_id = read_ids_from_mp3(path.resolve())
            untagged[key] = {"mtime": mtime, "youtube_id": youtube_id}

        self.tracks = tracks
        self.untagged = untagged
        if {"tracks": tracks, "untagged": untagged} != {"tracks": cached_tracks, "untagged": cached_untagged}:
            self._dirty = True
        print(f"📇 Library index: {len(tracks)} tracks / {scanned} mp3s")

    def spotify_ids(self) -> set[str]:
        return set(self.tracks.keys())

    def find_by_spotify_id(self, spotify_id: str) -> Path | None:
        paths = self.tracks.get(spotify_id, {}).get("paths", [])
        for key in paths:
            path = self.music_dir / key
            if path.exists():
                return path.resolve()
        return None

    def has_path(self, spotify_id: str, rel_path: str) -> bool:
        return rel_path in self.tracks.get(spotify_id, {}).get("paths", [])

    def note_file(self, path: Path, spotify_id: str | None = None, youtube_id: str | None = None):
        if not path.exists():
            return

        key = self._rel_key(path)
        if spotify_id is None or youtube_id is None:
            read_spotify, read_youtube = read_ids_from_mp3(path.resolve())
            spotify_id = spotify_id or read_spotify
            youtube_id = youtube_id or read_youtube

        if not spotify_id:
            entry = {"mtime": path.stat().st_mtime, "youtube_id": youtube_id}
            if self.untagged.get(key) == entry:
                return
            self.untagged[key] = entry
            self._dirty = True
            return

        track = self.tracks.get(spotify_id, {})
        paths = list(track.get("paths", []))
        if key not in paths:
            paths.append(key)

        primary = self.music_dir / paths[0]
        mtime = primary.resolve().stat().st_mtime
        entry = {"paths": paths, "mtime": mtime, "youtube_id": youtube_id or track.get("youtube_id")}
        if self.tracks.get(spotify_id) == entry:
            return
        self.tracks[spotify_id] = entry
        self._dirty = True

    def rename_file(self, old_key: str, new_path: Path):
        new_key = self._rel_key(new_path)
        mtime = new_path.stat().st_mtime

        for spotify_id, track in self.tracks.items():
            if old_key not in track.get("paths", []):
                continue
            paths = [new_key if p == old_key else p for p in track["paths"]]
            primary_mtime = mtime if paths and paths[0] == new_key else track["mtime"]
            self.tracks[spotify_id] = {**track, "paths": paths, "mtime": primary_mtime}
            self._dirty = True
            return

        if old_key in self.untagged:
            entry = self.untagged.pop(old_key)
            self.untagged[new_key] = {**entry, "mtime": mtime}
            self._dirty = True
            return

        self.note_file(new_path)

    def save(self):
        if not self._dirty:
            return
        self._write_cache()
        self._dirty = False

    def _load_cache(self) -> dict:
        try:
            with open(self.cache_file, "r") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def _write_cache(self):
        self.music_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.cache_file.with_suffix(".json.tmp")
        with open(tmp, "w") as f:
            json.dump({"tracks": self.tracks, "untagged": self.untagged, "updated_at": time.time()}, f)
        tmp.replace(self.cache_file)
