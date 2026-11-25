"""The hub's collection of distinct music covers (stdlib only).

The now-playing service adds a cover here whenever a music track's cover is
found, so the panel's Album cover mosaic has hundreds of covers to draw on, long
after the 50-play history has pruned its own copies.

Layout under the public root:

- ``cover/archive/<sha256 of the bytes>.<jpg|png>``: one file per distinct
  picture, so the same image is stored once. The name comes from the bytes,
  never from track metadata.
- ``nowplaying/cover-archive.json``: ``{"covers": [{"url", "album", "artist",
  "added_at"}]}``, newest first. It is also the index this module reloads at
  start.

A cover joins unless its bytes are already held, or a cover with the same
normalized album and artist is (a re-found cover of one album adds no second
file). Entries with no album and artist (seeded from the lookup cache) dedupe by
bytes only. Podcasts and audiobooks are left out when the kind is known. The
collection is capped; the oldest added entry and its file go first. Images must
be a JPEG or a PNG by magic bytes and no larger than ``max_bytes``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from .artlookup import _normalize, route

CAP = 1000
MAX_BYTES = 4 * 1024 * 1024
_NAME_MAX = 200
_NAME_RE = re.compile(r"^[0-9a-f]{64}\.(jpg|png)$")
_JPEG_MAGIC = b"\xff\xd8\xff"
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_SEED_DIRS = ("lookup", "history")


def _extension(data: bytes) -> Optional[str]:
    if data.startswith(_JPEG_MAGIC) and len(data) > 3:
        return "jpg"
    if data.startswith(_PNG_MAGIC) and len(data) > len(_PNG_MAGIC):
        return "png"
    return None


def _clean(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.split())[:_NAME_MAX]


def _name_key(album: str, artist: str) -> Optional[str]:
    album_n, artist_n = _normalize(album), _normalize(artist)
    if not album_n and not artist_n:
        return None
    return album_n + "\x1f" + artist_n


def _write_atomic(path: Path, data: bytes) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".cov.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
            os.fchmod(handle.fileno(), 0o644)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


class CoverArchive:
    def __init__(
        self,
        public_root: Path,
        *,
        cap: int = CAP,
        max_bytes: int = MAX_BYTES,
        clock: Callable[[], datetime] | None = None,
        log: logging.Logger | None = None,
    ) -> None:
        self._root = Path(public_root)
        self._cover_dir = self._root / "cover"
        self._dir = self._cover_dir / "archive"
        self._index = self._root / "nowplaying" / "cover-archive.json"
        self._cap = cap
        self._max_bytes = max_bytes
        self._clock = clock if clock is not None else (lambda: datetime.now(timezone.utc))
        self._log = log if log is not None else logging.getLogger("snaphouse.coverarchive")
        self.entries: list[dict] = self._load()

    # -- index ------------------------------------------------------------

    def _load(self) -> list[dict]:
        try:
            doc = json.loads(self._index.read_text(encoding="utf-8"))
            raw = doc["covers"]
        except (OSError, ValueError, KeyError, TypeError):
            return []
        entries = []
        if not isinstance(raw, list):
            return []
        for item in raw:
            if not isinstance(item, dict):
                continue
            url = item.get("url")
            if not isinstance(url, str) or not url.startswith("/cover/archive/"):
                continue
            if not _NAME_RE.match(url.rsplit("/", 1)[1]):
                continue
            entries.append(
                {
                    "url": url,
                    "album": _clean(item.get("album")),
                    "artist": _clean(item.get("artist")),
                    "added_at": item.get("added_at") if isinstance(item.get("added_at"), str) else "",
                }
            )
        return entries

    def publish(self) -> None:
        self._index.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps({"covers": self.entries}, indent=2) + "\n"
        _write_atomic(self._index, payload.encode("utf-8"))

    # -- adding -----------------------------------------------------------

    def _has_name(self, name: str) -> bool:
        return any(e["url"].endswith("/" + name) for e in self.entries)

    def _has_names(self, key: str) -> bool:
        return any(_name_key(e["album"], e["artist"]) == key for e in self.entries)

    def _add(self, data: bytes, album: str, artist: str, added_at: str) -> bool:
        ext = _extension(data)
        if ext is None or len(data) > self._max_bytes:
            return False
        name = f"{hashlib.sha256(data).hexdigest()}.{ext}"
        key = _name_key(album, artist)
        if self._has_name(name) or (key is not None and self._has_names(key)):
            return False
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            _write_atomic(self._dir / name, data)
        except OSError:
            self._log.warning("could not store a cover in the archive")
            return False
        self.entries.insert(
            0,
            {"url": f"/cover/archive/{name}", "album": album, "artist": artist, "added_at": added_at},
        )
        return True

    def _trim(self) -> None:
        while len(self.entries) > self._cap:
            old = self.entries.pop()
            try:
                (self._dir / old["url"].rsplit("/", 1)[1]).unlink()
            except OSError:
                pass

    def add_bytes(self, data: bytes, album: object, artist: object) -> bool:
        added = self._add(data, _clean(album), _clean(artist), self._clock().isoformat())
        if added:
            self._trim()
            self.publish()
        return added

    def _read_cover(self, art_url: object) -> Optional[bytes]:
        """The bytes of a ``/cover/...`` file, or None when unsafe or unreadable."""
        if not isinstance(art_url, str) or not art_url.startswith("/cover/"):
            return None
        relative = art_url[len("/cover/") :]
        if not relative or "\x00" in relative:
            return None
        try:
            base = self._cover_dir.resolve()
            target = (self._cover_dir / relative).resolve()
            target.relative_to(base)
            if not target.is_file() or target.stat().st_size > self._max_bytes:
                return None
            return target.read_bytes()
        except (OSError, ValueError):
            return None

    def add_file(
        self,
        art_url: object,
        album: object,
        artist: object,
        *,
        kind: str | None = None,
        genre: list[str] | None = None,
    ) -> bool:
        if kind is not None and route(kind, genre) != "music":
            return False
        data = self._read_cover(art_url)
        if data is None:
            return False
        return self.add_bytes(data, album, artist)

    # -- seeding ----------------------------------------------------------

    def seed(self, plays: list[dict]) -> int:
        """Fill an empty collection from the history covers and the lookup cache.

        Plays come first, so their covers carry names. The lookup cache holds
        covers of any kind and has no names, so each joins with empty strings and
        dedupes by bytes. A cover whose bytes a known podcast or audiobook play
        used is skipped. Does nothing when the collection already has entries.
        """
        if self.entries:
            return 0
        skip: set[bytes] = set()
        staged: list[tuple[str, bytes, str, str]] = []
        for play in plays:
            data = self._read_cover(play.get("art_url"))
            if data is None:
                continue
            kind, genre = play.get("kind"), play.get("genre")
            if kind is not None and route(kind, genre) != "music":
                skip.add(hashlib.sha256(data).digest())
                continue
            stamp = play.get("last_played_at") or play.get("started_at") or ""
            staged.append((stamp if isinstance(stamp, str) else "", data, play.get("album"), play.get("artist")))
        for sub in _SEED_DIRS:
            try:
                files = sorted((self._cover_dir / sub).iterdir())
            except OSError:
                continue
            for path in files:
                try:
                    if not path.is_file() or path.is_symlink():
                        continue
                    mtime = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()
                except OSError:
                    continue
                data = self._read_cover(f"/cover/{sub}/{path.name}")
                if data is not None:
                    staged.append((mtime, data, "", ""))
        # Oldest first so each insert at the front leaves the newest on top.
        named = [s for s in staged if s[2] or s[3]]
        unnamed = [s for s in staged if not (s[2] or s[3])]
        count = 0
        for group in (named, unnamed):
            for stamp, data, album, artist in sorted(group, key=lambda s: s[0]):
                if hashlib.sha256(data).digest() in skip:
                    continue
                if self._add(data, _clean(album), _clean(artist), stamp or self._clock().isoformat()):
                    count += 1
        self.entries.sort(key=lambda e: e["added_at"], reverse=True)
        self._trim()
        self.publish()
        return count
