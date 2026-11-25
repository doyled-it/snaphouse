"""The hub's cover collection (lib/snaphouse/coverarchive.py).

Every image here is built from bytes in the test (a JPEG or PNG signature plus
filler); no cover or any other picture is read from, or written into, the repo.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from snaphouse.coverarchive import CoverArchive

PNG_SIG = b"\x89PNG\r\n\x1a\n"


def jpeg(n: int = 0, size: int = 32) -> bytes:
    return b"\xff\xd8\xff\xe0" + bytes([n % 256]) * size + n.to_bytes(4, "big") + b"\xff\xd9"


def png(n: int = 0) -> bytes:
    return PNG_SIG + bytes([n % 256]) * 24 + n.to_bytes(4, "big")


class Clock:
    def __init__(self) -> None:
        self.t = datetime(2026, 11, 24, 12, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        self.t += timedelta(seconds=1)
        return self.t


@pytest.fixture
def root(tmp_path: Path) -> Path:
    (tmp_path / "cover" / "archive").mkdir(parents=True)
    (tmp_path / "nowplaying").mkdir()
    return tmp_path


def make(root: Path, **kw) -> CoverArchive:
    return CoverArchive(root, clock=Clock(), **kw)


def published(root: Path) -> dict:
    """The published document; nothing published yet reads as an empty collection."""
    if not (root / "nowplaying" / "cover-archive.json").exists():
        return {"covers": []}
    return json.loads((root / "nowplaying" / "cover-archive.json").read_text())


def put(root: Path, rel: str, data: bytes) -> str:
    path = root / "cover" / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return "/cover/" + rel


def test_file_is_named_by_hash_of_the_bytes(root):
    archive = make(root)
    data = jpeg(1)
    assert archive.add_bytes(data, "Album", "Artist") is True
    name = hashlib.sha256(data).hexdigest() + ".jpg"
    assert (root / "cover" / "archive" / name).read_bytes() == data
    assert published(root)["covers"][0]["url"] == f"/cover/archive/{name}"


def test_png_is_accepted_with_png_extension(root):
    archive = make(root)
    assert archive.add_bytes(png(1), "A", "B") is True
    assert published(root)["covers"][0]["url"].endswith(".png")


def test_same_bytes_with_other_names_are_stored_once(root):
    archive = make(root)
    assert archive.add_bytes(jpeg(1), "One", "X") is True
    assert archive.add_bytes(jpeg(1), "Two", "Y") is False
    assert len(published(root)["covers"]) == 1
    assert len(list((root / "cover" / "archive").iterdir())) == 1


def test_same_album_and_artist_normalized_adds_no_second_file(root):
    archive = make(root)
    assert archive.add_bytes(jpeg(1), "Abbey  Road!", "The Beatles") is True
    assert archive.add_bytes(jpeg(2), "abbey road", "the  beatles.") is False
    assert len(list((root / "cover" / "archive").iterdir())) == 1


def test_different_artist_same_album_is_kept(root):
    archive = make(root)
    assert archive.add_bytes(jpeg(1), "Greatest Hits", "A") is True
    assert archive.add_bytes(jpeg(2), "Greatest Hits", "B") is True


def test_podcast_and_audiobook_are_left_out(root):
    archive = make(root)
    a = put(root, "lookup/a.jpg", jpeg(1))
    b = put(root, "lookup/b.jpg", jpeg(2))
    c = put(root, "lookup/c.jpg", jpeg(3))
    assert archive.add_file(a, "Show", "Host", kind="long_form", genre=["Podcast"]) is False
    assert archive.add_file(b, "Book", "Author", kind="long_form", genre=["Audiobook"]) is False
    assert archive.add_file(c, "Spoken", "Host", kind="long_form", genre=None) is False
    assert published(root)["covers"] == []


def test_music_and_unknown_kind_are_included(root):
    archive = make(root)
    a = put(root, "lookup/a.jpg", jpeg(1))
    b = put(root, "lookup/b.jpg", jpeg(2))
    assert archive.add_file(a, "Al", "Ar", kind="music", genre=["Rock"]) is True
    assert archive.add_file(b, "Al2", "Ar2", kind=None, genre=None) is True


def test_newest_first_and_json_shape(root):
    archive = make(root)
    archive.add_bytes(jpeg(1), "One", "A")
    archive.add_bytes(jpeg(2), "Two", "B")
    doc = published(root)
    assert list(doc) == ["covers"]
    assert [c["album"] for c in doc["covers"]] == ["Two", "One"]
    cover = doc["covers"][0]
    assert set(cover) == {"url", "album", "artist", "added_at"}
    assert cover["artist"] == "B"
    assert cover["added_at"] > doc["covers"][1]["added_at"]


def test_cap_drops_oldest_and_deletes_its_file(root):
    archive = make(root, cap=3)
    for n in range(1, 5):
        archive.add_bytes(jpeg(n), f"Album {n}", f"Artist {n}")
    covers = published(root)["covers"]
    assert [c["album"] for c in covers] == ["Album 4", "Album 3", "Album 2"]
    names = {p.name for p in (root / "cover" / "archive").iterdir()}
    assert names == {c["url"].rsplit("/", 1)[1] for c in covers}


def test_publish_is_atomic_and_leaves_no_temp_files(root):
    archive = make(root)
    archive.add_bytes(jpeg(1), "A", "B")
    leftovers = [p for p in (root / "nowplaying").iterdir() if p.name != "cover-archive.json"]
    assert leftovers == []
    leftovers = [p for p in (root / "cover" / "archive").iterdir() if p.name.startswith(".")]
    assert leftovers == []
    mode = (root / "nowplaying" / "cover-archive.json").stat().st_mode & 0o777
    assert mode == 0o644


@pytest.mark.parametrize(
    "data",
    [b"", b"GIF89a....", b"<html>not an image</html>", b"\xff\xd8", PNG_SIG[:4]],
)
def test_bad_image_is_rejected(root, data):
    archive = make(root)
    assert archive.add_bytes(data, "A", "B") is False
    assert published(root)["covers"] == []
    assert list((root / "cover" / "archive").iterdir()) == []


def test_oversized_image_is_rejected(root):
    archive = make(root, max_bytes=100)
    assert archive.add_bytes(jpeg(1, size=500), "A", "B") is False
    big = put(root, "lookup/big.jpg", jpeg(2, size=500))
    assert archive.add_file(big, "A", "B") is False


@pytest.mark.parametrize(
    "url",
    [
        "/cover/../../etc/passwd",
        "/cover/lookup/../../../secret.jpg",
        "/other/a.jpg",
        "http://evil.example/cover/a.jpg",
        "",
        None,
        "/cover/",
    ],
)
def test_art_url_cannot_escape_the_cover_directory(root, url):
    outside = root.parent / "secret.jpg"
    outside.write_bytes(jpeg(9))
    archive = make(root)
    assert archive.add_file(url, "A", "B") is False
    assert published(root)["covers"] == []


def test_symlink_out_of_the_cover_dir_is_refused(root):
    outside = root.parent / "outside.jpg"
    outside.write_bytes(jpeg(9))
    link = root / "cover" / "lookup"
    link.mkdir()
    os.symlink(outside, link / "x.jpg")
    archive = make(root)
    assert archive.add_file("/cover/lookup/x.jpg", "A", "B") is False


def test_metadata_names_never_become_paths(root):
    archive = make(root)
    archive.add_bytes(jpeg(1), "../../evil", "/etc/x")
    names = [p.name for p in (root / "cover" / "archive").iterdir()]
    assert len(names) == 1 and "evil" not in names[0]
    assert not (root / "evil").exists()


def test_long_names_are_trimmed(root):
    archive = make(root)
    archive.add_bytes(jpeg(1), "x" * 5000, "y" * 5000)
    cover = published(root)["covers"][0]
    assert len(cover["album"]) <= 200 and len(cover["artist"]) <= 200


def test_reload_keeps_the_collection_and_its_dedupe(root):
    make(root).add_bytes(jpeg(1), "Album", "Artist")
    again = make(root)
    assert again.add_bytes(jpeg(2), "ALBUM", "artist") is False
    assert again.add_bytes(jpeg(1), "", "") is False
    assert len(published(root)["covers"]) == 1


def test_corrupt_index_starts_empty(root):
    (root / "nowplaying" / "cover-archive.json").write_text("{nope")
    archive = make(root)
    assert archive.entries == []


def test_seed_from_lookup_cache_with_empty_names(root):
    put(root, "lookup/a.jpg", jpeg(1))
    put(root, "lookup/b.jpg", jpeg(2))
    (root / "cover" / "lookup" / "a.none").write_text("")  # a miss marker is not a cover
    archive = make(root)
    assert archive.seed([]) == 2
    covers = published(root)["covers"]
    assert len(covers) == 2
    assert all(c["album"] == "" and c["artist"] == "" for c in covers)


def test_seed_from_history_uses_names_and_skips_known_podcasts(root):
    music = put(root, "history/m.jpg", jpeg(1))
    pod = put(root, "history/p.jpg", jpeg(2))
    plays = [
        {"art_url": music, "album": "Al", "artist": "Ar", "kind": "music", "genre": None},
        {"art_url": pod, "album": "Show", "artist": "Host", "kind": "long_form", "genre": None},
        {"art_url": None, "album": "x", "artist": "y", "kind": "music", "genre": None},
    ]
    # The podcast's lookup original has the same bytes; it must not sneak in.
    put(root, "lookup/p.jpg", jpeg(2))
    archive = make(root)
    assert archive.seed(plays) == 1
    covers = published(root)["covers"]
    assert [(c["album"], c["artist"]) for c in covers] == [("Al", "Ar")]


def test_seed_dedupes_history_copy_and_lookup_original_by_hash(root):
    put(root, "lookup/m.jpg", jpeg(1))
    music = put(root, "history/m.jpg", jpeg(1))
    plays = [{"art_url": music, "album": "Al", "artist": "Ar", "kind": "music", "genre": None}]
    archive = make(root)
    assert archive.seed(plays) == 1
    assert published(root)["covers"][0]["album"] == "Al"


def test_empty_name_entries_dedupe_by_hash_only(root):
    archive = make(root)
    assert archive.add_bytes(jpeg(1), "", "") is True
    assert archive.add_bytes(jpeg(2), "", "") is True
    assert archive.add_bytes(jpeg(1), "", "") is False


def test_seed_does_nothing_when_collection_is_not_empty(root):
    archive = make(root)
    archive.add_bytes(jpeg(9), "A", "B")
    put(root, "lookup/a.jpg", jpeg(1))
    assert archive.seed([]) == 0
    assert len(published(root)["covers"]) == 1


def test_seed_never_reads_the_archive_directory_itself(root):
    archive = make(root)
    archive.add_bytes(jpeg(1), "A", "B")
    (root / "nowplaying" / "cover-archive.json").unlink()
    fresh = make(root)
    assert fresh.entries == []
    fresh.seed([])
    assert len(published(root)["covers"]) <= 1


def test_publish_writes_an_empty_document_at_start(root):
    make(root).publish()
    assert published(root) == {"covers": []}
