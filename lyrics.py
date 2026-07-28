"""Fetch lyrics sidecars via LRCLIB (host only). Writes .lrc or .txt next to each MP3."""

from __future__ import annotations

import os
import re
import shutil
import time
from pathlib import Path

import requests
from mutagen.id3 import TXXX
from mutagen.mp3 import MP3

from audio_analysis import iter_track_groups, mp3_duration_sec
from library import LibraryIndex, get_txxx, link_track

LRCLIB_BASE = "https://lrclib.net/api"
USER_AGENT = "spotify-to-mp3/1.0 (https://github.com/flesler/spotify-to-mp3)"
DURATION_TOLERANCE_SEC = 2.0
REQUEST_DELAY_SEC = 0.25

LYRICS_LRCLIB_TAG = "LYRICS_LRCLIB"
LYRICS_NOMATCH = "nomatch"

SIDECAR_LRC = ".lrc"
SIDECAR_TXT = ".txt"


class LyricsFetchError(Exception):
    pass


def lyrics_enabled() -> bool:
    flag = os.environ.get("LYRICS_FETCH", "0").lower()
    return flag in ("1", "true", "yes")


def lrc_sidecar_path(mp3: Path) -> Path:
    return mp3.with_suffix(SIDECAR_LRC)


def txt_sidecar_path(mp3: Path) -> Path:
    return mp3.with_suffix(SIDECAR_TXT)


def acestep_sidecar_path(mp3: Path) -> Path:
    return mp3.with_name(mp3.stem + ".lyrics.txt")


def has_lyrics_sidecar(mp3: Path) -> bool:
    return lrc_sidecar_path(mp3).exists() or txt_sidecar_path(mp3).exists()


def lyrics_lrclib_nomatch(mp3: Path) -> bool:
    try:
        tags = MP3(mp3).tags
        return get_txxx(tags, LYRICS_LRCLIB_TAG) == LYRICS_NOMATCH
    except Exception:
        return False


def should_skip_lyrics_fetch(mp3: Path, *, force: bool = False) -> str | None:
    if force:
        return None
    if has_lyrics_sidecar(mp3):
        return "sidecar"
    if lyrics_lrclib_nomatch(mp3):
        return "nomatch"
    return None


def set_lyrics_nomatch(mp3: Path) -> None:
    audio = MP3(mp3)
    if audio.tags is None:
        audio.add_tags()
    assert audio.tags is not None
    audio.tags.setall(
        f"TXXX:{LYRICS_LRCLIB_TAG}",
        [TXXX(encoding=3, desc=LYRICS_LRCLIB_TAG, text=LYRICS_NOMATCH)],
    )
    audio.save()


def clear_lyrics_lrclib_tag(mp3: Path) -> None:
    try:
        audio = MP3(mp3)
        if audio.tags is None:
            return
        audio.tags.delall(f"TXXX:{LYRICS_LRCLIB_TAG}")
        audio.save()
    except Exception:
        pass


def read_mp3_tags(mp3: Path) -> tuple[str | None, str | None, str | None]:
    try:
        tags = MP3(mp3).tags
        if not tags:
            return None, None, None
        title = tags.get("TIT2")
        artist = tags.get("TPE1") or tags.get("TPE2")
        album = tags.get("TALB")
        return (
            str(title.text[0]) if title and title.text else None,
            str(artist.text[0]) if artist and artist.text else None,
            str(album.text[0]) if album and album.text else None,
        )
    except Exception:
        return None, None, None


def lrc_to_plain(text: str) -> str:
    """Strip LRC timestamps and metadata tags for ACE-Step .lyrics.txt."""
    lines: list[str] = []
    for raw in text.splitlines():
        line = re.sub(r"\[\d{1,2}:\d{2}(?:\.\d{1,3})?\]", "", raw).strip()
        if not line or line.startswith("["):
            continue
        lines.append(line)
    return "\n".join(lines)


def _lrclib_get(
    session: requests.Session,
    *,
    track_name: str,
    artist_name: str,
    album_name: str | None,
    duration_sec: int,
) -> dict | None:
    params: dict[str, str | int] = {
        "track_name": track_name,
        "artist_name": artist_name,
        "duration": duration_sec,
    }
    if album_name:
        params["album_name"] = album_name

    resp = session.get(f"{LRCLIB_BASE}/get", params=params, timeout=30)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return resp.json()


def _lrclib_search(
    session: requests.Session,
    *,
    track_name: str,
    artist_name: str,
    duration_sec: float,
) -> dict | None:
    resp = session.get(
        f"{LRCLIB_BASE}/search",
        params={"track_name": track_name, "artist_name": artist_name},
        timeout=30,
    )
    if resp.status_code != 200:
        return None

    best: dict | None = None
    best_delta = DURATION_TOLERANCE_SEC + 1
    for item in resp.json():
        item_duration = item.get("duration")
        if item_duration is None:
            continue
        delta = abs(float(item_duration) - duration_sec)
        if delta <= DURATION_TOLERANCE_SEC and delta < best_delta:
            best = item
            best_delta = delta
    return best


def fetch_lrclib(
    *,
    track_name: str,
    artist_name: str,
    album_name: str | None,
    duration_sec: float,
    session: requests.Session | None = None,
) -> dict | None:
    if not track_name or not artist_name or duration_sec <= 0:
        return None

    duration_int = int(round(duration_sec))
    own_session = session is None
    if own_session:
        session = requests.Session()
    assert session is not None
    session.headers.setdefault("User-Agent", USER_AGENT)

    try:
        result = _lrclib_get(
            session,
            track_name=track_name,
            artist_name=artist_name,
            album_name=album_name,
            duration_sec=duration_int,
        )
        if result:
            return result
        return _lrclib_search(
            session,
            track_name=track_name,
            artist_name=artist_name,
            duration_sec=duration_sec,
        )
    finally:
        if own_session:
            session.close()


