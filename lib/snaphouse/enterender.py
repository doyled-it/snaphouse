"""Downscaled JPEG renditions of Ente pictures for the frame and the art grid.

Ente exports are full resolution and may be HEIC. The art side serves a JPEG
with a 1600 px long side, generated once per picture and regenerated only when
the source's recorded mtime changes. Unreadable pictures yield None, never an
exception, so one bad file cannot stop the others.
"""

from __future__ import annotations

import os
from pathlib import Path

from PIL import Image, ImageOps

from snaphouse.enteexport import EntePicture

RENDITION_LONG_SIDE = 1600
_JPEG_QUALITY = 85
_registered = False


def register_heif() -> None:
    """Register pillow-heif's opener once; a missing package just means no HEIC."""
    global _registered
    if _registered:
        return
    try:
        import pillow_heif
    except ImportError:
        return
    pillow_heif.register_heif_opener()
    _registered = True


def rendition_path(cache: Path, folder: str, picture: EntePicture) -> Path:
    return cache / folder / f"{picture.file_id}.jpg"


def _is_current(path: Path, picture: EntePicture) -> bool:
    try:
        st = path.stat()
    except OSError:
        return False
    return st.st_size > 0 and int(st.st_mtime) == picture.mtime


def _render(source: Path, dest: Path, tmp: Path, max_pixels: int) -> None:
    with Image.open(source) as im:
        width, height = im.size
        if width * height > max_pixels:
            raise ValueError("image exceeds the pixel limit")
        im.load()
        im = ImageOps.exif_transpose(im)
        if im.mode in ("RGBA", "LA") or "transparency" in im.info:
            rgba = im.convert("RGBA")
            flat = Image.new("RGB", rgba.size, (255, 255, 255))
            flat.paste(rgba, mask=rgba.getchannel("A"))
            im = flat
        elif im.mode != "RGB":
            im = im.convert("RGB")
        im.thumbnail((RENDITION_LONG_SIDE, RENDITION_LONG_SIDE))
        im.save(tmp, format="JPEG", quality=_JPEG_QUALITY)
    os.replace(tmp, dest)


def ensure_rendition(
    cache: Path, folder: str, picture: EntePicture, *, max_pixels: int
) -> Path | None:
    dest = rendition_path(cache, folder, picture)
    if _is_current(dest, picture):
        return dest
    register_heif()
    tmp = dest.with_name(f".{dest.name}.tmp")
    try:
        if picture.source.stat().st_size == 0:
            return None
        dest.parent.mkdir(parents=True, exist_ok=True)
        _render(picture.source, dest, tmp, max_pixels)
        os.utime(dest, (picture.mtime, picture.mtime))
    except (OSError, ValueError, SyntaxError, Image.DecompressionBombError):
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        return None
    return dest


def prune_renditions(cache: Path, keep: set[Path]) -> int:
    """Delete rendition files not in keep, then empty folder directories."""
    removed = 0
    try:
        folders = [d for d in cache.iterdir() if d.is_dir() and not d.is_symlink()]
    except OSError:
        return 0
    for folder in folders:
        try:
            entries = list(folder.iterdir())
        except OSError:
            continue
        for entry in entries:
            if entry in keep:
                continue
            try:
                entry.unlink()
                removed += 1
            except OSError:
                pass
        try:
            folder.rmdir()
        except OSError:
            pass
    return removed
