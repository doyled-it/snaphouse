"""Tests for the hub's art library core (lib/snaphouse/artlib.py).

Every test works in ``tmp_path``: a throwaway library root, a thumbnail
directory, and a control-state file. Images are generated with Pillow (a dev
and hub dependency), so no test touches the real share. The clock is injected
so trash purge and timestamps are deterministic.
"""

from __future__ import annotations

import io
import json
import time
from pathlib import Path

import pytest
from PIL import Image

from snaphouse import artlib
from snaphouse.artlib import ArtError, Library


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _image_bytes(size=(400, 200), color=(120, 160, 200), fmt="JPEG", exif=None) -> bytes:
    """Encode a solid-colour image to bytes, optionally with EXIF."""
    image = Image.new("RGB", size, color)
    buffer = io.BytesIO()
    if exif is not None:
        image.save(buffer, format=fmt, exif=exif)
    else:
        image.save(buffer, format=fmt)
    return buffer.getvalue()


def _write_image(path: Path, size=(400, 200), color=(120, 160, 200), fmt="JPEG") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_image_bytes(size=size, color=color, fmt=fmt))


def _write_sidecar(image_path: Path, title="", artist="", source_url=None) -> None:
    lines = [title, artist]
    if source_url is not None:
        lines.append(source_url)
    sidecar = image_path.with_name(image_path.name + ".txt")
    sidecar.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _make_library(tmp_path: Path, clock=None) -> Library:
    root = tmp_path / "art"
    thumbs = tmp_path / "public" / "art-thumbs"
    state = tmp_path / "state" / "art-control.json"
    root.mkdir(parents=True, exist_ok=True)
    kwargs = {}
    if clock is not None:
        kwargs["clock"] = clock
    return Library(root, thumbs, state, **kwargs)


class FakeClock:
    """A settable clock returning seconds, injected as ``Library(clock=...)``."""

    def __init__(self, now: float = 1_700_000_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


# --------------------------------------------------------------------------- #
# scan: the index document
# --------------------------------------------------------------------------- #


def test_scan_lists_top_level_as_collection(tmp_path):
    library = _make_library(tmp_path)
    _write_image(library_root(library) / "met-1.jpg", size=(400, 200))

    index = library.scan()

    assert index["pieces"][0]["id"] == "met-1.jpg"
    assert index["pieces"][0]["folder"] == "Collection"
    assert index["pieces"][0]["file"] == "met-1.jpg"
    assert index["pieces"][0]["url"] == "/art/met-1.jpg"
    assert index["pieces"][0]["width"] == 400
    assert index["pieces"][0]["height"] == 200
    assert index["pieces"][0]["landscape"] is True
    collection = _folder(index, "Collection")
    assert collection["count"] == 1
    assert collection["builtin"] is True


def test_scan_lists_user_folder_pieces(tmp_path):
    library = _make_library(tmp_path)
    _write_image(library_root(library) / "Photos" / "beach.jpg")

    index = library.scan()

    piece = _piece(index, "Photos/beach.jpg")
    assert piece["folder"] == "Photos"
    assert piece["file"] == "beach.jpg"
    assert piece["url"] == "/art/Photos/beach.jpg"
    photos = _folder(index, "Photos")
    assert photos["count"] == 1
    assert photos["builtin"] is False


def test_scan_ignores_deeper_nesting(tmp_path):
    library = _make_library(tmp_path)
    _write_image(library_root(library) / "Photos" / "Trips" / "deep.jpg")

    index = library.scan()

    ids = [piece["id"] for piece in index["pieces"]]
    assert "Photos/Trips/deep.jpg" not in ids
    assert all("Trips" not in folder["name"] for folder in index["folders"])


def test_scan_reads_sidecar_caption(tmp_path):
    library = _make_library(tmp_path)
    image = library_root(library) / "met-5.jpg"
    _write_image(image)
    _write_sidecar(image, title="A Still Life", artist="A Painter",
                   source_url="https://example.org/art/5")

    piece = _piece(library.scan(), "met-5.jpg")

    assert piece["title"] == "A Still Life"
    assert piece["artist"] == "A Painter"
    assert piece["source_url"] == "https://example.org/art/5"


def test_scan_drops_non_https_source_url(tmp_path):
    library = _make_library(tmp_path)
    image = library_root(library) / "met-6.jpg"
    _write_image(image)
    _write_sidecar(image, title="T", artist="A", source_url="http://insecure/5")

    piece = _piece(library.scan(), "met-6.jpg")

    assert piece["source_url"] is None


def test_scan_title_falls_back_to_filename(tmp_path):
    library = _make_library(tmp_path)
    _write_image(library_root(library) / "untitled.jpg")

    piece = _piece(library.scan(), "untitled.jpg")

    assert piece["title"] == "untitled.jpg"
    assert piece["artist"] is None


def test_scan_skips_corrupt_image(tmp_path):
    library = _make_library(tmp_path)
    _write_image(library_root(library) / "good.jpg")
    (library_root(library) / "broken.jpg").write_bytes(b"not an image at all")

    ids = [piece["id"] for piece in library.scan()["pieces"]]

    assert "good.jpg" in ids
    assert "broken.jpg" not in ids


def test_scan_excludes_trash_from_pieces(tmp_path):
    library = _make_library(tmp_path)
    _write_image(library_root(library) / "keep.jpg")
    trashed = library_root(library) / ".trash" / "2020-01-01T00-00-00" / "old.jpg"
    _write_image(trashed)

    index = library.scan()

    ids = [piece["id"] for piece in index["pieces"]]
    assert ids == ["keep.jpg"]
    assert all(not folder["name"].startswith(".") for folder in index["folders"])


def test_scan_marks_portrait_not_landscape(tmp_path):
    library = _make_library(tmp_path)
    _write_image(library_root(library) / "tall.jpg", size=(200, 400))

    piece = _piece(library.scan(), "tall.jpg")

    assert piece["landscape"] is False


def test_scan_landscape_threshold_is_1_1(tmp_path):
    # The default rotation cutoff is 1.1 (settings.min_aspect): a 1.2 piece is
    # landscape and rotates, a 1.05 piece is not.
    library = _make_library(tmp_path)
    _write_image(library_root(library) / "wide.jpg", size=(120, 100))
    _write_image(library_root(library) / "just-under.jpg", size=(105, 100))

    index = library.scan()

    assert _piece(index, "wide.jpg")["landscape"] is True
    assert _piece(index, "just-under.jpg")["landscape"] is False


def test_scan_creates_thumbnail_file(tmp_path):
    library = _make_library(tmp_path)
    _write_image(library_root(library) / "met-9.jpg", size=(800, 400))

    piece = _piece(library.scan(), "met-9.jpg")

    assert piece["thumb"].startswith("/art-thumbs/")
    thumb_name = piece["thumb"].rsplit("/", 1)[-1]
    thumb_file = library_thumbs(library) / thumb_name
    assert thumb_file.is_file()
    with Image.open(thumb_file) as thumb:
        assert max(thumb.size) <= 320


def test_scan_prunes_unreferenced_thumbnails(tmp_path):
    library = _make_library(tmp_path)
    _write_image(library_root(library) / "met-10.jpg")
    library.scan()
    stray = library_thumbs(library) / "deadbeef.jpg"
    stray.write_bytes(b"stale")

    library.scan()

    assert not stray.exists()


def test_scan_collection_listed_even_when_empty(tmp_path):
    library = _make_library(tmp_path)

    index = library.scan()

    collection = _folder(index, "Collection")
    assert collection["count"] == 0
    assert collection["enabled"] is True


def test_scan_does_not_raise_when_file_vanishes_mid_scan(tmp_path, monkeypatch):
    # Review Focus 3: an SMB client removes a file between the directory listing
    # and the point the scanner opens it; the scan catches up and does not crash.
    library = _make_library(tmp_path)
    target = library_root(library) / "racing.jpg"
    _write_image(target)
    _write_image(library_root(library) / "survivor.jpg")

    real_open = artlib.Image.open

    def vanishing_open(path, *args, **kwargs):
        resolved = Path(path) if not hasattr(path, "read") else None
        if resolved is not None and resolved.name == "racing.jpg":
            target.unlink()
            raise FileNotFoundError(str(target))
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(artlib.Image, "open", vanishing_open)

    index = library.scan()

    ids = [piece["id"] for piece in index["pieces"]]
    assert "racing.jpg" not in ids
    assert "survivor.jpg" in ids


def test_scan_ignores_literal_collection_subdirectory(tmp_path):
    # A subdirectory named like the built-in folder (which the API refuses but an
    # SMB client can create by hand) would emit ids like "Collection/x.jpg" that
    # _resolve_piece maps back to the top level, risking a wrong-file delete or
    # move. The scan reserves the name and ignores such a directory.
    library = _make_library(tmp_path)
    _write_image(library_root(library) / "top.jpg")
    _write_image(library_root(library) / "Collection" / "sneaky.jpg")

    index = library.scan()

    ids = [piece["id"] for piece in index["pieces"]]
    assert ids == ["top.jpg"]
    assert not any(piece["id"].startswith("Collection/") for piece in index["pieces"])
    # Only the one built-in Collection folder is listed, counting top-level files.
    collection_folders = [f for f in index["folders"] if f["name"] == "Collection"]
    assert len(collection_folders) == 1
    assert collection_folders[0]["count"] == 1


def test_scan_percent_encodes_url_and_thumb(tmp_path):
    # Folder names may contain spaces, so url/thumb segments must be encoded.
    library = _make_library(tmp_path)
    _write_image(library_root(library) / "My Folder" / "a b.jpg")

    piece = _piece(library.scan(), "My Folder/a b.jpg")

    assert piece["url"] == "/art/My%20Folder/a%20b.jpg"
    assert " " not in piece["thumb"]
    # The encoded url still points at the real piece once unquoted.
    import urllib.parse

    assert urllib.parse.unquote(piece["url"]) == "/art/My Folder/a b.jpg"


# --------------------------------------------------------------------------- #
# id and folder-name validation (Review Focus 1)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "bad_id",
    [
        "../etc/passwd",
        "Photos/../../x",
        "/etc/passwd",
        ".trash/2020-01-01T00-00-00/old.jpg",
        "a/b/c.jpg",
        "Photos/sub/evil.jpg",
        "..",
        "",
        "Photos/..",
        "./met.jpg",
        "mét-lookalike.jpg",
        "back\\slash.jpg",
        "null\x00byte.jpg",
        ".hidden.jpg",
    ],
)
def test_move_rejects_bad_ids(tmp_path, bad_id):
    library = _make_library(tmp_path)
    (library_root(library) / "Photos").mkdir()
    with pytest.raises(ArtError) as excinfo:
        library.move(bad_id, "Photos")
    assert excinfo.value.status == 400


