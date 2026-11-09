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


def test_scan_landscape_threshold_is_1_3(tmp_path):
    library = _make_library(tmp_path)
    _write_image(library_root(library) / "square-ish.jpg", size=(130, 100))
    _write_image(library_root(library) / "just-under.jpg", size=(129, 100))

    index = library.scan()

    assert _piece(index, "square-ish.jpg")["landscape"] is True
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
    data = _image_bytes(size=(400, 200))  # 80000 px, far over 2x the lowered cap

    with pytest.raises(ArtError) as excinfo:
        library.upload("bomb.jpg", data, "Collection", None, None)
    assert excinfo.value.status == 413
    assert list(library_root(library).iterdir()) == []


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
    assert control["command"] is None


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
