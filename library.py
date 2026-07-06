"""Index MP3 files by Spotify ID. JSON cache is authoritative when paths[0] mtime matches."""

import json
import os
import shutil
import time
from collections import defaultdict
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


def link_track(source: Path, target: Path):
    """Hard link source at target using a relative path."""
    source = source.resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        raise FileExistsError(target)

    rel = os.path.relpath(source, start=target.parent.resolve())
    cwd = os.getcwd()
    os.chdir(target.parent)
    try:
        os.link(rel, target.name)
    finally:
        os.chdir(cwd)


class LibraryIndex:
    """Global map: spotify_id -> {paths[], youtube_id, mtime}. paths[0] is the primary file."""

    def __init__(self, music_dir: Path):
        self.music_dir = Path(music_dir).resolve()
        self.cache_file = self.music_dir / INDEX_FILENAME
        self.tracks: dict[str, dict] = {}
        self.untagged: dict[str, dict] = {}
        self._dirty = False
        self._hardlinks_ok: bool | None = None
        self._healed = 0

    def _rel_key(self, path: Path) -> str:
        return str(path.relative_to(self.music_dir))

    def _sort_paths(self, paths: list[str]) -> list[str]:
        return sorted(set(paths), key=lambda p: ((self.music_dir / p).is_symlink(), p))

    def _same_inode(self, a: Path, b: Path) -> bool:
        try:
            return a.resolve().stat().st_ino == b.resolve().stat().st_ino
        except OSError:
            return False

    def _hardlinks_work(self) -> bool:
        if self._hardlinks_ok is not None:
            return self._hardlinks_ok

        test_dir = self.music_dir / ".link-test"
        try:
            test_dir.mkdir(exist_ok=True)
            a = test_dir / "a"
            b = test_dir / "b"
            a.write_bytes(b"x")
            os.link(a, b)
            self._hardlinks_ok = os.stat(a).st_ino == os.stat(b).st_ino
        except OSError:
            self._hardlinks_ok = False
        finally:
            shutil.rmtree(test_dir, ignore_errors=True)

        return self._hardlinks_ok

    def _heal_paths(self, paths: list[str]) -> list[str]:
        paths = self._sort_paths(paths)
        if len(paths) < 2:
            return paths

        primary = self.music_dir / paths[0]
        if not primary.exists():
            return paths

        healed = [paths[0]]
        for rel in paths[1:]:
            dup = self.music_dir / rel
            if not dup.exists():
                self._dirty = True
                continue
            if not dup.is_symlink() and self._same_inode(primary, dup):
                healed.append(rel)
                continue

            try:
                dup.unlink()
                link_track(primary, dup)
                if self._same_inode(primary, dup) and not dup.is_symlink():
                    healed.append(rel)
                    self._healed += 1
                    self._dirty = True
                else:
                    shutil.copy2(primary, dup)
                    healed.append(rel)
            except OSError:
                if not dup.exists():
                    shutil.copy2(primary, dup)
                healed.append(rel)

        return self._sort_paths(healed)

    def build(self):
        cached = self._load_cache()
        cached_tracks = cached.get("tracks", {})
        cached_untagged = cached.get("untagged", {})
        self._healed = 0

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

        heal = self._hardlinks_work()

        tracks: dict[str, dict] = {}
        for spotify_id, paths in by_spotify.items():
            paths = self._sort_paths(paths)
            if heal and len(paths) > 1:
                paths = self._heal_paths(paths)

            primary = self.music_dir / paths[0]
            mtime = primary.resolve().stat().st_mtime
            cached_track = cached_tracks.get(spotify_id, {})
            cached_paths = cached_track.get("paths", [])

            if cached_paths and cached_paths[0] == paths[0] and cached_track.get("mtime") == mtime:
                youtube_id = cached_track.get("youtube_id")
            else:
                _, youtube_id = read_ids_from_mp3(primary.resolve())

            tracks[spotify_id] = {"paths": paths, "mtime": mtime, "youtube_id": youtube_id}

        by_name: dict[str, list[str]] = defaultdict(list)
        for key in untagged_paths:
            by_name[Path(key).name.lower()].append(key)

        final_untagged_keys: list[str] = []
        for paths in by_name.values():
            paths = self._sort_paths(paths)
            if heal and len(paths) > 1:
                paths = self._heal_paths(paths)
            final_untagged_keys.extend(paths)

        untagged: dict[str, dict] = {}
        for key in final_untagged_keys:
            path = self.music_dir / key
            if not path.exists():
                continue
            mtime = path.resolve().stat().st_mtime
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

        msg = f"📇 Library index: {len(tracks)} tracks / {scanned} mp3s"
        if self._healed:
            msg += f" (healed {self._healed} duplicates)"
        elif not heal:
            msg += " (hard links unavailable, skip heal)"
        print(msg)

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
