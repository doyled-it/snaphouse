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

_FOLDER_NAME_RE = re.compile(r"^[A-Za-z0-9 _-]{1,40}$")
_FILE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_TRASH_STAMP_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}-[0-9]{2}-[0-9]{2}(-[0-9]+)?$")

_TRASH_META = "meta.json"


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
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.root = Path(root)
        self.thumbs = Path(thumbs)
        self.state = Path(state)
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
                if entry.name in (TRASH_DIR, BUILTIN_FOLDER):
                    continue
                folder_counts.setdefault(entry.name, 0)
                for child in self._iter_dir(entry):
                    if child.is_file():
                        piece = self._build_piece(
                            child, entry.name, referenced, ensure_thumbnails, min_aspect
                        )
                        if piece is not None:
                            pieces.append(piece)
                            folder_counts[entry.name] += 1
            elif entry.is_file():
                piece = self._build_piece(
                    entry, BUILTIN_FOLDER, referenced, ensure_thumbnails, min_aspect
                )
                if piece is not None:
                    pieces.append(piece)
                    folder_counts[BUILTIN_FOLDER] += 1

        pieces.sort(key=lambda piece: piece["id"])
        folders = self._build_folders(folder_counts, enabled)
        trash = self._build_trash(referenced, ensure_thumbnails)
        self._prune_thumbnails(referenced)

        return {
            "generated_at": self._now_iso(),
            "folders": folders,
            "pieces": pieces,
            "trash": trash,
        }

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
            "landscape": self._is_landscape(width, height, min_aspect),
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
        for path, piece_id in self._thumbnail_targets():
            if should_stop is not None and should_stop():
                break
            try:
                stat = path.stat()
                name = self._thumbnail_name(piece_id, stat.st_size, int(stat.st_mtime))
                if (self.thumbs / name).is_file():
                    continue
                self._ensure_thumbnail(path, piece_id, stat.st_size, int(stat.st_mtime))
            except (OSError, UnidentifiedImageError, Image.DecompressionBombError):
                continue
            generated += 1
            if on_batch is not None and batch_size > 0 and generated % batch_size == 0:
                on_batch()
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
                elif entry.name == BUILTIN_FOLDER:
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
        source = self._resolve_piece(piece_id)
        stamp = self._trash_stamp()
        trash_dir = self._unique_dir(self.root / TRASH_DIR, stamp)
        trash_dir.mkdir(parents=True, exist_ok=True)
        self._chmod_dir(trash_dir)

        destination = trash_dir / source.name
        self._move_with_sidecar(source, destination)
        meta = {
            "original": piece_id,
            "deleted_at": self._now_iso(),
            "deleted_ts": self._clock(),
        }
        self._atomic_write(
            trash_dir / _TRASH_META, json.dumps(meta).encode("utf-8"), 0o664
        )
        return f"{TRASH_DIR}/{trash_dir.name}/{source.name}"

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

        folder, _ = self._split_id(original)
        target_dir = self.root if folder == BUILTIN_FOLDER else self.root / folder
        if not target_dir.exists():
            target_dir.mkdir(parents=True, exist_ok=True)
            self._chmod_dir(target_dir)

        name = self._free_name(target_dir, Path(trash_file.name).stem, trash_file.suffix)
        destination = target_dir / name
        self._move_with_sidecar(trash_file, destination)
        shutil.rmtree(trash_dir, ignore_errors=True)

        if folder == BUILTIN_FOLDER:
            return name
        return f"{folder}/{name}"

    def move(self, piece_id: str, folder: str) -> str:
        """Move a piece and its sidecar into ``folder`` (``Collection`` is the
        top level); return the new id, suffixed on a name collision."""
        source = self._resolve_piece(piece_id)
        target_dir = self._resolve_folder_dir(folder)

        name = self._free_name(target_dir, source.stem, source.suffix)
        destination = target_dir / name
        self._move_with_sidecar(source, destination)

        if folder == BUILTIN_FOLDER:
            return name
        return f"{folder}/{name}"

    # -- folders ----------------------------------------------------------- #

    def create_folder(self, name: str) -> str:
        """Create an empty user folder. ``Collection`` and an existing folder are
        both a 409."""
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
        if folder != BUILTIN_FOLDER and not (self.root / folder).is_dir():
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

    def command(self, action: str, piece: Optional[str] = None) -> dict:
        """Issue a rotation command (``next``, ``previous``, or ``show``).

        ``show`` requires a piece id that is a landscape image still in the
        library; a portrait piece is a 409 and nothing is recorded. The sequence
        number increases by one per accepted command, so a frame acts on a seq it
        has not applied.
        """
        if action not in ("next", "previous", "show"):
            raise ArtError(400, "That is not a valid rotation command.")
        control = self._load_state()
        if action == "show":
            if not piece:
                raise ArtError(400, "No piece was given to show.")
            path = self._resolve_piece(piece)
            width, height = self._dimensions(path)
            if not self._is_landscape(width, height, control["settings"]["min_aspect"]):
                raise ArtError(409, "That piece is too tall for the frame.")
        else:
            piece = None

        last = control.get("command")
        seq = (last["seq"] if last else 0) + 1
        command = {
            "seq": seq,
            "action": action,
            "piece": piece,
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
        if target.is_file():
            return name
        with Image.open(path) as image:
            image.load()
            thumb = ImageOps.exif_transpose(image) or image
            if thumb.mode != "RGB":
                thumb = thumb.convert("RGB")
            thumb.thumbnail((THUMB_LONG_SIDE, THUMB_LONG_SIDE))
            buffer = BytesIO()
            thumb.save(buffer, format="JPEG", quality=85)
        self.thumbs.mkdir(parents=True, exist_ok=True)
        self._atomic_write(target, buffer.getvalue(), 0o644)
        return name

    def _prune_thumbnails(self, referenced: set[str]) -> None:
        try:
            entries = list(self.thumbs.iterdir())
        except OSError:
            return
        for entry in entries:
            if entry.name in referenced:
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
            min_aspect = settings.get("min_aspect")
            if isinstance(min_aspect, (int, float)) and not isinstance(min_aspect, bool) and min_aspect > 0:
                state["settings"]["min_aspect"] = min_aspect
        command = data.get("command")
        if isinstance(command, dict) and isinstance(command.get("seq"), int):
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
