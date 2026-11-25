"""The hub's art library core: scan, index, thumbnails, uploads, trash, folders.

The library is the folder at ``/srv/snaphouse/art`` (see roles/art_share). This
module owns every operation on it that the admin service (Task 2) exposes over
HTTP: it produces the ``/art-index.json`` and ``/art-control.json`` documents,
generates and prunes thumbnails, validates and stores uploads (EXIF stripped,
orientation baked in), moves pieces between folders, keeps a restorable trash,
and records the rotation settings and the last command.

It is deliberately free of HTTP: every method takes plain filenames, bytes, ids,
and names, and raises ``ArtError(status, message)`` whose ``message`` is safe to
show on the page as-is. The caller turns an ``ArtError`` into a JSON error with
that status. The layout, the JSON shapes, the allowed intervals, the landscape
rule, and the validation rules are the design in
``docs/superpowers/specs/2026-11-09-art-rotation-design.md``.

Pillow is the hub's only extra dependency; it is imported at module load because
this module only ever runs on the hub (and in CI, where Pillow is a dev dep).
``MAX_IMAGE_PIXELS`` guards against decompression bombs: an image whose declared
size is far past the cap is refused before it is decoded.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import tempfile
import time
import urllib.parse
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Callable, Optional

from PIL import Image, ImageOps, UnidentifiedImageError

from snaphouse import framecrop
from snaphouse.enteexport import ENTE_ID_RE, ENTE_PREFIX, EnteAlbum, EntePicture, scan_export
from snaphouse.enterender import ensure_rendition, prune_renditions, rendition_path
from snaphouse.enterun import read_status


# --------------------------------------------------------------------------- #
# Constants (the binding settings from the spec and global constraints)
# --------------------------------------------------------------------------- #

#: The built-in folder name for pieces that sit at the top level of the library.
BUILTIN_FOLDER = "Collection"

#: Deleted pieces live under this directory, hidden from the piece listing.
TRASH_DIR = ".trash"

#: Long side of a generated thumbnail, in pixels.
THUMB_LONG_SIDE = 320

#: How many thumbnails the background pass generates between index republishes,
#: so a large library's thumbnails appear in batches rather than one write each.
THUMBNAIL_BATCH = 16

#: Trash entries older than this are purged on scan.
TRASH_RETENTION_SECONDS = 30 * 86400

#: Default rotation cutoff: a piece rotates on the frame only when its aspect
#: reaches this (landscape). Stored in the control file as ``settings.min_aspect``
#: so the hub is the single source of truth and the frame follows it.
MIN_ASPECT = 1.1

#: Upload size limit per file; nginx enforces the same limit at the edge.
MAX_UPLOAD_BYTES = 25 * 1024 * 1024

#: Decompression-bomb guard: an upload whose header declares more than this many
#: pixels is refused before any decode (an explicit check, not Pillow's global).
#: Real museum photos (about 12 megapixels) sit well under it.
MAX_IMAGE_PIXELS = 80_000_000

#: Rotation intervals the page offers, in minutes.
ALLOWED_INTERVALS = (15, 30, 60, 120, 180, 360, 720, 1440)

#: Default rotation interval, in minutes.
DEFAULT_INTERVAL = 60

#: Image extensions the library recognises (lowercase, with the dot).
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}

#: Pillow format names accepted on upload.
_UPLOAD_FORMATS = {"JPEG", "PNG", "WEBP"}

ENTE_SOURCE = "ente"

#: Shown when a write touches an Ente album or one of its pictures.
ENTE_READONLY_MESSAGE = "This picture comes from Ente; change it in Ente."

#: Shown when a new or renamed folder would take the reserved Ente prefix.
ENTE_RESERVED_MESSAGE = "That folder name is reserved."


def _is_ente_name(name: str) -> bool:
    """True for a name under the reserved Ente prefix, in any letter case."""
    return name.casefold().startswith(ENTE_PREFIX)


def _refuse_if_ente(value, *, creating: bool = False, piece: bool = False) -> None:
    """Refuse a write that touches an Ente folder or piece with a 409.

    ``value`` is a folder name, or with ``piece=True`` a piece id (only the
    folder part counts, so a Collection piece called ``Ente-sunset.jpg`` is
    fine). ``creating`` is for a name being made or renamed to: that is the
    reserved-name message; every other write says the picture lives in Ente.
    """
    if not isinstance(value, str):
        return
    if piece:
        if "/" not in value:
            return
        value = value.partition("/")[0]
    if _is_ente_name(value):
        raise ArtError(409, ENTE_RESERVED_MESSAGE if creating else ENTE_READONLY_MESSAGE)


_FOLDER_NAME_RE = re.compile(r"^[A-Za-z0-9 _-]{1,40}$")
_FILE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_TRASH_STAMP_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}-[0-9]{2}-[0-9]{2}(-[0-9]+)?$")

_TRASH_META = "meta.json"

#: Saved crops, one JSON file beside the control state file, keyed by piece id.
CROPS_FILE = "crops.json"

#: Suffix of the automatic-box cache written beside each thumbnail
#: (``<thumbnail stem>.crop.json``). ``AUTO_CROP_VERSION`` is bumped when the
#: window algorithm changes so old caches are recomputed.
AUTO_CROP_SUFFIX = ".crop.json"
AUTO_CROP_VERSION = 1

_NO_CACHE = object()


def _clean_box(box) -> Optional[dict]:
    """A stored or cached box as four finite floats in [0, 1], else ``None``."""
    if not isinstance(box, dict) or set(box) != {"x", "y", "w", "h"}:
        return None
    clean = {}
    for key in ("x", "y", "w", "h"):
        value = box[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        value = float(value)
        if not math.isfinite(value) or value < 0 or value > 1:
            return None
        clean[key] = value
    return clean


class ArtError(Exception):
    """A library error with an HTTP status and a message safe to show the user.

    ``status`` is one of 400, 404, 409, 413. ``message`` is a short sentence the
    page can display verbatim; it never names a filesystem path or leaks any
    detail of the host.
    """

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


# --------------------------------------------------------------------------- #
# The library
# --------------------------------------------------------------------------- #


class Library:
    """Every operation on the art folder, with no HTTP in sight.

    ``root`` is the library folder, ``thumbs`` the directory thumbnails are
    written to (served at ``/art-thumbs/``), and ``state`` the JSON file the
    rotation settings and last command persist in. ``clock`` is injected so
    timestamps and trash purge are deterministic under test.
    """

    def __init__(
        self,
        root: Path,
        thumbs: Path,
        state: Path,
        *,
        ente_root: Optional[Path] = None,
        ente_cache: Optional[Path] = None,
        ente_status: Optional[Path] = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.root = Path(root)
        self.thumbs = Path(thumbs)
        self.state = Path(state)
        # Ente albums are an optional, read-only source: the mirror the sync
        # writes, the renditions cache nginx serves, and the sync status record.
        # Without a root and a cache the library behaves exactly as before.
        self.ente_root = Path(ente_root) if ente_root is not None else None
        self.ente_cache = Path(ente_cache) if ente_cache is not None else None
        self.ente_status = Path(ente_status) if ente_status is not None else None
        self._clock = clock

    # -- scan / index ------------------------------------------------------ #

    def scan(self, *, ensure_thumbnails: bool = True) -> dict:
        """Build the ``/art-index.json`` document and reconcile side state.

        Reads the whole library fresh (so SMB edits are picked up), pruning
        thumbnails nothing references and purging trash older than the retention
        window. A file that vanishes or fails to decode between the listing and
        the open is skipped rather than raising, so a hand-edit racing the scan
        never crashes a request.

        With ``ensure_thumbnails`` (the default) each piece's thumbnail is
        generated if missing, so the returned ``thumb`` is always a URL. With it
        ``False`` the scan is metadata-only: it generates nothing (the slow
        decode is skipped), a piece whose thumbnail does not exist yet gets
        ``thumb: null``, and one whose thumbnail already exists keeps its URL.
        The admin service publishes a metadata-only index at startup so a large
        library is listed at once, then fills thumbnails in the background with
        ``ensure_thumbnails`` and republishes.
        """
        self._purge_trash()
        control = self._load_state()
        enabled = set(control["settings"]["folders_enabled"])
        min_aspect = control["settings"]["min_aspect"]
        crops = self._load_crops()

        pieces: list[dict] = []
        folder_counts: dict[str, int] = {BUILTIN_FOLDER: 0}
        referenced: set[str] = set()

        for entry in self._iter_dir(self.root):
            if entry.is_dir():
                # `.trash` is hidden, and a subdirectory literally named like the
                # built-in folder is ignored: its pieces would get ids of the form
                # `Collection/x.jpg`, which `_resolve_piece` maps back to the top
                # level, so a later delete or move would hit the wrong file. The
                # API refuses to create the name (`create_folder`); a hand-made
                # one over SMB is skipped here so it never emits colliding ids.
                if entry.name in (TRASH_DIR, BUILTIN_FOLDER) or _is_ente_name(entry.name):
                    continue
                folder_counts.setdefault(entry.name, 0)
                for child in self._iter_dir(entry):
                    if child.is_file():
                        piece = self._build_piece(
                            child, entry.name, referenced, ensure_thumbnails, min_aspect, crops
                        )
                        if piece is not None:
                            pieces.append(piece)
                            folder_counts[entry.name] += 1
            elif entry.is_file():
                piece = self._build_piece(
                    entry, BUILTIN_FOLDER, referenced, ensure_thumbnails, min_aspect, crops
                )
                if piece is not None:
                    pieces.append(piece)
                    folder_counts[BUILTIN_FOLDER] += 1

        albums = self._ente_albums()
        ente_pieces, ente_keep = self._build_ente_pieces(
            albums, referenced, ensure_thumbnails, min_aspect, crops
        )
        if self._reconcile_ente(control, [a.folder for a in albums]):
            self._save_state(control)
        enabled = set(control["settings"]["folders_enabled"])

        pieces.sort(key=lambda piece: piece["id"])
        pieces.extend(sorted(ente_pieces, key=lambda piece: piece["id"]))
        folders = self._build_folders(folder_counts, enabled)
        folders.extend(self._build_ente_folders(albums, ente_pieces, enabled))
        trash = self._build_trash(referenced, ensure_thumbnails)
        self._prune_thumbnails(referenced)
        self._prune_ente(ente_keep)
        self._prune_crops(crops)

        index = {
            "generated_at": self._now_iso(),
            "folders": folders,
            "pieces": pieces,
            "trash": trash,
        }
        if self.ente_status is not None:
            index["ente_sync"] = read_status(self.ente_status, now=self._clock())
        return index

    # -- Ente albums ------------------------------------------------------- #

    def _ente_albums(self) -> list[EnteAlbum]:
        if self.ente_root is None or self.ente_cache is None:
            return []
        return scan_export(self.ente_root)

    def _build_ente_pieces(
        self,
        albums: list[EnteAlbum],
        referenced: set[str],
        ensure_thumbnails: bool,
        min_aspect: float,
        crops: dict,
    ) -> tuple[list[dict], set[Path]]:
        """Index pieces for every album picture that has a rendition.

        With ``ensure_thumbnails`` a missing rendition is made first; without
        it a picture whose rendition does not exist yet is left out, like a
        thumbnail the metadata-only scan has not generated. Returns the pieces
        and the rendition paths that must be kept on disk.
        """
        pieces: list[dict] = []
        keep: set[Path] = set()
        for album in albums:
            for picture in album.pictures:
                dest = rendition_path(self.ente_cache, album.folder, picture)
                if ensure_thumbnails:
                    dest = ensure_rendition(
                        self.ente_cache, album.folder, picture, max_pixels=MAX_IMAGE_PIXELS
                    )
                    if dest is None:
                        continue
                elif not dest.is_file():
                    continue
                keep.add(dest)
                piece = self._build_ente_piece(
                    album, picture, dest, referenced, ensure_thumbnails, min_aspect, crops
                )
                if piece is not None:
                    pieces.append(piece)
        return pieces, keep

    def _build_ente_piece(
        self,
        album: EnteAlbum,
        picture: EntePicture,
        dest: Path,
        referenced: set[str],
        ensure_thumbnails: bool,
        min_aspect: float,
        crops: dict,
    ) -> Optional[dict]:
        piece_id = f"{album.folder}/{picture.file_id}.jpg"
        try:
            stat = dest.stat()
            width, height = self._dimensions(dest)
            thumb_name = self._thumbnail_name(piece_id, stat.st_size, int(stat.st_mtime))
            if ensure_thumbnails:
                self._ensure_thumbnail(dest, piece_id, stat.st_size, int(stat.st_mtime))
        except (OSError, UnidentifiedImageError, Image.DecompressionBombError):
            return None
        referenced.add(thumb_name)
        return {
            "id": piece_id,
            "folder": album.folder,
            "file": f"{picture.file_id}.jpg",
            "title": album.title,
            "artist": None,
            "source_url": None,
            "width": width,
            "height": height,
            "landscape": self._is_landscape(width, height, min_aspect)
            or piece_id in crops,
            "crop": crops.get(piece_id),
            "auto_crop": self._auto_crop_if_cached(thumb_name),
            "size": stat.st_size,
            "mtime": int(stat.st_mtime),
            "thumb": self._thumb_url_if_present(thumb_name),
            "url": self._art_url(piece_id),
            "source": ENTE_SOURCE,
        }

    @staticmethod
    def _build_ente_folders(
        albums: list[EnteAlbum], pieces: list[dict], enabled: set[str]
    ) -> list[dict]:
        counts: dict[str, int] = {}
        for piece in pieces:
            counts[piece["folder"]] = counts.get(piece["folder"], 0) + 1
        return [
            {
                "name": album.folder,
                "count": counts.get(album.folder, 0),
                "enabled": album.folder in enabled,
                "builtin": False,
                "source": ENTE_SOURCE,
                "title": album.title,
            }
            for album in albums
        ]

    @staticmethod
    def _reconcile_ente(control: dict, folders: list[str]) -> bool:
        """Switch a newly seen Ente folder on once; return True if state changed.

        An id already in ``ente_seen`` is never touched, so an album the owner
        switched off stays off across rescans and renames in Ente.
        """
        settings = control["settings"]
        new = [f for f in folders if f not in settings["ente_seen"]]
        if not new:
            return False
        settings["ente_seen"] = settings["ente_seen"] + new
        settings["folders_enabled"] = settings["folders_enabled"] + [
            f for f in new if f not in settings["folders_enabled"]
        ]
        return True

    def _ente_album_exists(self, folder: str) -> bool:
        """True if ``folder`` is ``ente-<albumId>`` with a mirror directory."""
        if self.ente_root is None or not folder.startswith(ENTE_PREFIX):
            return False
        album_id = folder[len(ENTE_PREFIX):]
        if not ENTE_ID_RE.fullmatch(album_id):
            return False
        album_dir = self.ente_root / album_id
        return album_dir.is_dir() and not album_dir.is_symlink()

    def _prune_ente(self, keep: set[Path]) -> None:
        """Remove renditions nothing lists. Skipped while the mirror root is
        unreadable, so an unmounted export never wipes the cache."""
        if self.ente_cache is None or self.ente_root is None or not self.ente_root.is_dir():
            return
        prune_renditions(self.ente_cache, keep)

    def _resolve_ente_piece(self, piece_id) -> Optional[Path]:
        """The rendition path for an Ente piece id, or ``None`` for an art piece.

        Any id under the reserved prefix is an Ente id: one that names no
        rendition is a 404, never a fall-through to an art folder.
        """
        if not isinstance(piece_id, str) or "/" not in piece_id:
            return None
        folder, _, name = piece_id.partition("/")
        if not _is_ente_name(folder):
            return None
        stem, dot, ext = name.rpartition(".")
        if (
            self.ente_cache is None
            or not folder.startswith(ENTE_PREFIX)
            or not ENTE_ID_RE.fullmatch(folder[len(ENTE_PREFIX):])
            or not ENTE_ID_RE.fullmatch(stem)
            or (dot, ext) != (".", "jpg")
        ):
            raise ArtError(404, "That piece is no longer in the library.")
        path = self.ente_cache / folder / name
        if not path.is_file():
            raise ArtError(404, "That piece is no longer in the library.")
        return path

    def _build_folders(self, counts: dict[str, int], enabled: set[str]) -> list[dict]:
        folders = [
            {
                "name": BUILTIN_FOLDER,
                "count": counts.get(BUILTIN_FOLDER, 0),
                "enabled": BUILTIN_FOLDER in enabled,
                "builtin": True,
            }
        ]
        for name in sorted(n for n in counts if n != BUILTIN_FOLDER):
            folders.append(
                {
                    "name": name,
                    "count": counts[name],
                    "enabled": name in enabled,
                    "builtin": False,
                }
            )
        return folders

    def _build_piece(
        self,
        path: Path,
        folder: str,
        referenced: set[str],
        ensure_thumbnails: bool,
        min_aspect: float,
        crops: dict,
    ) -> Optional[dict]:
        if path.suffix.lower() not in IMAGE_EXTENSIONS:
            return None
        if folder == BUILTIN_FOLDER:
            piece_id = path.name
        else:
            piece_id = f"{folder}/{path.name}"
        try:
            stat = path.stat()
            width, height = self._dimensions(path)
            thumb_name = self._thumbnail_name(piece_id, stat.st_size, int(stat.st_mtime))
            if ensure_thumbnails:
                self._ensure_thumbnail(path, piece_id, stat.st_size, int(stat.st_mtime))
        except (OSError, UnidentifiedImageError, Image.DecompressionBombError):
            # The file vanished (SMB edit racing the scan) or is not a real
            # image; skip it. The next scan catches up.
            return None

        # Reference the expected name whether or not the thumbnail exists yet, so
        # a thumbnail the background pass is about to write (or has just written)
        # is never pruned out from under it.
        referenced.add(thumb_name)
        title, artist, source_url = self._read_sidecar(path)
        return {
            "id": piece_id,
            "folder": folder,
            "file": path.name,
            "title": title or path.name,
            "artist": artist,
            "source_url": source_url,
            "width": width,
            "height": height,
            "landscape": self._is_landscape(width, height, min_aspect)
            or piece_id in crops,
            "crop": crops.get(piece_id),
            "auto_crop": self._auto_crop_if_cached(thumb_name),
            "size": stat.st_size,
            "mtime": int(stat.st_mtime),
            "thumb": self._thumb_url_if_present(thumb_name),
            "url": self._art_url(piece_id),
        }

    def _build_trash(self, referenced: set[str], ensure_thumbnails: bool) -> list[dict]:
        trash_root = self.root / TRASH_DIR
        entries: list[dict] = []
        for stamp_dir in self._iter_dir(trash_root):
            if not stamp_dir.is_dir():
                continue
            meta = self._read_trash_meta(stamp_dir)
            for child in self._iter_dir(stamp_dir):
                if not child.is_file() or child.suffix.lower() not in IMAGE_EXTENSIONS:
                    continue
                trash_id = f"{TRASH_DIR}/{stamp_dir.name}/{child.name}"
                try:
                    stat = child.stat()
                    thumb_name = self._thumbnail_name(
                        trash_id, stat.st_size, int(stat.st_mtime)
                    )
                    if ensure_thumbnails:
                        self._ensure_thumbnail(
                            child, trash_id, stat.st_size, int(stat.st_mtime)
                        )
                except (OSError, UnidentifiedImageError, Image.DecompressionBombError):
                    continue
                referenced.add(thumb_name)
                entries.append(
                    {
                        "id": trash_id,
                        "original": meta.get("original", child.name),
                        "deleted_at": meta.get("deleted_at", ""),
                        "thumb": self._thumb_url_if_present(thumb_name),
                    }
                )
        entries.sort(key=lambda entry: entry["id"], reverse=True)
        return entries

    def ensure_thumbnails(
        self,
        *,
        batch_size: int = THUMBNAIL_BATCH,
        on_batch: Optional[Callable[[], None]] = None,
        should_stop: Optional[Callable[[], bool]] = None,
    ) -> int:
        """Generate every missing thumbnail for pieces and trash, returning the
        number generated.

        The admin service calls this in the background after the fast
        metadata-only publish, so the whole library is listed at once and the
        thumbnails fill in behind it. ``on_batch`` is called after every
        ``batch_size`` thumbnails so the caller republishes the index in batches
        rather than once per thumbnail. ``should_stop`` is checked before each
        thumbnail so a shutdown ends the pass promptly. A file that vanishes or
        fails to decode mid-pass is skipped.
        """
        generated = 0

        def counted() -> None:
            nonlocal generated
            generated += 1
            if on_batch is not None and batch_size > 0 and generated % batch_size == 0:
                on_batch()

        for path, piece_id in self._thumbnail_targets():
            if should_stop is not None and should_stop():
                return generated
            try:
                stat = path.stat()
                name = self._thumbnail_name(piece_id, stat.st_size, int(stat.st_mtime))
                if self._derived_complete(name):
                    continue
                self._ensure_thumbnail(path, piece_id, stat.st_size, int(stat.st_mtime))
            except (OSError, UnidentifiedImageError, Image.DecompressionBombError):
                continue
            counted()
        for album in self._ente_albums():
            for picture in album.pictures:
                if should_stop is not None and should_stop():
                    return generated
                dest = rendition_path(self.ente_cache, album.folder, picture)
                made = not dest.is_file()
                dest = ensure_rendition(
                    self.ente_cache, album.folder, picture, max_pixels=MAX_IMAGE_PIXELS
                )
                if dest is None:
                    continue
                if made:
                    counted()
                try:
                    stat = dest.stat()
                    piece_id = f"{album.folder}/{picture.file_id}.jpg"
                    name = self._thumbnail_name(piece_id, stat.st_size, int(stat.st_mtime))
                    if self._derived_complete(name):
                        continue
                    self._ensure_thumbnail(dest, piece_id, stat.st_size, int(stat.st_mtime))
                except (OSError, UnidentifiedImageError, Image.DecompressionBombError):
                    continue
                counted()
        return generated

    def _thumbnail_targets(self):
        """Yield ``(path, id)`` for every piece and trash image, matching scan's
        walk (``.trash`` included, a literal ``Collection`` subdirectory skipped)."""
        for entry in self._iter_dir(self.root):
            if entry.is_dir():
                if entry.name == TRASH_DIR:
                    for stamp_dir in self._iter_dir(entry):
                        if not stamp_dir.is_dir():
                            continue
                        for child in self._iter_dir(stamp_dir):
                            if child.is_file() and child.suffix.lower() in IMAGE_EXTENSIONS:
                                yield child, f"{TRASH_DIR}/{stamp_dir.name}/{child.name}"
                elif entry.name == BUILTIN_FOLDER or _is_ente_name(entry.name):
                    continue
                else:
                    for child in self._iter_dir(entry):
                        if child.is_file() and child.suffix.lower() in IMAGE_EXTENSIONS:
                            yield child, f"{entry.name}/{child.name}"
            elif entry.is_file() and entry.suffix.lower() in IMAGE_EXTENSIONS:
                yield entry, entry.name

    # -- upload ------------------------------------------------------------ #

    def upload(
        self,
        filename: str,
        data: bytes,
        folder: str,
        title: Optional[str],
        artist: Optional[str],
    ) -> str:
        """Validate ``data`` as an image, strip it, and store it; return its id.

        The image is decoded with Pillow to prove it is a real JPEG, PNG, or
        WebP; anything else, a truncated file, a file past the size limit, or a
        decompression bomb raises before anything is written, so a rejected
        upload leaves nothing behind. EXIF orientation is baked into the pixels
        and every scrap of metadata (GPS included) is dropped by re-encoding. The
        stored name is sanitised from the original, with a numeric suffix on a
        collision. A title or artist, when given, is written to a sidecar.
        """
        _refuse_if_ente(folder)
        target_dir = self._resolve_folder_dir(folder)

        if len(data) > MAX_UPLOAD_BYTES:
            raise ArtError(413, "That file is larger than the 25 MB limit.")

        clean_bytes, extension = self._reencode(data)

        stem = self._sanitize_stem(filename)
        name = self._free_name(target_dir, stem, extension)
        destination = target_dir / name
        self._atomic_write(destination, clean_bytes, 0o664)

        if (title and title.strip()) or (artist and artist.strip()):
            sidecar = destination.with_name(destination.name + ".txt")
            body = f"{(title or '').strip()}\n{(artist or '').strip()}\n"
            self._atomic_write(sidecar, body.encode("utf-8"), 0o664)

        if folder == BUILTIN_FOLDER:
            return name
        return f"{folder}/{name}"

    def _reencode(self, data: bytes) -> tuple[bytes, str]:
        """Decode, bake in orientation, strip metadata, and re-encode.

        Returns the new bytes and the output extension. JPEG stays JPEG at
        quality 92; PNG stays PNG; WebP becomes PNG when it carries transparency,
        otherwise JPEG. Nothing from the source's metadata (EXIF, ICC, text
        chunks) is passed to ``save``, so none of it reaches disk.

        The decompression-bomb guard is an explicit pixel-count check on the
        header dimensions before any decode: it never touches the process-global
        ``Image.MAX_IMAGE_PIXELS``, so it is safe under the threaded admin server
        and background rescan of Task 2.
        """
        try:
            image = Image.open(BytesIO(data))
        except Image.DecompressionBombError:
            raise ArtError(413, "That image is too large to process.")
        except (UnidentifiedImageError, OSError, ValueError):
            raise ArtError(400, "That file is not a JPEG, PNG, or WebP image.")

        # `Image.open` has parsed the header, so the declared size is known
        # without decoding the pixels. Refuse an oversized image here, before
        # `load()` allocates anything for it.
        if image.width * image.height > MAX_IMAGE_PIXELS:
            raise ArtError(413, "That image is too large to process.")

        try:
            image.load()
        except Image.DecompressionBombError:
            raise ArtError(413, "That image is too large to process.")
        except (UnidentifiedImageError, OSError, ValueError):
            raise ArtError(400, "That file is not a JPEG, PNG, or WebP image.")

        source_format = image.format
        if source_format not in _UPLOAD_FORMATS:
            raise ArtError(400, "That file is not a JPEG, PNG, or WebP image.")

        oriented = ImageOps.exif_transpose(image) or image
        oriented.info.pop("exif", None)

        buffer = BytesIO()
        if source_format == "PNG" or (
            source_format == "WEBP" and self._has_alpha(oriented)
        ):
            converted = oriented if oriented.mode in ("RGB", "RGBA", "L", "LA", "P") else oriented.convert("RGBA")
            converted.save(buffer, format="PNG")
            return buffer.getvalue(), ".png"
        converted = oriented if oriented.mode == "RGB" else oriented.convert("RGB")
        converted.save(buffer, format="JPEG", quality=92)
        return buffer.getvalue(), ".jpg"

    @staticmethod
    def _has_alpha(image: "Image.Image") -> bool:
        return image.mode in ("RGBA", "LA") or (
            image.mode == "P" and "transparency" in image.info
        )

    # -- delete / restore / move ------------------------------------------ #

    def delete(self, piece_id: str) -> str:
        """Move a piece and its sidecar into ``.trash/<timestamp>/``; return the
        trash id. The original location and the delete time are recorded so
        ``restore`` can put it back."""
        _refuse_if_ente(piece_id, piece=True)
        source = self._resolve_piece(piece_id)
        stamp = self._trash_stamp()
        trash_dir = self._unique_dir(self.root / TRASH_DIR, stamp)
        trash_dir.mkdir(parents=True, exist_ok=True)
        self._chmod_dir(trash_dir)

        destination = trash_dir / source.name
        self._move_with_sidecar(source, destination)
        trash_id = f"{TRASH_DIR}/{trash_dir.name}/{source.name}"
        self._rekey_crops([(piece_id, trash_id)])
        meta = {
            "original": piece_id,
            "deleted_at": self._now_iso(),
            "deleted_ts": self._clock(),
        }
        self._atomic_write(
            trash_dir / _TRASH_META, json.dumps(meta).encode("utf-8"), 0o664
        )
        return trash_id

    def restore(self, trash_id: str) -> str:
        """Move a trashed piece back to its original place; return the new id.

        The name is suffixed if something now occupies the original name. If the
        original folder is gone it is recreated. The trash directory is removed
        once its piece (and sidecar) are out.
        """
        trash_file = self._resolve_trash(trash_id)
        trash_dir = trash_file.parent
        meta = self._read_trash_meta(trash_dir)
        original = meta.get("original", trash_file.name)
        _refuse_if_ente(original, piece=True)

        folder, _ = self._split_id(original)
        target_dir = self.root if folder == BUILTIN_FOLDER else self.root / folder
        if not target_dir.exists():
            target_dir.mkdir(parents=True, exist_ok=True)
            self._chmod_dir(target_dir)

        name = self._free_name(target_dir, Path(trash_file.name).stem, trash_file.suffix)
        destination = target_dir / name
        self._move_with_sidecar(trash_file, destination)
        shutil.rmtree(trash_dir, ignore_errors=True)

        new_id = name if folder == BUILTIN_FOLDER else f"{folder}/{name}"
        self._rekey_crops([(trash_id, new_id)])
        return new_id

    def move(self, piece_id: str, folder: str) -> str:
        """Move a piece and its sidecar into ``folder`` (``Collection`` is the
        top level); return the new id, suffixed on a name collision."""
        _refuse_if_ente(piece_id, piece=True)
        _refuse_if_ente(folder)
        source = self._resolve_piece(piece_id)
        target_dir = self._resolve_folder_dir(folder)

        name = self._free_name(target_dir, source.stem, source.suffix)
        destination = target_dir / name
        self._move_with_sidecar(source, destination)

        new_id = name if folder == BUILTIN_FOLDER else f"{folder}/{name}"
        self._rekey_crops([(piece_id, new_id)])
        return new_id

    # -- frame crops -------------------------------------------------------- #

    def set_crop(self, piece_id, crop) -> Optional[dict]:
        """Save (``crop`` a box) or remove (``None``) a piece's frame crop.

        The crop is a note on the hub: it is kept in ``crops.json`` by piece id
        and nothing is written to the picture, so an Ente picture can be cropped
        too. The box is validated against the UPRIGHT picture (see
        ``framecrop.validate_crop``). Returns the stored box, or ``None``.
        """
        path = self._resolve_ente_piece(piece_id)
        if path is None:
            path = self._resolve_piece(piece_id)
        crops = self._load_crops()
        if crop is None:
            if crops.pop(piece_id, None) is not None:
                self._save_crops(crops)
            return None
        try:
            width, height = self._upright_dimensions(path)
        except (OSError, UnidentifiedImageError, Image.DecompressionBombError):
            raise ArtError(404, "That piece is no longer in the library.")
        try:
            box = framecrop.validate_crop(crop, width, height)
        except framecrop.CropError as error:
            raise ArtError(400, error.message)
        crops[piece_id] = box
        self._save_crops(crops)
        return box

    @property
    def crops_path(self) -> Path:
        return self.state.parent / CROPS_FILE

    def _load_crops(self) -> dict:
        """The stored crops by piece id; a missing or damaged file reads as empty
        and a malformed entry is skipped, so a bad file never breaks a scan."""
        try:
            data = json.loads(self.crops_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        raw = data.get("crops") if isinstance(data, dict) else None
        if not isinstance(raw, dict):
            return {}
        crops: dict = {}
        for piece_id, box in raw.items():
            clean = _clean_box(box)
            if isinstance(piece_id, str) and clean is not None:
                crops[piece_id] = clean
        return crops

    def _save_crops(self, crops: dict) -> None:
        payload = {"version": 1, "crops": crops}
        self._atomic_write(
            self.crops_path, json.dumps(payload, indent=2, sort_keys=True).encode("utf-8"), 0o644
        )

    def _rekey_crops(self, pairs: list[tuple[str, str]]) -> None:
        """Move the crop of each ``(old id, new id)`` to the new id, once the
        file itself has moved. Callers run under the admin lock."""
        crops = self._load_crops()
        changed = False
        for old, new in pairs:
            box = crops.pop(old, None)
            if box is not None:
                crops[new] = box
                changed = True
        if changed:
            self._save_crops(crops)

    def _prune_crops(self, crops: dict) -> None:
        """Drop crops whose picture no longer exists (a delete or rename over
        SMB, a purged trash entry, a picture gone from an Ente album).

        Existence comes from directory listings, not from decoding, so a file
        that fails to open for a moment keeps its crop. Ente ids are judged only
        while the mirror root is readable, and by the album listing, not by the
        rendition cache, so a cold cache never costs anyone a crop.
        """
        if not crops:
            return
        exists = {piece_id for _, piece_id in self._thumbnail_targets()}
        ente_ids: Optional[set[str]] = None
        if self.ente_root is not None and self.ente_cache is not None and self.ente_root.is_dir():
            ente_ids = {
                f"{album.folder}/{picture.file_id}.jpg"
                for album in self._ente_albums()
                for picture in album.pictures
            }
        kept = {}
        for piece_id, box in crops.items():
            if _is_ente_name(piece_id.partition("/")[0]):
                if ente_ids is None or piece_id in ente_ids:
                    kept[piece_id] = box
            elif piece_id in exists:
                kept[piece_id] = box
        if kept != crops:
            self._save_crops(kept)
            crops.clear()
            crops.update(kept)

    @staticmethod
    def _upright_dimensions(path: Path) -> tuple[int, int]:
        """Width and height of the picture as shown upright (EXIF orientation 5
        to 8 swap them), read from the header without decoding."""
        with Image.open(path) as image:
            width, height = image.size
            orientation = image.getexif().get(0x0112)
        if orientation in (5, 6, 7, 8):
            return height, width
        return width, height

    # -- folders ----------------------------------------------------------- #

    def create_folder(self, name: str) -> str:
        """Create an empty user folder. ``Collection`` and an existing folder are
        both a 409."""
        _refuse_if_ente(name, creating=True)
        folder = self._validate_folder_name(name)
        if folder == BUILTIN_FOLDER:
            raise ArtError(409, "A folder named Collection already exists.")
        path = self.root / folder
        if path.exists():
            raise ArtError(409, "A folder with that name already exists.")
        path.mkdir(parents=True)
        self._chmod_dir(path)
        return folder

    def rename_folder(self, old: str, new: str) -> str:
        """Rename a user folder, carrying its rotation-enabled state. Collection
        cannot be renamed; renaming onto an existing name is a 409."""
        _refuse_if_ente(old)
        _refuse_if_ente(new, creating=True)
        old_name = self._validate_folder_name(old)
        new_name = self._validate_folder_name(new)
        if old_name == BUILTIN_FOLDER:
            raise ArtError(400, "The Collection folder cannot be renamed.")
        if new_name == BUILTIN_FOLDER:
            raise ArtError(409, "A folder named Collection already exists.")
        source = self.root / old_name
        if not source.is_dir():
            raise ArtError(404, "That folder does not exist.")
        destination = self.root / new_name
        if destination.exists() and new_name != old_name:
            raise ArtError(409, "A folder with that name already exists.")
        os.rename(source, destination)
        prefix = f"{old_name}/"
        self._rekey_crops(
            [
                (piece_id, f"{new_name}/{piece_id[len(prefix):]}")
                for piece_id in self._load_crops()
                if piece_id.startswith(prefix)
            ]
        )

        control = self._load_state()
        folders = control["settings"]["folders_enabled"]
        if old_name in folders:
            folders = [new_name if f == old_name else f for f in folders]
            control["settings"]["folders_enabled"] = folders
            self._save_state(control)
        return new_name

    def delete_folder(self, name: str) -> str:
        """Remove an empty user folder and drop its enabled state. A non-empty
        folder is a 409; Collection cannot be removed."""
        _refuse_if_ente(name)
        folder = self._validate_folder_name(name)
        if folder == BUILTIN_FOLDER:
            raise ArtError(400, "The Collection folder cannot be removed.")
        path = self.root / folder
        if not path.is_dir():
            raise ArtError(404, "That folder does not exist.")
        if any(path.iterdir()):
            raise ArtError(409, "That folder is not empty.")
        path.rmdir()

        control = self._load_state()
        folders = control["settings"]["folders_enabled"]
        if folder in folders:
            control["settings"]["folders_enabled"] = [f for f in folders if f != folder]
            self._save_state(control)
        return folder

    def set_folder_enabled(self, name: str, enabled: bool) -> bool:
        """Turn rotation on or off for a folder (stored by name)."""
        folder = self._validate_folder_name(name)
        if _is_ente_name(folder):
            # The reserved prefix is never an art folder: only a mirrored album.
            if not self._ente_album_exists(folder):
                raise ArtError(404, "That folder does not exist.")
        elif folder != BUILTIN_FOLDER and not (self.root / folder).is_dir():
            raise ArtError(404, "That folder does not exist.")
        control = self._load_state()
        folders = list(control["settings"]["folders_enabled"])
        if enabled and folder not in folders:
            folders.append(folder)
        elif not enabled and folder in folders:
            folders = [f for f in folders if f != folder]
        control["settings"]["folders_enabled"] = folders
        self._save_state(control)
        return enabled

    # -- settings and commands -------------------------------------------- #

    def set_interval(self, minutes: int) -> int:
        """Set the rotation interval; only the allowed set is accepted."""
        if minutes not in ALLOWED_INTERVALS:
            raise ArtError(400, "That is not one of the allowed rotation intervals.")
        control = self._load_state()
        control["settings"]["rotate_minutes"] = minutes
        self._save_state(control)
        return minutes

    def command(
        self, action: str, piece: Optional[str] = None, frame: Optional[str] = None
    ) -> dict:
        """Issue a rotation command (``next``, ``previous``, or ``show``).

        ``show`` requires a piece id that is a landscape image still in the
        library; a portrait piece is a 409 and nothing is recorded. The sequence
        number increases by one per accepted command, so a frame acts on a seq it
        has not applied.

        ``frame`` scopes the command to one frame: a frame acts on the command only
        when ``frame`` is absent (``null``, every frame) or equals its own id. The
        value is a frame id (its format already checked by the caller) or ``None``;
        it is recorded as-is so each frame can decide whether the command is for it.
        """
        if action not in ("next", "previous", "show"):
            raise ArtError(400, "That is not a valid rotation command.")
        control = self._load_state()
        if action == "show":
            if not piece:
                raise ArtError(400, "No piece was given to show.")
            # An Ente piece is shown whatever its shape: the frame exempts the
            # reserved folders from the landscape rule.
            if self._resolve_ente_piece(piece) is None:
                path = self._resolve_piece(piece)
                width, height = self._dimensions(path)
                if piece not in self._load_crops() and not self._is_landscape(
                    width, height, control["settings"]["min_aspect"]
                ):
                    raise ArtError(409, "That piece is too tall for the frame.")
        else:
            piece = None

        last = control.get("command")
        seq = (last["seq"] if last else 0) + 1
        command = {
            "seq": seq,
            "action": action,
            "piece": piece,
            "frame": frame if isinstance(frame, str) else None,
            "issued_at": self._now_iso(),
        }
        control["command"] = command
        self._save_state(control)
        return command

    def control(self) -> dict:
        """The ``/art-control.json`` document: the persisted settings and the
        last command (``null`` until the first one)."""
        return self._load_state()

    # -- validation -------------------------------------------------------- #

    def _split_id(self, piece_id) -> tuple[str, str]:
        """Parse a piece id into (folder, filename), rejecting anything unsafe.

        A valid id is one safe filename (a Collection piece) or ``folder/file``
        (one level). Absolute paths, ``..``, backslashes, null bytes, hidden or
        non-ASCII names, and deeper nesting are all a 400. The folder, if any, is
        validated as a folder name and the file against the safe-file rule.
        """
        if not isinstance(piece_id, str) or not piece_id:
            raise ArtError(400, "That is not a valid piece id.")
        if piece_id.startswith("/") or "\\" in piece_id or "\x00" in piece_id:
            raise ArtError(400, "That is not a valid piece id.")
        segments = piece_id.split("/")
        if any(seg in ("", ".", "..") for seg in segments):
            raise ArtError(400, "That is not a valid piece id.")
        if len(segments) == 1:
            return BUILTIN_FOLDER, self._validate_file_segment(segments[0])
        if len(segments) == 2:
            folder = self._validate_folder_name(segments[0])
            return folder, self._validate_file_segment(segments[1])
        raise ArtError(400, "That is not a valid piece id.")

    def _validate_file_segment(self, name: str) -> str:
        if not _FILE_NAME_RE.match(name):
            raise ArtError(400, "That is not a valid piece id.")
        return name

    def _validate_folder_name(self, name) -> str:
        if not isinstance(name, str):
            raise ArtError(400, "That is not a valid folder name.")
        stripped = name.strip()
        if stripped != name or not _FOLDER_NAME_RE.match(name) or name.startswith("."):
            raise ArtError(400, "That is not a valid folder name.")
        return name

    def _resolve_piece(self, piece_id) -> Path:
        """The on-disk path for a piece id, confirmed inside the library root."""
        folder, name = self._split_id(piece_id)
        path = (self.root if folder == BUILTIN_FOLDER else self.root / folder) / name
        self._assert_within_root(path, self.root)
        if not path.is_file():
            raise ArtError(404, "That piece is no longer in the library.")
        return path

    def _resolve_trash(self, trash_id) -> Path:
        """The on-disk path for a trash id (``.trash/<stamp>/<file>``)."""
        if not isinstance(trash_id, str) or "\\" in trash_id or "\x00" in trash_id:
            raise ArtError(400, "That is not a valid trash id.")
        segments = trash_id.split("/")
        if len(segments) != 3 or any(seg in ("", ".", "..") for seg in segments):
            raise ArtError(400, "That is not a valid trash id.")
        if segments[0] != TRASH_DIR or not _TRASH_STAMP_RE.match(segments[1]):
            raise ArtError(400, "That is not a valid trash id.")
        self._validate_file_segment(segments[2])
        path = self.root / TRASH_DIR / segments[1] / segments[2]
        self._assert_within_root(path, self.root / TRASH_DIR)
        if not path.is_file():
            raise ArtError(404, "That piece is no longer in the trash.")
        return path

    def _resolve_folder_dir(self, folder) -> Path:
        """The directory for a folder operand, requiring a user folder to exist."""
        name = self._validate_folder_name(folder)
        if name == BUILTIN_FOLDER:
            return self.root
        path = self.root / name
        if not path.is_dir():
            raise ArtError(404, "That folder does not exist.")
        return path

    @staticmethod
    def _assert_within_root(path: Path, root: Path) -> None:
        try:
            path.resolve().relative_to(root.resolve())
        except ValueError:
            raise ArtError(400, "That path is outside the library.")

    # -- sidecars and names ----------------------------------------------- #

    def _read_sidecar(self, image: Path) -> tuple[Optional[str], Optional[str], Optional[str]]:
        sidecar = image.with_name(image.name + ".txt")
        try:
            text = sidecar.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None, None, None
        lines = text.splitlines()
        title = lines[0].strip() if len(lines) >= 1 and lines[0].strip() else None
        artist = lines[1].strip() if len(lines) >= 2 and lines[1].strip() else None
        source_url = None
        if len(lines) >= 3 and lines[2].strip().startswith("https://"):
            source_url = lines[2].strip()
        return title, artist, source_url

    @staticmethod
    def _sanitize_stem(filename: str) -> str:
        stem = Path(filename or "").stem.lower()
        stem = re.sub(r"[^a-z0-9]+", "-", stem).strip("-")
        return stem or "image"

    @staticmethod
    def _free_name(directory: Path, stem: str, extension: str) -> str:
        """A name ``stem+extension`` free in ``directory``, suffixed on clashes."""
        candidate = f"{stem}{extension}"
        if not (directory / candidate).exists():
            return candidate
        index = 1
        while (directory / f"{stem}-{index}{extension}").exists():
            index += 1
        return f"{stem}-{index}{extension}"

    def _move_with_sidecar(self, source: Path, destination: Path) -> None:
        os.rename(source, destination)
        sidecar = source.with_name(source.name + ".txt")
        if sidecar.exists():
            os.rename(sidecar, destination.with_name(destination.name + ".txt"))

    # -- thumbnails -------------------------------------------------------- #

    @staticmethod
    def _thumbnail_name(piece_id: str, size: int, mtime: int) -> str:
        """The thumbnail file name for a piece: a hash of id, size, and mtime, so
        an edited file gets a fresh thumbnail and an unchanged one reuses its own.
        Pure (no I/O), so a metadata-only scan can name a thumbnail it has not
        generated yet."""
        key = hashlib.sha256(f"{piece_id}\x1f{size}\x1f{mtime}".encode("utf-8")).hexdigest()[:32]
        return f"{key}.jpg"

    def _ensure_thumbnail(self, path: Path, piece_id: str, size: int, mtime: int) -> str:
        """Return the thumbnail file name for a piece, creating it if absent.

        May raise ``OSError`` or a Pillow error if the source vanished or is not
        decodable; the caller skips the piece in that case.
        """
        name = self._thumbnail_name(piece_id, size, mtime)
        target = self.thumbs / name
        need_thumb = not target.is_file()
        need_auto = self._read_auto_crop(name) is _NO_CACHE
        if not need_thumb and not need_auto:
            return name
        with Image.open(path) as image:
            image.load()
            upright = ImageOps.exif_transpose(image) or image
            if need_auto:
                # The automatic frame box, measured once on the upright picture
                # and cached beside the thumbnail so an index build never
                # recomputes it. A picture that cannot be measured caches null.
                self._write_auto_crop(name, framecrop.auto_crop(upright))
            if need_thumb:
                thumb = upright if upright.mode == "RGB" else upright.convert("RGB")
                thumb.thumbnail((THUMB_LONG_SIDE, THUMB_LONG_SIDE))
                buffer = BytesIO()
                thumb.save(buffer, format="JPEG", quality=85)
                self.thumbs.mkdir(parents=True, exist_ok=True)
                self._atomic_write(target, buffer.getvalue(), 0o644)
        return name

    def _derived_complete(self, thumb_name: str) -> bool:
        """True when the thumbnail and its automatic-box cache both exist."""
        return (self.thumbs / thumb_name).is_file() and (
            self._read_auto_crop(thumb_name) is not _NO_CACHE
        )

    def _auto_crop_path(self, thumb_name: str) -> Path:
        return self.thumbs / (thumb_name[: -len(".jpg")] + AUTO_CROP_SUFFIX)

    def _read_auto_crop(self, thumb_name: str):
        """The cached automatic box, ``None`` for a cached "cannot measure", or
        ``_NO_CACHE`` when there is no valid cache for this thumbnail yet."""
        try:
            data = json.loads(self._auto_crop_path(thumb_name).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return _NO_CACHE
        if not isinstance(data, dict) or data.get("version") != AUTO_CROP_VERSION:
            return _NO_CACHE
        box = data.get("auto_crop")
        if box is None:
            return None
        clean = _clean_box(box)
        return clean if clean is not None else _NO_CACHE

    def _auto_crop_if_cached(self, thumb_name: str) -> Optional[dict]:
        cached = self._read_auto_crop(thumb_name)
        return None if cached is _NO_CACHE else cached

    def _write_auto_crop(self, thumb_name: str, box: Optional[dict]) -> None:
        payload = {"version": AUTO_CROP_VERSION, "auto_crop": box}
        self.thumbs.mkdir(parents=True, exist_ok=True)
        self._atomic_write(
            self._auto_crop_path(thumb_name), json.dumps(payload).encode("utf-8"), 0o644
        )

    def _prune_thumbnails(self, referenced: set[str]) -> None:
        try:
            entries = list(self.thumbs.iterdir())
        except OSError:
            return
        for entry in entries:
            if entry.name in referenced:
                continue
            if entry.name.endswith(AUTO_CROP_SUFFIX) and (
                entry.name[: -len(AUTO_CROP_SUFFIX)] + ".jpg" in referenced
            ):
                continue
            try:
                entry.unlink()
            except OSError:
                pass

    # -- trash bookkeeping ------------------------------------------------- #

    def _purge_trash(self) -> None:
        trash_root = self.root / TRASH_DIR
        now = self._clock()
        for stamp_dir in self._iter_dir(trash_root):
            if not stamp_dir.is_dir():
                continue
            meta = self._read_trash_meta(stamp_dir)
            deleted_ts = meta.get("deleted_ts")
            if not isinstance(deleted_ts, (int, float)):
                continue
            if now - deleted_ts > TRASH_RETENTION_SECONDS:
                shutil.rmtree(stamp_dir, ignore_errors=True)

    def _read_trash_meta(self, stamp_dir: Path) -> dict:
        try:
            raw = (stamp_dir / _TRASH_META).read_text(encoding="utf-8")
            meta = json.loads(raw)
        except (OSError, ValueError):
            return {}
        return meta if isinstance(meta, dict) else {}

    def _trash_stamp(self) -> str:
        return datetime.fromtimestamp(self._clock()).strftime("%Y-%m-%dT%H-%M-%S")

    @staticmethod
    def _unique_dir(parent: Path, stamp: str) -> Path:
        candidate = parent / stamp
        if not candidate.exists():
            return candidate
        index = 1
        while (parent / f"{stamp}-{index}").exists():
            index += 1
        return parent / f"{stamp}-{index}"

    # -- state persistence ------------------------------------------------- #

    def _default_state(self) -> dict:
        return {
            "settings": {
                "rotate_minutes": DEFAULT_INTERVAL,
                "folders_enabled": [BUILTIN_FOLDER],
                "min_aspect": MIN_ASPECT,
                "ente_seen": [],
            },
            "command": None,
        }

    def _load_state(self) -> dict:
        try:
            raw = self.state.read_text(encoding="utf-8")
            data = json.loads(raw)
        except (OSError, ValueError):
            return self._default_state()
        if not isinstance(data, dict):
            return self._default_state()
        return self._normalize_state(data)

    def _normalize_state(self, data: dict) -> dict:
        state = self._default_state()
        settings = data.get("settings")
        if isinstance(settings, dict):
            minutes = settings.get("rotate_minutes")
            if minutes in ALLOWED_INTERVALS:
                state["settings"]["rotate_minutes"] = minutes
            folders = settings.get("folders_enabled")
            if isinstance(folders, list):
                state["settings"]["folders_enabled"] = [f for f in folders if isinstance(f, str)]
            seen = settings.get("ente_seen")
            if isinstance(seen, list):
                state["settings"]["ente_seen"] = [f for f in seen if isinstance(f, str)]
            min_aspect = settings.get("min_aspect")
            if (
                isinstance(min_aspect, (int, float))
                and not isinstance(min_aspect, bool)
                and math.isfinite(min_aspect)
                and min_aspect > 0
            ):
                state["settings"]["min_aspect"] = min_aspect
        command = data.get("command")
        if isinstance(command, dict) and isinstance(command.get("seq"), int):
            # ``frame`` is additive: a command stored before per-frame commands has
            # no ``frame`` key. Default it to ``null`` (every frame) so the
            # published document always carries the key and a consumer reads it
            # uniformly; a non-string value is coerced to ``null`` as well.
            frame = command.get("frame")
            command["frame"] = frame if isinstance(frame, str) else None
            state["command"] = command
        return state

    def _save_state(self, control: dict) -> None:
        self.state.parent.mkdir(parents=True, exist_ok=True)
        self._atomic_write(self.state, json.dumps(control, indent=2).encode("utf-8"), 0o644)

    # -- shared helpers ---------------------------------------------------- #

    @staticmethod
    def _encode_path(relative: str) -> str:
        """Percent-encode each segment of a relative path, keeping the slashes.

        Folder names may contain spaces, so a raw ``My Folder/x.jpg`` is not a
        usable URL path. Each segment is quoted (nothing left safe), so a reader
        can fetch the piece or its thumbnail without guessing the encoding.
        """
        return "/".join(urllib.parse.quote(segment, safe="") for segment in relative.split("/"))

    def _art_url(self, piece_id: str) -> str:
        return "/art/" + self._encode_path(piece_id)

    def _thumb_url(self, thumb_name: str) -> str:
        return "/art-thumbs/" + self._encode_path(thumb_name)

    def _thumb_url_if_present(self, thumb_name: str) -> Optional[str]:
        """The thumbnail URL if the file exists, otherwise ``None`` (the
        metadata-only case: the thumbnail has not been generated yet)."""
        if (self.thumbs / thumb_name).is_file():
            return self._thumb_url(thumb_name)
        return None

    @staticmethod
    def _is_landscape(width: int, height: int, min_aspect: float) -> bool:
        return height > 0 and (width / height) >= min_aspect

    @staticmethod
    def _dimensions(path: Path) -> tuple[int, int]:
        with Image.open(path) as image:
            return image.size

    @staticmethod
    def _iter_dir(directory: Path):
        try:
            return sorted(directory.iterdir(), key=lambda p: p.name)
        except OSError:
            return []

    def _now_iso(self) -> str:
        return datetime.fromtimestamp(self._clock()).astimezone().isoformat()

    @staticmethod
    def _chmod_dir(path: Path) -> None:
        # setgid plus group-writable, matching the SMB share's directory mask so
        # files created here keep the art group and stay writable over SMB.
        try:
            os.chmod(path, 0o2775)
        except OSError:
            pass

    @staticmethod
    def _atomic_write(target: Path, data: bytes, mode: int) -> None:
        """Write ``data`` to ``target`` atomically (temp file then ``os.replace``).

        A reader (nginx, the frame, an SMB client) never sees a half-written
        file: either the old one or the complete new one. The temp file is
        cleaned up on any failure so a crash mid-write leaves nothing behind.
        """
        directory = target.parent
        directory.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=directory, prefix=".art.", suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
                os.fchmod(handle.fileno(), mode)
            os.replace(tmp_name, target)
        except BaseException:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise
