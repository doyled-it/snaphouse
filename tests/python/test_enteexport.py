"""Scan the Ente export mirror into albums and pictures."""

import json
import re
from pathlib import Path

from snaphouse import enteexport
from snaphouse.enteexport import ENTE_PREFIX, scan_export


def make_album(root: Path, album_id: str, title, files: dict, *, write_files=True):
    """files maps file name (with extension) to its record's file id."""
    d = root / album_id
    d.mkdir(parents=True)
    records = {}
    for name, fid in files.items():
        ext = Path(name).suffix
        records[fid] = {"updated_at": 1, "hash": "h", "ext": ext}
        if write_files:
            (d / name).write_bytes(b"x" * 5)
    (d / "album.json").write_text(
        json.dumps({"title": title, "updated_at": 5, "files": records})
    )
    return d


def test_scan_lists_one_album_per_directory_sorted(tmp_path):
    make_album(tmp_path, "202", "Album Two", {"1.jpg": "1"})
    make_album(tmp_path, "101", "Album One", {"1.jpg": "1"})
    albums = scan_export(tmp_path)
    assert [a.folder for a in albums] == ["ente-101", "ente-202"]


def test_folder_is_ente_prefix_plus_album_id(tmp_path):
    make_album(tmp_path, "101", "Album One", {})
    (album,) = scan_export(tmp_path)
    assert ENTE_PREFIX == "ente-"
    assert album.folder == "ente-101"


def test_title_comes_from_album_json(tmp_path):
    make_album(tmp_path, "101", "Album One", {})
    (album,) = scan_export(tmp_path)
    assert album.title == "Album One"


def test_album_without_or_with_garbage_album_json_is_skipped(tmp_path):
    make_album(tmp_path, "101", "Album One", {"1.jpg": "1"})
    (tmp_path / "202").mkdir()
    (tmp_path / "202" / "1.jpg").write_bytes(b"x")
    bad = tmp_path / "303"
    bad.mkdir()
    (bad / "album.json").write_text("{not json")
    notdict = tmp_path / "404"
    notdict.mkdir()
    (notdict / "album.json").write_text("[1, 2]")
    notitle = tmp_path / "505"
    notitle.mkdir()
    (notitle / "album.json").write_text(json.dumps({"files": {}}))
    assert [a.folder for a in scan_export(tmp_path)] == ["ente-101"]


def test_only_recorded_files_that_exist_with_image_extensions(tmp_path):
    d = make_album(
        tmp_path,
        "101",
        "Album One",
        {"1.jpg": "1", "2.HEIC": "2", "3.MP": "3", "4.png": "4", "6.webp": "6"},
    )
    records = json.loads((d / "album.json").read_text())
    records["files"]["5"] = {"updated_at": 1, "hash": "h", "ext": ".jpg"}  # file missing
    records["files"][".hidden"] = {"updated_at": 1, "hash": "h", "ext": ".jpg"}
    records["files"]["album"] = {"updated_at": 1, "hash": "h", "ext": ".json"}
    (d / "album.json").write_text(json.dumps(records))
    (d / ".7.jpg").write_bytes(b"x")
    (d / "8.jpg.tmp").write_bytes(b"x")
    (d / "9.jpg").write_bytes(b"x")  # on disk but not recorded
    (album,) = scan_export(tmp_path)
    assert [p.file_id for p in album.pictures] == ["1", "2", "4", "6"]
    heic = album.pictures[1]
    assert heic.source == d / "2.HEIC"
    assert heic.album_id == "101"
    assert heic.size == 5
    assert isinstance(heic.mtime, int)
    assert heic.mtime == int((d / "2.HEIC").stat().st_mtime)


def test_ids_failing_the_pattern_are_skipped(tmp_path):
    make_album(tmp_path, "101", "Album One", {"1.jpg": "1"})
    d = tmp_path / "202"
    d.mkdir()
    (d / "1.jpg").write_bytes(b"x")
    long_id = "a" * 40
    (d / "album.json").write_text(
        json.dumps(
            {
                "title": "Album Two",
                "updated_at": 1,
                "files": {
                    "1": {"updated_at": 1, "hash": "h", "ext": ".jpg"},
                    "../x": {"updated_at": 1, "hash": "h", "ext": ".jpg"},
                    long_id: {"updated_at": 1, "hash": "h", "ext": ".jpg"},
                    "": {"updated_at": 1, "hash": "h", "ext": ".jpg"},
                },
            }
        )
    )
    for bad in ("..x", long_id):
        make_album(tmp_path, bad, "Bad", {"1.jpg": "1"})
    albums = scan_export(tmp_path)
    assert [a.folder for a in albums] == ["ente-101", "ente-202"]
    assert [p.file_id for p in albums[1].pictures] == ["1"]


def test_two_albums_same_title_get_distinct_folders(tmp_path):
    make_album(tmp_path, "101", "Album One", {})
    make_album(tmp_path, "202", "Album One", {})
    albums = scan_export(tmp_path)
    assert albums[0].title == albums[1].title == "Album One"
    assert albums[0].folder != albums[1].folder


def test_title_with_emoji_and_long_text_is_kept_verbatim_but_folder_stays_valid(
    tmp_path,
):
    title = "Album \U0001f389 " + "long " * 30
    make_album(tmp_path, "101", title, {})
    (album,) = scan_export(tmp_path)
    assert album.title == title
    assert re.fullmatch(r"[A-Za-z0-9 _-]{1,40}", album.folder)


def test_symlinks_are_not_followed(tmp_path):
    outside = tmp_path / "outside"
    make_album(outside, "999", "Outside", {"1.jpg": "1"})
    root = tmp_path / "root"
    root.mkdir()
    d = make_album(root, "101", "Album One", {"1.jpg": "1"})
    (d / "2.jpg").symlink_to(outside / "999" / "1.jpg")
    (root / "202").symlink_to(outside / "999", target_is_directory=True)
    albums = scan_export(root)
    assert [a.folder for a in albums] == ["ente-101"]
    assert [p.file_id for p in albums[0].pictures] == ["1"]


def test_unreadable_or_missing_root_returns_empty(tmp_path):
    assert scan_export(tmp_path / "nope") == []
    afile = tmp_path / "afile"
    afile.write_text("x")
    assert scan_export(afile) == []
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0)
    try:
        assert scan_export(locked) == []
    finally:
        locked.chmod(0o700)


def test_module_constants():
    assert enteexport.ENTE_EXTENSIONS == {
        ".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif",
    }
    assert enteexport.ENTE_ID_RE.fullmatch("aZ09")
    assert not enteexport.ENTE_ID_RE.fullmatch("")
