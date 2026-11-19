"""Scan the Ente export mirror into albums and pictures.

The mirror is `<root>/<albumId>/<fileId>.<ext>` plus `<root>/<albumId>/album.json`
(`{"title": str, "updated_at": int, "files": {"<fileId>": {...}}}`). A picture is
listed only if it is recorded in `album.json`, its file exists as a regular file
(never a symlink) and its extension is an image extension. Image contents are
never read here.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

ENTE_PREFIX = "ente-"
ENTE_ID_RE = re.compile(r"^[0-9A-Za-z]{1,24}$")
ENTE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif"}
_ALBUM_JSON = "album.json"


@dataclass(frozen=True)
class EntePicture:
    file_id: str
    source: Path
    album_id: str
    mtime: int
    size: int


@dataclass(frozen=True)
class EnteAlbum:
    folder: str
    title: str
    pictures: tuple[EntePicture, ...]


def _is_id(value: object) -> bool:
    return isinstance(value, str) and ENTE_ID_RE.fullmatch(value) is not None


def _read_record(album_dir: Path) -> dict | None:
    path = album_dir / _ALBUM_JSON
    try:
        if path.is_symlink() or not path.is_file():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("title"), str):
        return None
    if not isinstance(data.get("files", {}), dict):
        return None
    return data


def _index_pictures(album_dir: Path, album_id: str) -> dict[str, EntePicture]:
    """List the directory once: stem -> picture for image regular files."""
    try:
        entries = list(album_dir.iterdir())
    except OSError:
        return {}
    found: dict[str, EntePicture] = {}
    for entry in sorted(entries, key=lambda e: e.name):
        if entry.suffix.lower() not in ENTE_EXTENSIONS or entry.stem in found:
            continue
        try:
            if entry.is_symlink() or not entry.is_file():
                continue
            st = entry.stat()
        except OSError:
            continue
        found[entry.stem] = EntePicture(
            entry.stem, entry, album_id, int(st.st_mtime), st.st_size
        )
    return found


def _scan_album(album_dir: Path) -> EnteAlbum | None:
    album_id = album_dir.name
    if not _is_id(album_id) or album_dir.is_symlink() or not album_dir.is_dir():
        return None
    record = _read_record(album_dir)
    if record is None:
        return None
    on_disk = _index_pictures(album_dir, album_id)
    pictures = [
        on_disk[file_id]
        for file_id in sorted(record.get("files", {}))
        if _is_id(file_id) and file_id in on_disk
    ]
    return EnteAlbum(ENTE_PREFIX + album_id, record["title"], tuple(pictures))


def scan_export(root: Path) -> list[EnteAlbum]:
    """One album per readable `<root>/<albumId>/`, sorted by folder id."""
    try:
        children = list(Path(root).iterdir())
    except OSError:
        return []
    albums = [a for a in (_scan_album(c) for c in children) if a is not None]
    return sorted(albums, key=lambda a: a.folder)