def _pick_sidecar_content(record: dict) -> tuple[str, str] | None:
    if record.get("instrumental"):
        return None

    synced = (record.get("syncedLyrics") or "").strip()
    if synced:
        return synced, SIDECAR_LRC

    plain = (record.get("plainLyrics") or "").strip()
    if plain:
        return plain, SIDECAR_TXT

    return None


def write_lyrics_sidecar(mp3: Path, record: dict) -> Path | None:
    picked = _pick_sidecar_content(record)
    if not picked:
        return None

    content, suffix = picked
    path = mp3.with_suffix(suffix)
    path.write_text(content if content.endswith("\n") else content + "\n", encoding="utf-8")
    return path


def fetch_lyrics_for_mp3(
    mp3: Path,
    *,
    session: requests.Session | None = None,
    mark_miss: bool = True,
) -> Path | None:
    if not lyrics_enabled():
        raise RuntimeError("Lyrics fetch disabled (set LYRICS_FETCH=1)")

    mp3 = Path(mp3)
    title, artist, album = read_mp3_tags(mp3)
    if not title or not artist:
        raise LyricsFetchError("missing TIT2/TPE1 ID3 tags")

    duration = mp3_duration_sec(mp3)
    if duration is None:
        raise LyricsFetchError("could not read duration")

    record = fetch_lrclib(
        track_name=title,
        artist_name=artist,
        album_name=album,
        duration_sec=duration,
        session=session,
    )
    if not record:
        if mark_miss:
            set_lyrics_nomatch(mp3)
        return None

    sidecar = write_lyrics_sidecar(mp3, record)
    if sidecar:
        clear_lyrics_lrclib_tag(mp3)
    elif mark_miss:
        set_lyrics_nomatch(mp3)
    return sidecar


def _same_inode(a: Path, b: Path) -> bool:
    try:
        return a.resolve().stat().st_ino == b.resolve().stat().st_ino
    except OSError:
        return False


def propagate_lyrics_sidecar(primary_mp3: Path, other_mp3s: list[Path]) -> int:
    primary_mp3 = primary_mp3.resolve()
    source: Path | None = None
    for candidate in (lrc_sidecar_path(primary_mp3), txt_sidecar_path(primary_mp3)):
        if candidate.exists():
            source = candidate
            break
    if source is None:
        return 0

    linked = 0
    for mp3 in other_mp3s:
        mp3 = Path(mp3).resolve()
        if mp3 == primary_mp3:
            continue
        target = mp3.with_suffix(source.suffix)
        if target.exists() and _same_inode(source, target):
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            target.unlink()
        try:
            link_track(source, target)
        except OSError:
            shutil.copy2(source, target)
        linked += 1
    return linked


def _process_track(
    primary: Path,
    paths: list[Path],
    *,
    session: requests.Session,
    force: bool,
    stats: dict[str, int],
    label: str,
) -> None:
    skip = should_skip_lyrics_fetch(primary, force=force)
    if skip:
        stats["skipped"] += 1
        print(f"  {label} skip ({skip}) {primary.name}", flush=True)
        stats["linked"] += propagate_lyrics_sidecar(primary, paths)
        return

    title, artist, _album = read_mp3_tags(primary)
    if not title or not artist:
        stats["no_tags"] += 1
        print(f"  {label} no tags {primary.name}", flush=True)
        return

    if force:
        clear_lyrics_lrclib_tag(primary)

    try:
        print(f"  {label} fetch {primary.name}", flush=True)
        sidecar = fetch_lyrics_for_mp3(primary, session=session)
        if sidecar:
            stats["fetched"] += 1
            print(f"           → {sidecar.name}", flush=True)
        else:
            stats["missing"] += 1
            print("           → not found (tagged nomatch)", flush=True)
        stats["linked"] += propagate_lyrics_sidecar(primary, paths)
    except Exception as e:
        stats["failed"] += 1
        print(f"❌ {primary.name}: {e}", flush=True)


def fetch_lyrics(
    music_dir: Path,
    *,
    playlist: str | None = None,
    mp3_paths: list[Path] | None = None,
    limit: int | None = None,
    force: bool = False,
) -> dict[str, int]:
    stats = {"fetched": 0, "skipped": 0, "missing": 0, "no_tags": 0, "linked": 0, "failed": 0}

    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT

    try:
        if mp3_paths is not None:
            total = len(mp3_paths)
            print(f"🎤 {total} track(s)", flush=True)
            for i, mp3 in enumerate(mp3_paths, 1):
                if limit is not None and stats["fetched"] >= limit:
                    break
                _process_track(mp3, [mp3], session=session, force=force, stats=stats, label=f"[{i}/{total}]")
                time.sleep(REQUEST_DELAY_SEC)
            return stats

        print(f"📚 Indexing {music_dir}…", flush=True)
        index = LibraryIndex(music_dir)
        index.build()

        groups = iter_track_groups(index, playlist=playlist)
        total = len(groups)
        print(f"🎤 {total} unique tracks", flush=True)

        for i, (primary, paths) in enumerate(groups, 1):
            if limit is not None and stats["fetched"] >= limit:
                break
            _process_track(
                primary,
                paths,
                session=session,
                force=force,
                stats=stats,
                label=f"[{i}/{total}]",
            )
            time.sleep(REQUEST_DELAY_SEC)
    finally:
        session.close()

    return stats


# Back-compat alias
fetch_lyrics_library = fetch_lyrics