@pytest.mark.parametrize(
    "bad_name",
    [
        "has/slash",
        "has.dot",
        ".hidden",
        "",
        "a" * 41,
        "..",
        "mét",
        "tab\tname",
        "null\x00",
    ],
)
def test_create_folder_rejects_bad_names(tmp_path, bad_name):
    library = _make_library(tmp_path)
    with pytest.raises(ArtError) as excinfo:
        library.create_folder(bad_name)
    assert excinfo.value.status == 400


def test_show_command_rejects_traversal_piece(tmp_path):
    library = _make_library(tmp_path)
    with pytest.raises(ArtError) as excinfo:
        library.command("show", "../../etc/passwd")
    assert excinfo.value.status == 400


# --------------------------------------------------------------------------- #
# upload
# --------------------------------------------------------------------------- #


def test_upload_stores_sanitized_name_and_sidecar(tmp_path):
    library = _make_library(tmp_path)
    data = _image_bytes(size=(400, 200))

    new_id = library.upload("My Photo!!.JPG", data, "Collection", "Sunset", "Me")

    assert new_id == "my-photo.jpg"
    assert (library_root(library) / "my-photo.jpg").is_file()
    sidecar = library_root(library) / "my-photo.jpg.txt"
    assert sidecar.read_text(encoding="utf-8").splitlines()[:2] == ["Sunset", "Me"]


def test_upload_into_user_folder(tmp_path):
    library = _make_library(tmp_path)
    (library_root(library) / "Photos").mkdir()

    new_id = library.upload("x.png", _image_bytes(fmt="PNG"), "Photos", None, None)

    assert new_id == "Photos/x.png"
    assert (library_root(library) / "Photos" / "x.png").is_file()
    assert not (library_root(library) / "Photos" / "x.png.txt").exists()


def test_upload_suffixes_name_on_collision(tmp_path):
    library = _make_library(tmp_path)
    library.upload("dup.jpg", _image_bytes(), "Collection", None, None)

    second = library.upload("dup.jpg", _image_bytes(), "Collection", None, None)

    assert second == "dup-1.jpg"
    assert (library_root(library) / "dup-1.jpg").is_file()


def test_upload_strips_exif_and_applies_orientation(tmp_path):
    library = _make_library(tmp_path)
    exif = Image.Exif()
    exif[0x0112] = 6  # orientation: rotate so display swaps width and height
    exif[0x0110] = "Test Camera"  # Model, a metadata string
    exif[0x8825] = {1: "N", 2: (40.0, 30.0, 15.0), 3: "W", 4: (73.0, 58.0, 30.0)}  # GPS
    data = _image_bytes(size=(400, 200), exif=exif.tobytes())
    # Sanity: the uploaded bytes really carry EXIF before stripping.
    assert Image.open(io.BytesIO(data)).getexif()

    new_id = library.upload("geo.jpg", data, "Collection", None, None)

    with Image.open(library_root(library) / new_id) as stored:
        assert stored.size == (200, 400)  # orientation applied to the pixels
        assert len(stored.getexif()) == 0
        assert "exif" not in stored.info


def test_upload_rejects_non_image(tmp_path):
    library = _make_library(tmp_path)
    with pytest.raises(ArtError) as excinfo:
        library.upload("evil.jpg", b"this is not an image", "Collection", None, None)
    assert excinfo.value.status == 400
    assert list(library_root(library).iterdir()) == []


def test_upload_rejects_truncated_image(tmp_path):
    library = _make_library(tmp_path)
    data = _image_bytes(size=(400, 200))[: 40]  # header only, body cut off

    with pytest.raises(ArtError) as excinfo:
        library.upload("trunc.jpg", data, "Collection", None, None)
    assert excinfo.value.status == 400
    assert list(library_root(library).iterdir()) == []


def test_upload_rejects_oversized_bytes(tmp_path):
    library = _make_library(tmp_path)
    data = _image_bytes(size=(400, 200))
    oversized = data + b"\x00" * (artlib.MAX_UPLOAD_BYTES + 1)

    with pytest.raises(ArtError) as excinfo:
        library.upload("big.jpg", oversized, "Collection", None, None)
    assert excinfo.value.status == 413
    assert list(library_root(library).iterdir()) == []


def test_upload_rejects_decompression_bomb(tmp_path, monkeypatch):
    library = _make_library(tmp_path)
    monkeypatch.setattr(artlib, "MAX_IMAGE_PIXELS", 100)
    data = _image_bytes(size=(400, 200))  # 80000 px, far over the lowered cap

    with pytest.raises(ArtError) as excinfo:
        library.upload("bomb.jpg", data, "Collection", None, None)
    assert excinfo.value.status == 413
    assert list(library_root(library).iterdir()) == []


def test_upload_does_not_touch_global_max_image_pixels(tmp_path, monkeypatch):
    # The bomb guard is an explicit size check, not a mutation of Pillow's
    # process-global cap, so it is safe under Task 2's threaded server.
    library = _make_library(tmp_path)
    before = Image.MAX_IMAGE_PIXELS

    library.upload("ok.jpg", _image_bytes(size=(400, 200)), "Collection", None, None)
    assert Image.MAX_IMAGE_PIXELS == before

    monkeypatch.setattr(artlib, "MAX_IMAGE_PIXELS", 100)
    with pytest.raises(ArtError):
        library.upload("bomb.jpg", _image_bytes(size=(400, 200)), "Collection", None, None)
    assert Image.MAX_IMAGE_PIXELS == before


def test_upload_rejects_unknown_folder(tmp_path):
    library = _make_library(tmp_path)
    with pytest.raises(ArtError) as excinfo:
        library.upload("x.jpg", _image_bytes(), "Nope", None, None)
    assert excinfo.value.status == 404


# --------------------------------------------------------------------------- #
# delete and restore
# --------------------------------------------------------------------------- #


