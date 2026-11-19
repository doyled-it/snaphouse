"""Downscaled JPEG renditions of Ente pictures."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from PIL import Image

from snaphouse.enteexport import EntePicture
from snaphouse.enterender import (
    RENDITION_LONG_SIDE,
    ensure_rendition,
    prune_renditions,
    register_heif,
    rendition_path,
)

MAX_PIXELS = 100_000_000


def _picture(src: Path, file_id: str = "f1", mtime: int = 1_700_000_000) -> EntePicture:
    return EntePicture(
        file_id=file_id,
        source=src,
        album_id="a1",
        mtime=mtime,
        size=src.stat().st_size,
    )


def _make(tmp_path: Path, name: str, size: tuple[int, int], mode: str = "RGB", **save) -> Path:
    path = tmp_path / "src" / name
    path.parent.mkdir(exist_ok=True)
    Image.new(mode, size, (200, 30, 30, 255) if mode == "RGBA" else (200, 30, 30)).save(path, **save)
    return path


def test_rendition_downscales_long_side_to_1600_and_is_jpeg(tmp_path):
    src = _make(tmp_path, "big.png", (4000, 3000))
    cache = tmp_path / "cache"
    out = ensure_rendition(cache, "ente-a1", _picture(src), max_pixels=MAX_PIXELS)
    assert out == rendition_path(cache, "ente-a1", _picture(src))
    assert out == cache / "ente-a1" / "f1.jpg"
    assert RENDITION_LONG_SIDE == 1600
    with Image.open(out) as im:
        assert im.format == "JPEG"
        assert im.size == (1600, 1200)


def test_small_image_not_upscaled(tmp_path):
    src = _make(tmp_path, "small.png", (300, 200))
    out = ensure_rendition(tmp_path / "c", "ente-a1", _picture(src), max_pixels=MAX_PIXELS)
    with Image.open(out) as im:
        assert im.size == (300, 200)


def test_exif_orientation_applied(tmp_path):
    src = tmp_path / "src" / "rot.jpg"
    src.parent.mkdir()
    img = Image.new("RGB", (200, 100), (10, 200, 10))
    exif = Image.Exif()
    exif[0x0112] = 6
    img.save(src, exif=exif)
    out = ensure_rendition(tmp_path / "c", "ente-a1", _picture(src), max_pixels=MAX_PIXELS)
    with Image.open(out) as im:
        assert im.size == (100, 200)


def test_heic_source_becomes_jpeg(tmp_path):
    pillow_heif = pytest.importorskip("pillow_heif")
    pillow_heif.register_heif_opener()
    src = tmp_path / "src" / "p.heic"
    src.parent.mkdir()
    img = Image.new("RGB", (200, 100), (10, 10, 200))
    exif = Image.Exif()
    exif[0x0112] = 6
    img.save(src, format="HEIF", exif=exif.tobytes())
    out = ensure_rendition(tmp_path / "c", "ente-a1", _picture(src), max_pixels=MAX_PIXELS)
    assert out is not None
    with Image.open(out) as im:
        assert im.format == "JPEG"
        assert im.size == (100, 200)


def test_alpha_png_flattened_to_rgb(tmp_path):
    src = tmp_path / "src" / "a.png"
    src.parent.mkdir()
    Image.new("RGBA", (50, 50), (0, 0, 0, 0)).save(src)
    out = ensure_rendition(tmp_path / "c", "ente-a1", _picture(src), max_pixels=MAX_PIXELS)
    with Image.open(out) as im:
        assert im.mode == "RGB"
        assert im.getpixel((10, 10)) == (255, 255, 255)


def test_unchanged_source_is_not_regenerated(tmp_path):
    src = _make(tmp_path, "s.png", (400, 300))
    pic = _picture(src)
    out = ensure_rendition(tmp_path / "c", "ente-a1", pic, max_pixels=MAX_PIXELS)
    before = out.stat()
    assert before.st_mtime == pic.mtime
    again = ensure_rendition(tmp_path / "c", "ente-a1", pic, max_pixels=MAX_PIXELS)
    after = again.stat()
    assert (after.st_mtime, after.st_ino) == (before.st_mtime, before.st_ino)


def test_changed_source_mtime_regenerates(tmp_path):
    src = _make(tmp_path, "s.png", (400, 300))
    out = ensure_rendition(tmp_path / "c", "ente-a1", _picture(src), max_pixels=MAX_PIXELS)
    new = _make(tmp_path, "s.png", (200, 100))
    out2 = ensure_rendition(
        tmp_path / "c", "ente-a1", _picture(new, mtime=1_700_000_500), max_pixels=MAX_PIXELS
    )
    assert out2 == out
    assert out2.stat().st_mtime == 1_700_000_500
    with Image.open(out2) as im:
        assert im.size == (200, 100)


def test_corrupt_zero_byte_and_oversized_return_none(tmp_path):
    cache = tmp_path / "c"
    corrupt = tmp_path / "src" / "bad.jpg"
    corrupt.parent.mkdir()
    corrupt.write_bytes(b"not an image at all")
    empty = tmp_path / "src" / "empty.jpg"
    empty.write_bytes(b"")
    big = _make(tmp_path, "big.png", (100, 100))
    assert ensure_rendition(cache, "ente-a1", _picture(corrupt, "c1"), max_pixels=MAX_PIXELS) is None
    assert ensure_rendition(cache, "ente-a1", _picture(empty, "c2"), max_pixels=MAX_PIXELS) is None
    assert ensure_rendition(cache, "ente-a1", _picture(big, "c3"), max_pixels=50) is None
    assert not list(cache.rglob("*.jpg"))


def test_missing_source_returns_none(tmp_path):
    pic = EntePicture("m1", tmp_path / "nope.jpg", "a1", 1, 0)
    assert ensure_rendition(tmp_path / "c", "ente-a1", pic, max_pixels=MAX_PIXELS) is None


def test_prune_removes_unreferenced_and_empty_dirs(tmp_path):
    cache = tmp_path / "c"
    (cache / "ente-a1").mkdir(parents=True)
    (cache / "ente-a2").mkdir()
    keep = cache / "ente-a1" / "k.jpg"
    drop = cache / "ente-a1" / "d.jpg"
    other = cache / "ente-a2" / "x.jpg"
    for p in (keep, drop, other):
        p.write_bytes(b"x")
    removed = prune_renditions(cache, {keep})
    assert removed == 2
    assert keep.exists() and not drop.exists()
    assert not (cache / "ente-a2").exists()
    assert (cache / "ente-a1").is_dir()


def test_prune_missing_cache_is_zero(tmp_path):
    assert prune_renditions(tmp_path / "none", set()) == 0


def test_write_is_atomic(tmp_path, monkeypatch):
    cache = tmp_path / "c"
    src = _make(tmp_path, "s.png", (400, 300))
    out = ensure_rendition(cache, "ente-a1", _picture(src), max_pixels=MAX_PIXELS)
    assert out and not list(cache.rglob("*.tmp*"))

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", boom)
    src2 = _make(tmp_path, "t.png", (400, 300))
    res = ensure_rendition(cache, "ente-a1", _picture(src2, "f2"), max_pixels=MAX_PIXELS)
    assert res is None
    assert not list(cache.rglob("*.tmp*"))
    assert not (cache / "ente-a1" / "f2.jpg").exists()


def test_register_heif_is_idempotent():
    register_heif()
    register_heif()