def test_delete_moves_image_and_sidecar_to_trash(tmp_path):
    clock = FakeClock()
    library = _make_library(tmp_path, clock=clock)
    image = library_root(library) / "Photos" / "beach.jpg"
    _write_image(image)
    _write_sidecar(image, title="Beach", artist="Me")

    trash_id = library.delete("Photos/beach.jpg")

    assert not image.exists()
    assert not image.with_name("beach.jpg.txt").exists()
    assert trash_id.startswith(".trash/")
    trash_image = library_root(library) / trash_id
    assert trash_image.is_file()
    assert trash_image.with_name("beach.jpg.txt").is_file()
    trash_entry = _trash_entry(library.scan(), trash_id)
    assert trash_entry["original"] == "Photos/beach.jpg"


def test_restore_round_trips_including_sidecar(tmp_path):
    clock = FakeClock()
    library = _make_library(tmp_path, clock=clock)
    image = library_root(library) / "Photos" / "beach.jpg"
    _write_image(image)
    _write_sidecar(image, title="Beach", artist="Me")

    trash_id = library.delete("Photos/beach.jpg")
    restored_id = library.restore(trash_id)

    assert restored_id == "Photos/beach.jpg"
    assert (library_root(library) / "Photos" / "beach.jpg").is_file()
    assert (library_root(library) / "Photos" / "beach.jpg.txt").is_file()
    assert _trash_entry(library.scan(), trash_id) is None


def test_restore_suffixes_name_when_original_taken(tmp_path):
    clock = FakeClock()
    library = _make_library(tmp_path, clock=clock)
    image = library_root(library) / "met.jpg"
    _write_image(image)

    trash_id = library.delete("met.jpg")
    _write_image(library_root(library) / "met.jpg")  # a new file takes the name
    restored_id = library.restore(trash_id)

    assert restored_id == "met-1.jpg"
    assert (library_root(library) / "met-1.jpg").is_file()
    assert (library_root(library) / "met.jpg").is_file()


def test_restore_rejects_bad_trash_id(tmp_path):
    library = _make_library(tmp_path)
    with pytest.raises(ArtError) as excinfo:
        library.restore(".trash/../../etc/passwd")
    assert excinfo.value.status == 400


def test_delete_missing_piece_is_404(tmp_path):
    library = _make_library(tmp_path)
    with pytest.raises(ArtError) as excinfo:
        library.delete("ghost.jpg")
    assert excinfo.value.status == 404


# --------------------------------------------------------------------------- #
# move
# --------------------------------------------------------------------------- #


def test_move_carries_sidecar(tmp_path):
    library = _make_library(tmp_path)
    image = library_root(library) / "met.jpg"
    _write_image(image)
    _write_sidecar(image, title="Art", artist="Painter")
    (library_root(library) / "Photos").mkdir()

    new_id = library.move("met.jpg", "Photos")

    assert new_id == "Photos/met.jpg"
    assert (library_root(library) / "Photos" / "met.jpg").is_file()
    assert (library_root(library) / "Photos" / "met.jpg.txt").is_file()
    assert not (library_root(library) / "met.jpg").exists()


def test_move_to_collection(tmp_path):
    library = _make_library(tmp_path)
    _write_image(library_root(library) / "Photos" / "beach.jpg")

    new_id = library.move("Photos/beach.jpg", "Collection")

    assert new_id == "beach.jpg"
    assert (library_root(library) / "beach.jpg").is_file()


def test_move_to_unknown_folder_is_404(tmp_path):
    library = _make_library(tmp_path)
    _write_image(library_root(library) / "met.jpg")
    with pytest.raises(ArtError) as excinfo:
        library.move("met.jpg", "Nope")
    assert excinfo.value.status == 404


# --------------------------------------------------------------------------- #
# folders
# --------------------------------------------------------------------------- #


def test_create_folder(tmp_path):
    library = _make_library(tmp_path)

    library.create_folder("Trips")

    assert (library_root(library) / "Trips").is_dir()


def test_create_existing_folder_is_409(tmp_path):
    library = _make_library(tmp_path)
    library.create_folder("Trips")
    with pytest.raises(ArtError) as excinfo:
        library.create_folder("Trips")
    assert excinfo.value.status == 409


def test_create_collection_is_409(tmp_path):
    library = _make_library(tmp_path)
    with pytest.raises(ArtError) as excinfo:
        library.create_folder("Collection")
    assert excinfo.value.status == 409


def test_rename_folder(tmp_path):
    library = _make_library(tmp_path)
    _write_image(library_root(library) / "Trips" / "x.jpg")

    library.rename_folder("Trips", "Voyages")

    assert (library_root(library) / "Voyages" / "x.jpg").is_file()
    assert not (library_root(library) / "Trips").exists()


def test_rename_carries_enabled_state(tmp_path):
    library = _make_library(tmp_path)
    library.create_folder("Trips")
    library.set_folder_enabled("Trips", True)

    library.rename_folder("Trips", "Voyages")

    assert "Voyages" in library.control()["settings"]["folders_enabled"]
    assert "Trips" not in library.control()["settings"]["folders_enabled"]


def test_rename_collection_rejected(tmp_path):
    library = _make_library(tmp_path)
    with pytest.raises(ArtError):
        library.rename_folder("Collection", "Other")


def test_rename_onto_existing_is_409(tmp_path):
    library = _make_library(tmp_path)
    library.create_folder("A")
    library.create_folder("B")
    with pytest.raises(ArtError) as excinfo:
        library.rename_folder("A", "B")
    assert excinfo.value.status == 409


def test_delete_nonempty_folder_is_409(tmp_path):
    library = _make_library(tmp_path)
    _write_image(library_root(library) / "Trips" / "x.jpg")
    with pytest.raises(ArtError) as excinfo:
        library.delete_folder("Trips")
    assert excinfo.value.status == 409
    assert (library_root(library) / "Trips").is_dir()


def test_delete_empty_folder(tmp_path):
    library = _make_library(tmp_path)
    library.create_folder("Trips")

    library.delete_folder("Trips")

    assert not (library_root(library) / "Trips").exists()


def test_delete_folder_removes_enabled_state(tmp_path):
    library = _make_library(tmp_path)
    library.create_folder("Trips")
    library.set_folder_enabled("Trips", True)

    library.delete_folder("Trips")

    assert "Trips" not in library.control()["settings"]["folders_enabled"]


# --------------------------------------------------------------------------- #
# settings, enablement, and the folder index flag
# --------------------------------------------------------------------------- #


def test_set_folder_enabled_reflected_in_index(tmp_path):
    library = _make_library(tmp_path)
    _write_image(library_root(library) / "Photos" / "x.jpg")

    assert _folder(library.scan(), "Photos")["enabled"] is False
    library.set_folder_enabled("Photos", True)
    assert _folder(library.scan(), "Photos")["enabled"] is True


def test_set_folder_enabled_unknown_is_404(tmp_path):
    library = _make_library(tmp_path)
    with pytest.raises(ArtError) as excinfo:
        library.set_folder_enabled("Nope", True)
    assert excinfo.value.status == 404


def test_set_interval_accepts_allowed(tmp_path):
    library = _make_library(tmp_path)
    for minutes in (15, 30, 60, 120, 180, 360, 720, 1440):
        library.set_interval(minutes)
        assert library.control()["settings"]["rotate_minutes"] == minutes


@pytest.mark.parametrize("bad", [0, 10, 45, 90, 1441, -60])
def test_set_interval_rejects_others(tmp_path, bad):
    library = _make_library(tmp_path)
    with pytest.raises(ArtError) as excinfo:
        library.set_interval(bad)
    assert excinfo.value.status == 400


def test_control_defaults(tmp_path):
    library = _make_library(tmp_path)

    control = library.control()

    assert control["settings"]["rotate_minutes"] == 60
    assert control["settings"]["folders_enabled"] == ["Collection"]
    assert control["settings"]["min_aspect"] == 1.1
    assert control["command"] is None


def test_control_carries_a_stored_min_aspect(tmp_path):
    library = _make_library(tmp_path)
    library.state.parent.mkdir(parents=True, exist_ok=True)
    library.state.write_text(
        json.dumps({"settings": {"min_aspect": 1.3}, "command": None}), encoding="utf-8"
    )

    assert library.control()["settings"]["min_aspect"] == 1.3


@pytest.mark.parametrize("bad", [0, -1, "1.2", True, None, float("inf"), float("nan")])
def test_control_rejects_a_bad_stored_min_aspect(tmp_path, bad):
    library = _make_library(tmp_path)
    library.state.parent.mkdir(parents=True, exist_ok=True)
    library.state.write_text(
        json.dumps({"settings": {"min_aspect": bad}, "command": None}), encoding="utf-8"
    )

    assert library.control()["settings"]["min_aspect"] == 1.1


def test_scan_landscape_uses_the_stored_min_aspect(tmp_path):
    # The stored cutoff is the single source of truth: raised to 1.3, a 1.2 piece
    # no longer counts as landscape.
    library = _make_library(tmp_path)
    library.state.parent.mkdir(parents=True, exist_ok=True)
    library.state.write_text(
        json.dumps({"settings": {"min_aspect": 1.3}, "command": None}), encoding="utf-8"
    )
    _write_image(library_root(library) / "wide.jpg", size=(120, 100))

    index = library.scan()

    assert _piece(index, "wide.jpg")["landscape"] is False


# --------------------------------------------------------------------------- #
# commands
# --------------------------------------------------------------------------- #


def test_command_seq_increments_by_one(tmp_path):
    clock = FakeClock()
    library = _make_library(tmp_path, clock=clock)

    first = library.command("next")
    second = library.command("previous")

    assert first["seq"] == 1
    assert second["seq"] == 2
    assert library.control()["command"]["seq"] == 2
    assert library.control()["command"]["action"] == "previous"


def test_command_show_landscape_sets_piece(tmp_path):
    library = _make_library(tmp_path)
    _write_image(library_root(library) / "wide.jpg", size=(400, 200))

    command = library.command("show", "wide.jpg")

    assert command["action"] == "show"
    assert command["piece"] == "wide.jpg"


def test_command_show_portrait_is_409(tmp_path):
    library = _make_library(tmp_path)
    _write_image(library_root(library) / "tall.jpg", size=(200, 400))

    with pytest.raises(ArtError) as excinfo:
        library.command("show", "tall.jpg")
    assert excinfo.value.status == 409
    # Nothing recorded: the rejected command does not advance the sequence.
    assert library.control()["command"] is None


def test_command_show_missing_piece_is_404(tmp_path):
    library = _make_library(tmp_path)
    with pytest.raises(ArtError) as excinfo:
        library.command("show", "ghost.jpg")
    assert excinfo.value.status == 404


def test_command_rejects_unknown_action(tmp_path):
    library = _make_library(tmp_path)
    with pytest.raises(ArtError) as excinfo:
        library.command("explode")
    assert excinfo.value.status == 400


def test_command_without_frame_is_null_scoped(tmp_path):
    # A command with no frame carries frame=null (every frame), the compatible
    # default; the published document always carries the key.
    library = _make_library(tmp_path)
    command = library.command("next")
    assert command["frame"] is None
    assert library.control()["command"]["frame"] is None


def test_command_records_the_frame_scope(tmp_path):
    library = _make_library(tmp_path)
    command = library.command("next", frame="office")
    assert command["frame"] == "office"
    assert library.control()["command"]["frame"] == "office"


def test_a_command_stored_without_a_frame_key_reads_as_null(tmp_path):
    # An older command document (written before per-frame commands) has no frame
    # key; it reads back as null rather than missing, so a consumer reads it
    # uniformly.
    library = _make_library(tmp_path)
    state = library.state
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text(
        json.dumps(
            {
                "settings": {"rotate_minutes": 60, "folders_enabled": ["Collection"], "min_aspect": 1.1},
                "command": {"seq": 3, "action": "next", "piece": None,
                            "issued_at": "2026-11-09T07:00:00-08:00"},
            }
        ),
        encoding="utf-8",
    )
    assert library.control()["command"]["frame"] is None


# --------------------------------------------------------------------------- #
# trash purge
# --------------------------------------------------------------------------- #


def test_trash_purged_after_30_days(tmp_path):
    clock = FakeClock(now=1_700_000_000.0)
    library = _make_library(tmp_path, clock=clock)
    _write_image(library_root(library) / "old.jpg")
    trash_id = library.delete("old.jpg")
    assert (library_root(library) / trash_id).is_file()

    clock.now += 31 * 86400
    library.scan()

    assert not (library_root(library) / trash_id).exists()
    assert library.scan()["trash"] == []


def test_trash_kept_before_30_days(tmp_path):
    clock = FakeClock(now=1_700_000_000.0)
    library = _make_library(tmp_path, clock=clock)
    _write_image(library_root(library) / "recent.jpg")
    trash_id = library.delete("recent.jpg")

    clock.now += 29 * 86400
    index = library.scan()

    assert (library_root(library) / trash_id).is_file()
    assert _trash_entry(index, trash_id) is not None


# --------------------------------------------------------------------------- #
# small accessors so the tests read against the public shape only
# --------------------------------------------------------------------------- #


def library_root(library: Library) -> Path:
    return library.root


def library_thumbs(library: Library) -> Path:
    return library.thumbs


def _piece(index: dict, piece_id: str) -> dict:
    for piece in index["pieces"]:
        if piece["id"] == piece_id:
            return piece
    raise AssertionError(f"piece {piece_id!r} not in index")


def _folder(index: dict, name: str) -> dict:
    for folder in index["folders"]:
        if folder["name"] == name:
            return folder
    raise AssertionError(f"folder {name!r} not in index")


def _trash_entry(index: dict, trash_id: str):
    for entry in index["trash"]:
        if entry["id"] == trash_id:
            return entry
    return None


# --------------------------------------------------------------------------- #
# metadata-only scan and background thumbnailing (hotfix: publish before thumbs)
# --------------------------------------------------------------------------- #


def test_scan_without_thumbnails_lists_pieces_with_null_thumb(tmp_path):
    # A metadata-only pass lists every piece immediately with a null thumb and
    # generates nothing, so the first index is published without the slow decode.
    library = _make_library(tmp_path)
    for i in range(5):
        _write_image(library_root(library) / f"met-{i}.jpg")

    index = library.scan(ensure_thumbnails=False)

    assert len(index["pieces"]) == 5
    assert all(piece["thumb"] is None for piece in index["pieces"])
    # Nothing was written to the thumbnail directory.
    assert not library_thumbs(library).exists() or not any(library_thumbs(library).iterdir())


def test_scan_without_thumbnails_reuses_an_existing_thumbnail(tmp_path):
    # Once a thumbnail exists (a full scan, or a restart after the background
    # pass), the metadata-only scan reports its URL instead of null.
    library = _make_library(tmp_path)
    _write_image(library_root(library) / "met-1.jpg")
    library.scan()  # full pass generates the thumbnail

    piece = _piece(library.scan(ensure_thumbnails=False), "met-1.jpg")

    assert piece["thumb"] is not None
    assert piece["thumb"].startswith("/art-thumbs/")


def test_scan_without_thumbnails_keeps_existing_thumbnail_unpruned(tmp_path):
    # The metadata-only scan must not prune a thumbnail that belongs to a current
    # piece just because it did not regenerate it.
    library = _make_library(tmp_path)
    _write_image(library_root(library) / "met-1.jpg")
    library.scan()
    thumb_files = set(p.name for p in library_thumbs(library).iterdir())

    library.scan(ensure_thumbnails=False)

    assert set(p.name for p in library_thumbs(library).iterdir()) == thumb_files


def test_ensure_thumbnails_generates_missing_in_batches(tmp_path):
    library = _make_library(tmp_path)
    for i in range(20):
        _write_image(library_root(library) / f"met-{i:02d}.jpg")
    library.scan(ensure_thumbnails=False)  # index exists, no thumbs yet

    batches = []
    generated = library.ensure_thumbnails(batch_size=5, on_batch=lambda: batches.append(1))

    assert generated == 20
    # Batched republish: a callback after each group of five, not one per thumb.
    assert len(batches) == 4
    # Every piece now has a thumbnail, visible to a later metadata scan.
    index = library.scan(ensure_thumbnails=False)
    assert all(piece["thumb"] is not None for piece in index["pieces"])


def test_ensure_thumbnails_is_a_noop_when_all_present(tmp_path):
    library = _make_library(tmp_path)
    _write_image(library_root(library) / "met-1.jpg")
    library.scan()  # generates the thumbnail

    calls = []
    generated = library.ensure_thumbnails(batch_size=1, on_batch=lambda: calls.append(1))

    assert generated == 0
    assert calls == []


def test_ensure_thumbnails_stops_when_asked(tmp_path):
    library = _make_library(tmp_path)
    for i in range(10):
        _write_image(library_root(library) / f"met-{i}.jpg")
    library.scan(ensure_thumbnails=False)

    # Stop after the first thumbnail: should_stop is checked before each one.
    seen = {"n": 0}

    def should_stop():
        seen["n"] += 1
        return seen["n"] > 1

    generated = library.ensure_thumbnails(batch_size=100, should_stop=should_stop)

    assert generated == 1


def test_ensure_thumbnails_covers_trash(tmp_path):
    library = _make_library(tmp_path)
    _write_image(library_root(library) / "met-1.jpg")
    library.scan(ensure_thumbnails=False)
    trash_id = library.delete("met-1.jpg")
    library.scan(ensure_thumbnails=False)

    library.ensure_thumbnails(batch_size=100)

    entry = _trash_entry(library.scan(ensure_thumbnails=False), trash_id)
    assert entry is not None
    assert entry["thumb"] is not None


# --------------------------------------------------------------------------- #
# Ente albums as read-only folders
# --------------------------------------------------------------------------- #


def _ente_album(mirror: Path, album_id: str, title: str, files: dict) -> None:
    """Write a mirror album: ``files`` maps file id to (width, height)."""
    album_dir = mirror / album_id
    album_dir.mkdir(parents=True, exist_ok=True)
    record = {"title": title, "updated_at": 1, "files": {}}
    for file_id, dims in files.items():
        _write_image(album_dir / f"{file_id}.jpg", size=dims)
        record["files"][file_id] = {"updated_at": 1, "hash": "h", "ext": "jpg"}
    (album_dir / "album.json").write_text(json.dumps(record), encoding="utf-8")


def _ente_library(tmp_path: Path, *, with_status: bool = False) -> Library:
    root = tmp_path / "art"
    root.mkdir(parents=True, exist_ok=True)
    return Library(
        root,
        tmp_path / "public" / "art-thumbs",
        tmp_path / "state" / "art-control.json",
        ente_root=tmp_path / "mirror",
        ente_cache=tmp_path / "public" / "art-ente",
        ente_status=(tmp_path / "ente-status.json") if with_status else None,
    )


def test_scan_without_ente_root_is_unchanged(tmp_path):
    library = _make_library(tmp_path)
    _write_image(library.root / "a.jpg")
    index = library.scan()
    assert "ente_sync" not in index
    assert all("source" not in f for f in index["folders"])
    assert all("source" not in p for p in index["pieces"])
    assert library.control()["settings"]["ente_seen"] == []


def test_ente_sync_unknown_when_status_given_but_missing(tmp_path):
    library = _ente_library(tmp_path, with_status=True)
    index = library.scan()
    assert index["ente_sync"] == {"state": "unknown", "last_success": None, "error": None}


def test_ente_album_listed_as_folder_with_source_and_title(tmp_path):
    library = _ente_library(tmp_path)
    _write_image(library.root / "a.jpg")
    _ente_album(tmp_path / "mirror", "alb1", "Album One", {"f1": (400, 200)})
    index = library.scan()
    names = [f["name"] for f in index["folders"]]
    assert names == ["Collection", "ente-alb1"]
    folder = _folder(index, "ente-alb1")
    assert folder["source"] == "ente"
    assert folder["title"] == "Album One"
    assert folder["count"] == 1
    assert folder["builtin"] is False
    piece = next(p for p in index["pieces"] if p["folder"] == "ente-alb1")
    assert piece["source"] == "ente"
    assert piece["title"] == "Album One"
    assert piece["artist"] is None and piece["source_url"] is None
    assert piece["file"] == "f1.jpg"
    assert (piece["width"], piece["height"]) == (400, 200)
    assert piece["landscape"] is True
    assert piece["thumb"].startswith("/art-thumbs/")
    rendition = tmp_path / "public" / "art-ente" / "ente-alb1" / "f1.jpg"
    assert piece["size"] == rendition.stat().st_size
    assert piece["mtime"] == int(rendition.stat().st_mtime)


def test_ente_piece_url_is_under_art_and_id_matches_validators(tmp_path):
    library = _ente_library(tmp_path)
    _ente_album(tmp_path / "mirror", "alb1", "Album One", {"f1": (400, 200)})
    piece = library.scan()["pieces"][0]
    assert piece["id"] == "ente-alb1/f1.jpg"
    assert piece["url"] == "/art/ente-alb1/f1.jpg"
    assert artlib._FOLDER_NAME_RE.match(piece["folder"])
    assert artlib._FILE_NAME_RE.match(piece["file"])


def test_ente_pieces_counted_and_tall_piece_still_listed(tmp_path):
    library = _ente_library(tmp_path)
    _ente_album(tmp_path / "mirror", "alb1", "Album One", {"f1": (400, 200), "f2": (200, 400)})
    index = library.scan()
    assert _folder(index, "ente-alb1")["count"] == 2
    tall = next(p for p in index["pieces"] if p["file"] == "f2.jpg")
    assert tall["landscape"] is False


def test_new_album_appended_to_enabled_and_seen(tmp_path):
    library = _ente_library(tmp_path)
    _ente_album(tmp_path / "mirror", "alb1", "Album One", {"f1": (400, 200)})
    index = library.scan()
    assert _folder(index, "ente-alb1")["enabled"] is True
    settings = library.control()["settings"]
    assert "ente-alb1" in settings["folders_enabled"]
    assert settings["ente_seen"] == ["ente-alb1"]


def test_album_switched_off_stays_off_after_rescan(tmp_path):
    library = _ente_library(tmp_path)
    _ente_album(tmp_path / "mirror", "alb1", "Album One", {"f1": (400, 200)})
    library.scan()
    library.set_folder_enabled("ente-alb1", False)
    index = library.scan()
    assert _folder(index, "ente-alb1")["enabled"] is False
    assert "ente-alb1" not in library.control()["settings"]["folders_enabled"]


def test_renamed_album_keeps_switch_state(tmp_path):
    library = _ente_library(tmp_path)
    mirror = tmp_path / "mirror"
    _ente_album(mirror, "alb1", "Album One", {"f1": (400, 200)})
    library.scan()
    library.set_folder_enabled("ente-alb1", False)
    _ente_album(mirror, "alb1", "Album Uno", {"f1": (400, 200)})
    folder = _folder(library.scan(), "ente-alb1")
    assert folder["title"] == "Album Uno"
    assert folder["enabled"] is False


def test_removed_picture_leaves_index_and_rendition_pruned(tmp_path):
    library = _ente_library(tmp_path)
    mirror = tmp_path / "mirror"
    _ente_album(mirror, "alb1", "Album One", {"f1": (400, 200), "f2": (400, 200)})
    library.scan()
    cache = tmp_path / "public" / "art-ente" / "ente-alb1"
    assert (cache / "f2.jpg").is_file()
    _ente_album(mirror, "alb1", "Album One", {"f1": (400, 200)})
    (mirror / "alb1" / "f2.jpg").unlink()
    index = library.scan()
    assert [p["file"] for p in index["pieces"]] == ["f1.jpg"]
    assert not (cache / "f2.jpg").exists()
    assert (cache / "f1.jpg").is_file()


def test_metadata_only_scan_omits_pieces_without_rendition(tmp_path):
    library = _ente_library(tmp_path)
    _ente_album(tmp_path / "mirror", "alb1", "Album One", {"f1": (400, 200)})
    index = library.scan(ensure_thumbnails=False)
    assert index["pieces"] == []
    assert not list((tmp_path / "public").rglob("f1.jpg"))
    generated = library.ensure_thumbnails()
    assert generated == 2  # one rendition plus one thumbnail
    index = library.scan(ensure_thumbnails=False)
    assert [p["id"] for p in index["pieces"]] == ["ente-alb1/f1.jpg"]
    assert index["pieces"][0]["thumb"].startswith("/art-thumbs/")


def test_art_folder_named_ente_prefix_is_ignored_on_scan(tmp_path):
    library = _ente_library(tmp_path)
    _write_image(library.root / "ente-0123456789ab" / "x.jpg")
    _write_image(library.root / "ENTE-Other" / "y.jpg")
    _write_image(library.root / "Fine" / "z.jpg")
    index = library.scan(ensure_thumbnails=False)
    assert [f["name"] for f in index["folders"]] == ["Collection", "Fine"]
    assert [p["id"] for p in index["pieces"]] == ["Fine/z.jpg"]
    assert library.ensure_thumbnails() == 1  # only Fine/z.jpg


def test_corrupt_ente_file_skipped_others_listed(tmp_path):
    library = _ente_library(tmp_path)
    mirror = tmp_path / "mirror"
    _ente_album(mirror, "alb1", "Album One", {"f1": (400, 200), "f2": (400, 200)})
    (mirror / "alb1" / "f2.jpg").write_bytes(b"not an image")
    index = library.scan()
    assert [p["file"] for p in index["pieces"]] == ["f1.jpg"]
    assert _folder(index, "ente-alb1")["count"] == 1


def test_missing_export_root_gives_no_ente_folders_and_keeps_art(tmp_path):
    library = _ente_library(tmp_path)
    _write_image(library.root / "a.jpg")
    index = library.scan()
    assert [f["name"] for f in index["folders"]] == ["Collection"]
    assert [p["id"] for p in index["pieces"]] == ["a.jpg"]


def test_command_show_accepts_tall_ente_piece_but_rejects_tall_art_piece(tmp_path):
    library = _ente_library(tmp_path)
    _write_image(library.root / "Tall" / "t.jpg", size=(200, 400))
    _ente_album(tmp_path / "mirror", "alb1", "Album One", {"f1": (200, 400)})
    library.scan()
    assert library.command("show", "ente-alb1/f1.jpg")["piece"] == "ente-alb1/f1.jpg"
    with pytest.raises(ArtError) as excinfo:
        library.command("show", "Tall/t.jpg")
    assert excinfo.value.status == 409


def test_ente_seen_survives_state_normalisation(tmp_path):
    library = _ente_library(tmp_path)
    library.state.parent.mkdir(parents=True, exist_ok=True)
    library.state.write_text(
        json.dumps({"settings": {"ente_seen": ["ente-a", 5, None, "ente-b"]}}),
        encoding="utf-8",
    )
    assert library.control()["settings"]["ente_seen"] == ["ente-a", "ente-b"]
    library.state.write_text(json.dumps({"settings": {"ente_seen": "bad"}}), encoding="utf-8")
    assert library.control()["settings"]["ente_seen"] == []


def test_set_folder_enabled_switches_ente_folder_off_and_on(tmp_path):
    library = _ente_library(tmp_path)
    _ente_album(tmp_path / "mirror", "alb1", "Album One", {"f1": (400, 200)})
    library.scan()
    assert library.set_folder_enabled("ente-alb1", False) is False
    assert _folder(library.scan(), "ente-alb1")["enabled"] is False
    assert library.set_folder_enabled("ente-alb1", True) is True
    assert _folder(library.scan(), "ente-alb1")["enabled"] is True


def test_set_folder_enabled_ente_name_without_mirror_dir_is_404(tmp_path):
    library = _ente_library(tmp_path)
    (tmp_path / "mirror").mkdir()
    with pytest.raises(ArtError) as excinfo:
        library.set_folder_enabled("ente-nope", False)
    assert excinfo.value.status == 404


def test_set_folder_enabled_ente_name_without_ente_root_is_404(tmp_path):
    library = _make_library(tmp_path)
    (library.root / "ente-alb1").mkdir()  # a hand-made art dir is not an Ente album
    with pytest.raises(ArtError) as excinfo:
        library.set_folder_enabled("ente-alb1", False)
    assert excinfo.value.status == 404


# --------------------------------------------------------------------------- #
# Ente albums are read-only
# --------------------------------------------------------------------------- #

READONLY = "This picture comes from Ente; change it in Ente."
RESERVED = "That folder name is reserved."


def _ente_library_with_album(tmp_path: Path) -> Library:
    library = _ente_library(tmp_path)
    _ente_album(tmp_path / "mirror", "alb1", "Album One", {"f1": (400, 200)})
    library.scan()
    return library


def _mirror_snapshot(tmp_path: Path) -> list[str]:
    return sorted(str(p.relative_to(tmp_path / "mirror")) for p in (tmp_path / "mirror").rglob("*"))


def test_delete_ente_piece_refused_409_with_message(tmp_path):
    library = _ente_library_with_album(tmp_path)
    before = _mirror_snapshot(tmp_path)
    with pytest.raises(ArtError) as excinfo:
        library.delete("ente-alb1/f1.jpg")
    assert excinfo.value.status == 409
    assert excinfo.value.message == READONLY
    assert artlib.ENTE_READONLY_MESSAGE == READONLY
    assert _mirror_snapshot(tmp_path) == before


def test_restore_move_upload_into_ente_folder_refused(tmp_path):
    library = _ente_library_with_album(tmp_path)
    _write_image(library.root / "met-1.jpg")
    # A trash entry whose recorded original place is an Ente folder.
    stamp_dir = library.root / ".trash" / "2026-01-01T00-00-00"
    stamp_dir.mkdir(parents=True)
    _write_image(stamp_dir / "f9.jpg")
    (stamp_dir / "meta.json").write_text(
        json.dumps({"original": "ente-alb1/f9.jpg", "deleted_ts": 1}), encoding="utf-8"
    )
    with pytest.raises(ArtError) as restore_error:
        library.restore(".trash/2026-01-01T00-00-00/f9.jpg")
    with pytest.raises(ArtError) as move_error:
        library.move("met-1.jpg", "ente-alb1")
    with pytest.raises(ArtError) as upload_error:
        library.upload("new.jpg", _image_bytes(), "ente-alb1", None, None)
    for error in (restore_error, move_error, upload_error):
        assert error.value.status == 409
        assert error.value.message == READONLY
    assert (stamp_dir / "f9.jpg").is_file()
    assert not (tmp_path / "mirror" / "alb1" / "new.jpg").exists()
    assert not (library.root / "ente-alb1").exists()


def test_move_ente_piece_out_refused(tmp_path):
    library = _ente_library_with_album(tmp_path)
    before = _mirror_snapshot(tmp_path)
    with pytest.raises(ArtError) as excinfo:
        library.move("ente-alb1/f1.jpg", "Collection")
    assert excinfo.value.status == 409
    assert excinfo.value.message == READONLY
    assert _mirror_snapshot(tmp_path) == before


def test_rename_and_delete_ente_folder_refused(tmp_path):
    library = _ente_library_with_album(tmp_path)
    library.create_folder("Photos")
    with pytest.raises(ArtError) as rename_error:
        library.rename_folder("ente-alb1", "Elsewhere")
    with pytest.raises(ArtError) as delete_error:
        library.delete_folder("ente-alb1")
    for error in (rename_error, delete_error):
        assert error.value.status == 409
        assert error.value.message == READONLY
    assert (tmp_path / "mirror" / "alb1").is_dir()


@pytest.mark.parametrize("name", ["ente-new", "Ente-new", "ENTE-NEW", "eNtE-x"])
def test_create_and_rename_to_ente_prefix_refused_any_case(tmp_path, name):
    library = _ente_library_with_album(tmp_path)
    library.create_folder("Photos")
    with pytest.raises(ArtError) as create_error:
        library.create_folder(name)
    with pytest.raises(ArtError) as rename_error:
        library.rename_folder("Photos", name)
    for error in (create_error, rename_error):
        assert error.value.status == 409
        assert error.value.message == RESERVED
    assert not (library.root / name).exists()
    assert (library.root / "Photos").is_dir()


def test_enable_disable_ente_folder_works_and_persists(tmp_path):
    library = _ente_library_with_album(tmp_path)
    assert library.set_folder_enabled("ente-alb1", False) is False
    reloaded = Library(
        library.root, library.thumbs, library.state,
        ente_root=library.ente_root, ente_cache=library.ente_cache,
    )
    assert "ente-alb1" not in reloaded.control()["settings"]["folders_enabled"]
    assert _folder(reloaded.scan(), "ente-alb1")["enabled"] is False
    assert reloaded.set_folder_enabled("ente-alb1", True) is True
    assert "ente-alb1" in reloaded.control()["settings"]["folders_enabled"]


def test_collection_piece_named_like_ente_prefix_is_not_refused(tmp_path):
    library = _ente_library_with_album(tmp_path)
    _write_image(library.root / "Ente-sunset.jpg")
    library.create_folder("Photos")
    new_id = library.move("Ente-sunset.jpg", "Photos")
    assert new_id == "Photos/Ente-sunset.jpg"
    assert library.delete(new_id).startswith(".trash/")


# --------------------------------------------------------------------------- #
# Frame crop: storage, moves, trash, Ente, the index fields
# --------------------------------------------------------------------------- #

CROP_5_3 = {"x": 0.0, "y": 0.0, "w": 0.5, "h": 0.5}  # exact 5:3 on a 5:3 picture


def _crop_for(width, height, x=0.0, y=0.0, w=0.5):
    """A valid 5:3 box (exact aspect) for a picture of width x height."""
    return {"x": x, "y": y, "w": w, "h": w * width / height * 0.6}


def _piece(index, piece_id):
    return next(p for p in index["pieces"] if p["id"] == piece_id)


def _crops_file(library):
    return library.state.parent / "crops.json"


def test_set_crop_is_stored_in_crops_json_beside_the_state_file(tmp_path):
    library = _make_library(tmp_path)
    _write_image(library_root(library) / "a.jpg", size=(2000, 1200))
    stored = library.set_crop("a.jpg", CROP_5_3)
    assert stored == CROP_5_3
    saved = json.loads(_crops_file(library).read_text(encoding="utf-8"))
    assert saved["crops"]["a.jpg"] == CROP_5_3


def test_index_carries_crop_and_auto_crop(tmp_path):
    library = _make_library(tmp_path)
    _write_image(library_root(library) / "a.jpg", size=(2000, 1200))
    _write_image(library_root(library) / "b.jpg", size=(2000, 1200))
    library.set_crop("a.jpg", CROP_5_3)
    index = library.scan()
    assert _piece(index, "a.jpg")["crop"] == CROP_5_3
    assert _piece(index, "b.jpg")["crop"] is None
    for piece_id in ("a.jpg", "b.jpg"):
        auto = _piece(index, piece_id)["auto_crop"]
        assert auto == {"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0}


def test_metadata_only_scan_has_null_auto_crop_until_the_pass_computes_it(tmp_path):
    library = _make_library(tmp_path)
    _write_image(library_root(library) / "a.jpg", size=(2000, 1000))
    first = library.scan(ensure_thumbnails=False)
    assert _piece(first, "a.jpg")["auto_crop"] is None
    library.ensure_thumbnails()
    again = library.scan(ensure_thumbnails=False)
    assert _piece(again, "a.jpg")["auto_crop"] is not None


def test_thumbnail_pass_adds_auto_crop_for_a_thumbnail_that_already_exists(tmp_path):
    library = _make_library(tmp_path)
    _write_image(library_root(library) / "a.jpg", size=(2000, 1000))
    library.scan()
    for cache in library.thumbs.glob("*.crop.json"):
        cache.unlink()  # a thumbnail from before this feature
    assert _piece(library.scan(ensure_thumbnails=False), "a.jpg")["auto_crop"] is None
    assert library.ensure_thumbnails() == 1
    assert _piece(library.scan(ensure_thumbnails=False), "a.jpg")["auto_crop"] is not None


def test_auto_crop_is_cached_with_the_thumbnail_not_recomputed(tmp_path, monkeypatch):
    from snaphouse import framecrop

    library = _make_library(tmp_path)
    _write_image(library_root(library) / "a.jpg", size=(2000, 1000))
    calls = []
    real = framecrop.auto_crop
    monkeypatch.setattr(framecrop, "auto_crop", lambda image: calls.append(1) or real(image))
    library.scan()
    library.scan()
    library.ensure_thumbnails()
    assert len(calls) == 1


def test_a_cached_auto_crop_is_pruned_with_its_thumbnail(tmp_path):
    library = _make_library(tmp_path)
    _write_image(library_root(library) / "a.jpg", size=(2000, 1000))
    library.scan()
    assert list(library.thumbs.glob("*.crop.json"))
    (library_root(library) / "a.jpg").unlink()
    library.scan()
    assert not list(library.thumbs.iterdir())


def test_a_piece_that_cannot_be_measured_gets_null_auto_crop(tmp_path, monkeypatch):
    from snaphouse import framecrop

    library = _make_library(tmp_path)
    _write_image(library_root(library) / "a.jpg", size=(2000, 1000))
    monkeypatch.setattr(framecrop, "auto_crop", lambda image: None)
    assert _piece(library.scan(), "a.jpg")["auto_crop"] is None


def test_landscape_is_true_for_a_piece_with_a_crop(tmp_path):
    library = _make_library(tmp_path)
    _write_image(library_root(library) / "tall.jpg", size=(1000, 2000))
    assert _piece(library.scan(), "tall.jpg")["landscape"] is False
    library.set_crop("tall.jpg", _crop_for(1000, 2000, w=0.8))
    assert _piece(library.scan(), "tall.jpg")["landscape"] is True
    library.set_crop("tall.jpg", None)
    assert _piece(library.scan(), "tall.jpg")["landscape"] is False


def test_remove_crop_with_none(tmp_path):
    library = _make_library(tmp_path)
    _write_image(library_root(library) / "a.jpg", size=(2000, 1200))
    library.set_crop("a.jpg", CROP_5_3)
    assert library.set_crop("a.jpg", None) is None
    assert _piece(library.scan(), "a.jpg")["crop"] is None
    assert json.loads(_crops_file(library).read_text(encoding="utf-8"))["crops"] == {}


def test_removing_a_crop_that_was_never_set_is_fine(tmp_path):
    library = _make_library(tmp_path)
    _write_image(library_root(library) / "a.jpg", size=(2000, 1200))
    assert library.set_crop("a.jpg", None) is None


def test_set_crop_unknown_piece_is_404(tmp_path):
    library = _make_library(tmp_path)
    with pytest.raises(ArtError) as caught:
        library.set_crop("nope.jpg", CROP_5_3)
    assert caught.value.status == 404


def test_set_crop_bad_value_is_400_and_stores_nothing(tmp_path):
    library = _make_library(tmp_path)
    _write_image(library_root(library) / "a.jpg", size=(2000, 1200))
    with pytest.raises(ArtError) as caught:
        library.set_crop("a.jpg", {"x": 0, "y": 0, "w": 0.5, "h": 0.1})
    assert caught.value.status == 400
    assert not _crops_file(library).exists()


@pytest.mark.parametrize("bad_id", ["../x.jpg", "/etc/passwd", "a/b/c.jpg", 5, None])
def test_set_crop_rejects_bad_ids(tmp_path, bad_id):
    library = _make_library(tmp_path)
    with pytest.raises(ArtError) as caught:
        library.set_crop(bad_id, CROP_5_3)
    assert caught.value.status in (400, 404)


def test_crop_is_checked_against_the_upright_picture(tmp_path):
    # Stored 2000x1200 with an orientation tag that turns it to 1200x2000.
    library = _make_library(tmp_path)
    exif = Image.Exif()
    exif[0x0112] = 6
    (library_root(library) / "r.jpg").write_bytes(_image_bytes(size=(2000, 1200), exif=exif.tobytes()))
    with pytest.raises(ArtError):
        library.set_crop("r.jpg", _crop_for(2000, 1200))
    upright = _crop_for(1200, 2000, w=0.8)
    assert library.set_crop("r.jpg", upright)["w"] == 0.8


def test_move_keeps_the_crop_under_the_new_id(tmp_path):
    library = _make_library(tmp_path)
    _write_image(library_root(library) / "a.jpg", size=(2000, 1200))
    library.create_folder("Trips")
    library.set_crop("a.jpg", CROP_5_3)
    new_id = library.move("a.jpg", "Trips")
    assert new_id == "Trips/a.jpg"
    index = library.scan()
    assert _piece(index, "Trips/a.jpg")["crop"] == CROP_5_3
    assert list(json.loads(_crops_file(library).read_text())["crops"]) == ["Trips/a.jpg"]


def test_a_failed_move_leaves_the_crop_on_the_old_id(tmp_path, monkeypatch):
    library = _make_library(tmp_path)
    _write_image(library_root(library) / "a.jpg", size=(2000, 1200))
    library.create_folder("Trips")
    library.set_crop("a.jpg", CROP_5_3)

    def broken(self, source, destination):
        raise OSError("disk went away")

    monkeypatch.setattr(Library, "_move_with_sidecar", broken)
    with pytest.raises(OSError):
        library.move("a.jpg", "Trips")
    assert list(json.loads(_crops_file(library).read_text())["crops"]) == ["a.jpg"]


def test_trash_and_restore_keep_the_crop(tmp_path):
    clock = FakeClock()
    library = _make_library(tmp_path, clock=clock)
    _write_image(library_root(library) / "a.jpg", size=(2000, 1200))
    library.set_crop("a.jpg", CROP_5_3)
    trash_id = library.delete("a.jpg")
    index = library.scan()
    assert index["pieces"] == []
    assert json.loads(_crops_file(library).read_text())["crops"] == {trash_id: CROP_5_3}
    restored = library.restore(trash_id)
    assert _piece(library.scan(), restored)["crop"] == CROP_5_3


def test_restore_under_a_suffixed_name_carries_the_crop(tmp_path):
    clock = FakeClock()
    library = _make_library(tmp_path, clock=clock)
    _write_image(library_root(library) / "a.jpg", size=(2000, 1200))
    library.set_crop("a.jpg", CROP_5_3)
    trash_id = library.delete("a.jpg")
    _write_image(library_root(library) / "a.jpg", size=(2000, 1200))
    restored = library.restore(trash_id)
    assert restored == "a-1.jpg"
    index = library.scan()
    assert _piece(index, "a-1.jpg")["crop"] == CROP_5_3
    assert _piece(index, "a.jpg")["crop"] is None


def test_purging_the_trash_drops_the_crop(tmp_path):
    clock = FakeClock()
    library = _make_library(tmp_path, clock=clock)
    _write_image(library_root(library) / "a.jpg", size=(2000, 1200))
    library.set_crop("a.jpg", CROP_5_3)
    library.delete("a.jpg")
    clock.now += artlib.TRASH_RETENTION_SECONDS + 10
    library.scan()
    assert json.loads(_crops_file(library).read_text())["crops"] == {}


def test_renaming_a_folder_re_keys_its_crops(tmp_path):
    library = _make_library(tmp_path)
    library.create_folder("Old")
    _write_image(library_root(library) / "Old" / "a.jpg", size=(2000, 1200))
    library.set_crop("Old/a.jpg", CROP_5_3)
    library.rename_folder("Old", "New")
    assert _piece(library.scan(), "New/a.jpg")["crop"] == CROP_5_3


def test_a_crop_for_a_picture_deleted_over_smb_is_pruned_on_scan(tmp_path):
    library = _make_library(tmp_path)
    _write_image(library_root(library) / "a.jpg", size=(2000, 1200))
    _write_image(library_root(library) / "b.jpg", size=(2000, 1200))
    library.set_crop("a.jpg", CROP_5_3)
    library.set_crop("b.jpg", CROP_5_3)
    (library_root(library) / "a.jpg").unlink()
    library.scan(ensure_thumbnails=False)
    assert list(json.loads(_crops_file(library).read_text())["crops"]) == ["b.jpg"]


def test_a_rename_over_smb_loses_the_crop(tmp_path):
    # Accepted and documented: a rename outside the API is a new id.
    library = _make_library(tmp_path)
    _write_image(library_root(library) / "a.jpg", size=(2000, 1200))
    library.set_crop("a.jpg", CROP_5_3)
    (library_root(library) / "a.jpg").rename(library_root(library) / "renamed.jpg")
    index = library.scan(ensure_thumbnails=False)
    assert _piece(index, "renamed.jpg")["crop"] is None
    assert json.loads(_crops_file(library).read_text())["crops"] == {}


def test_a_corrupt_crops_file_reads_as_empty(tmp_path):
    library = _make_library(tmp_path)
    _write_image(library_root(library) / "a.jpg", size=(2000, 1200))
    _crops_file(library).parent.mkdir(parents=True, exist_ok=True)
    _crops_file(library).write_text("{not json", encoding="utf-8")
    assert _piece(library.scan(), "a.jpg")["crop"] is None
    assert library.set_crop("a.jpg", CROP_5_3) == CROP_5_3


def test_a_malformed_stored_crop_is_ignored_by_the_index(tmp_path):
    library = _make_library(tmp_path)
    _write_image(library_root(library) / "a.jpg", size=(2000, 1200))
    _crops_file(library).parent.mkdir(parents=True, exist_ok=True)
    _crops_file(library).write_text(
        json.dumps({"version": 1, "crops": {"a.jpg": {"x": "bad"}}}), encoding="utf-8"
    )
    assert _piece(library.scan(), "a.jpg")["crop"] is None


def test_ente_piece_can_be_cropped_and_keeps_it_across_rescans(tmp_path):
    library = _ente_library(tmp_path)
    _ente_album(tmp_path / "mirror", "alb1", "Album One", {"f1": (1000, 2000)})
    piece_id = "ente-alb1/f1.jpg"
    library.scan()  # the page only offers a picture it has listed
    crop = _crop_for(1000, 2000, w=0.9)
    assert library.set_crop(piece_id, crop)["w"] == 0.9
    for _ in range(2):
        piece = _piece(library.scan(), piece_id)
        assert piece["crop"]["w"] == 0.9
        assert piece["landscape"] is True
        assert piece["auto_crop"] is not None
    # A re-sync rewrites the mirror picture; the crop stays on its id.
    _write_image(tmp_path / "mirror" / "alb1" / "f1.jpg", size=(1000, 2000), color=(1, 2, 3))
    assert _piece(library.scan(), piece_id)["crop"]["w"] == 0.9


def test_cropping_an_ente_piece_writes_nothing_to_the_ente_folders(tmp_path):
    library = _ente_library(tmp_path)
    _ente_album(tmp_path / "mirror", "alb1", "Album One", {"f1": (1000, 2000)})
    library.scan()

    def snapshot():
        return {
            str(p): p.stat().st_mtime_ns
            for base in (tmp_path / "mirror", tmp_path / "public" / "art-ente")
            for p in base.rglob("*")
        }

    before = snapshot()
    library.set_crop("ente-alb1/f1.jpg", _crop_for(1000, 2000, w=0.9))
    library.set_crop("ente-alb1/f1.jpg", None)
    assert snapshot() == before


def test_ente_crop_is_kept_while_the_cache_is_cold_and_pruned_when_the_picture_goes(tmp_path):
    library = _ente_library(tmp_path)
    _ente_album(tmp_path / "mirror", "alb1", "Album One", {"f1": (1000, 2000)})
    library.scan()
    piece_id = "ente-alb1/f1.jpg"
    library.set_crop(piece_id, _crop_for(1000, 2000, w=0.9))
    # A cold rendition cache (metadata-only scan lists no Ente piece) must not
    # cost the owner their crop.
    for rendition in (tmp_path / "public" / "art-ente").rglob("*.jpg"):
        rendition.unlink()
    library.scan(ensure_thumbnails=False)
    assert piece_id in json.loads(_crops_file(library).read_text())["crops"]
    # The picture really leaving the album drops it.
    (tmp_path / "mirror" / "alb1" / "f1.jpg").unlink()
    (tmp_path / "mirror" / "alb1" / "album.json").write_text(
        json.dumps({"title": "Album One", "updated_at": 2, "files": {}}), encoding="utf-8"
    )
    library.scan()
    assert json.loads(_crops_file(library).read_text())["crops"] == {}


def test_ente_crops_are_kept_when_the_mirror_root_is_unreadable(tmp_path):
    library = _ente_library(tmp_path)
    _ente_album(tmp_path / "mirror", "alb1", "Album One", {"f1": (1000, 2000)})
    library.scan()
    piece_id = "ente-alb1/f1.jpg"
    library.set_crop(piece_id, _crop_for(1000, 2000, w=0.9))
    mirror = tmp_path / "mirror"
    mirror.rename(tmp_path / "mirror-away")  # an unmounted export
    library.scan(ensure_thumbnails=False)
    assert piece_id in json.loads(_crops_file(library).read_text())["crops"]


def test_ente_piece_unknown_id_is_404(tmp_path):
    library = _ente_library(tmp_path)
    with pytest.raises(ArtError) as caught:
        library.set_crop("ente-alb1/nope.jpg", CROP_5_3)
    assert caught.value.status == 404


def test_command_show_accepts_a_tall_piece_with_a_crop(tmp_path):
    library = _make_library(tmp_path)
    _write_image(library_root(library) / "tall.jpg", size=(1000, 2000))
    with pytest.raises(ArtError):
        library.command("show", "tall.jpg")
    library.set_crop("tall.jpg", _crop_for(1000, 2000, w=0.8))
    assert library.command("show", "tall.jpg")["piece"] == "tall.jpg"
