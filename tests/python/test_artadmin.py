"""Tests for the hub's art admin service (lib/snaphouse/artadmin.py).

The service wraps Task 1's ``Library`` in a stdlib ``ThreadingHTTPServer`` bound
to loopback. nginx proxies ``/api/art/`` to it and sets the upload limit; the
service enforces the header guard, the per-file size cap, JSON errors, and the
two public documents it rewrites after every change. Every test spins up a real
server on an ephemeral port in ``tmp_path`` (a throwaway library, thumbs dir,
state file, and public dir), so no test touches the real share or a fixed port.
Images are generated with Pillow (a dev and hub dependency).
"""

from __future__ import annotations

import io
import json
import socket
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

import pytest
from PIL import Image

from snaphouse import artadmin
from snaphouse.artlib import Library
from snaphouse.framecontrol import FrameControl


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _image_bytes(size=(400, 200), color=(120, 160, 200), fmt="JPEG") -> bytes:
    image = Image.new("RGB", size, color)
    buffer = io.BytesIO()
    image.save(buffer, format=fmt)
    return buffer.getvalue()


def _write_image(path: Path, size=(400, 200), color=(120, 160, 200), fmt="JPEG") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_image_bytes(size=size, color=color, fmt=fmt))


# The frames most tests exercise: the original single frame plus a second frame,
# so a scoped command or pin can be checked against another frame.
FRAME = "nightstand"
OTHER = "office"


def _make_app(
    tmp_path: Path,
    *,
    art_history: Path = None,
    geocode=None,
    frames=(FRAME, OTHER),
) -> artadmin.ArtAdmin:
    root = tmp_path / "art"
    thumbs = tmp_path / "public" / "art-thumbs"
    state = tmp_path / "state" / "art-control.json"
    index_path = tmp_path / "public" / "art" / "art-index.json"
    control_path = tmp_path / "public" / "art" / "art-control.json"
    frame_state = tmp_path / "state" / "frame-control.json"
    frame_control_path = tmp_path / "public" / "art" / "frame-control.json"
    root.mkdir(parents=True, exist_ok=True)
    library = Library(root, thumbs, state)
    # art_history is accepted as a single path (the nightstand's, the common case
    # in these tests) or a per-frame mapping.
    if art_history is not None and not isinstance(art_history, dict):
        art_history = {FRAME: art_history}
    frame = FrameControl(
        frame_state,
        frames=list(frames),
        legacy_frame=FRAME,
        art_history=art_history,
        geocode=geocode,
    )
    return artadmin.ArtAdmin(
        library,
        index_path=index_path,
        control_path=control_path,
        frame=frame,
        frame_control_path=frame_control_path,
    )


class Client:
    """A started server on an ephemeral port, with helpers to call it."""

    def __init__(self, app: artadmin.ArtAdmin) -> None:
        self.app = app
        self.server = artadmin.make_server(app, "127.0.0.1", 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.server.server_address[1]

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def _url(self, path: str) -> str:
        return f"http://127.0.0.1:{self.port}{path}"

    def request(self, method, path, *, body=None, content_type=None, header=True):
        headers = {}
        if header:
            headers["X-Snaphouse"] = "1"
        if content_type is not None:
            headers["Content-Type"] = content_type
        req = urllib.request.Request(
            self._url(path), data=body, headers=headers, method=method
        )
        try:
            with urllib.request.urlopen(req) as response:
                return response.status, _read_json(response)
        except urllib.error.HTTPError as error:
            return error.code, _read_json(error)

    def post_json(self, path, obj, *, header=True):
        body = json.dumps(obj).encode("utf-8")
        return self.request(
            "POST", path, body=body, content_type="application/json", header=header
        )

    def get(self, path, *, header=True):
        return self.request("GET", path, header=header)

    def upload(self, files, fields, *, header=True):
        body, content_type = _multipart(files, fields)
        return self.request(
            "POST", "/api/art/upload", body=body, content_type=content_type, header=header
        )


def _read_json(response):
    raw = response.read()
    if not raw:
        return None
    try:
        return json.loads(raw.decode("utf-8"))
    except ValueError:
        return raw


def _multipart(files, fields):
    """Build a multipart/form-data body. ``files`` is a list of (name, filename,
    bytes); ``fields`` is a dict of plain string fields."""
    boundary = uuid.uuid4().hex
    parts = []
    for name, value in fields.items():
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n'.encode("utf-8")
            + value.encode("utf-8")
            + b"\r\n"
        )
    for name, filename, data in files:
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; '
            f'filename="{filename}"\r\nContent-Type: application/octet-stream\r\n\r\n'.encode("utf-8")
            + data
            + b"\r\n"
        )
    parts.append(f"--{boundary}--\r\n".encode("utf-8"))
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


@pytest.fixture
def client(tmp_path):
    app = _make_app(tmp_path)
    started = Client(app)
    yield started
    started.close()


def _index(app: artadmin.ArtAdmin) -> dict:
    return json.loads(app.index_path.read_text(encoding="utf-8"))


def _control(app: artadmin.ArtAdmin) -> dict:
    return json.loads(app.control_path.read_text(encoding="utf-8"))


def _frame_doc(app: artadmin.ArtAdmin) -> dict:
    return json.loads(app.frame_control_path.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- #
# Publishing the two public documents
# --------------------------------------------------------------------------- #


def test_publish_writes_both_documents(tmp_path):
    app = _make_app(tmp_path)
    _write_image(app.library.root / "met-1.jpg")

    app.publish()

    index = _index(app)
    assert index["pieces"][0]["id"] == "met-1.jpg"
    control = _control(app)
    assert control["settings"]["rotate_minutes"] == 60
    assert control["command"] is None


def test_startup_publishes_before_any_request(client):
    # Constructing the server publishes once, so the documents exist before a
    # single request arrives (a frame polling at boot gets a real index).
    assert client.app.index_path.is_file()
    assert client.app.control_path.is_file()


# --------------------------------------------------------------------------- #
# Header guard and method guard
# --------------------------------------------------------------------------- #


def test_post_without_header_is_403(client):
    status, body = client.post_json(
        "/api/art/folder/create", {"name": "Photos"}, header=False
    )
    assert status == 403
    assert "error" in body
    # Nothing was created: the index still has no Photos folder.
    names = [f["name"] for f in _index(client.app)["folders"]]
    assert "Photos" not in names


def test_every_post_route_requires_the_header(client):
    routes = [
        "/api/art/upload",
        "/api/art/delete",
        "/api/art/restore",
        "/api/art/move",
        "/api/art/folder/create",
        "/api/art/folder/rename",
        "/api/art/folder/delete",
        "/api/art/folder/enable",
        "/api/art/settings",
        "/api/art/command",
    ]
    for path in routes:
        status, _ = client.post_json(path, {}, header=False)
        assert status == 403, f"{path} did not require the header"


def test_get_on_a_post_route_is_405(client):
    status, body = client.get("/api/art/settings")
    assert status == 405
    assert "error" in body


def test_unknown_endpoint_is_404(client):
    status, _ = client.post_json("/api/art/nope", {})
    assert status == 404


# --------------------------------------------------------------------------- #
# Folder endpoints
# --------------------------------------------------------------------------- #


def test_create_folder_happy_path(client):
    status, body = client.post_json("/api/art/folder/create", {"name": "Photos"})
    assert status == 200
    assert body["folder"] == "Photos"
    assert (client.app.library.root / "Photos").is_dir()
    names = [f["name"] for f in _index(client.app)["folders"]]
    assert "Photos" in names


def test_create_folder_rejects_collection_case_insensitively(client):
    for name in ("Collection", "collection", "COLLECTION", "CoLLeCtiON"):
        status, body = client.post_json("/api/art/folder/create", {"name": name})
        assert status == 409, f"{name} was not rejected as reserved"
        assert "error" in body


def test_create_duplicate_folder_is_409(client):
    client.post_json("/api/art/folder/create", {"name": "Photos"})
    status, _ = client.post_json("/api/art/folder/create", {"name": "Photos"})
    assert status == 409


def test_rename_folder_carries_enabled_state(client):
    client.post_json("/api/art/folder/create", {"name": "Photos"})
    client.post_json("/api/art/folder/enable", {"name": "Photos", "enabled": True})

    status, body = client.post_json(
        "/api/art/folder/rename", {"from": "Photos", "to": "Travel"}
    )
    assert status == 200
    assert body["folder"] == "Travel"
    folders = {f["name"]: f for f in _index(client.app)["folders"]}
    assert "Travel" in folders
    assert folders["Travel"]["enabled"] is True


def test_rename_onto_collection_is_409(client):
    client.post_json("/api/art/folder/create", {"name": "Photos"})
    status, _ = client.post_json(
        "/api/art/folder/rename", {"from": "Photos", "to": "collection"}
    )
    assert status == 409


def test_delete_empty_folder(client):
    client.post_json("/api/art/folder/create", {"name": "Photos"})
    status, body = client.post_json("/api/art/folder/delete", {"name": "Photos"})
    assert status == 200
    assert not (client.app.library.root / "Photos").exists()


def test_delete_nonempty_folder_is_409(client):
    client.post_json("/api/art/folder/create", {"name": "Photos"})
    _write_image(client.app.library.root / "Photos" / "beach.jpg")
    status, _ = client.post_json("/api/art/folder/delete", {"name": "Photos"})
    assert status == 409


def test_enable_toggles_rotation_in_control_file(client):
    client.post_json("/api/art/folder/create", {"name": "Photos"})
    status, _ = client.post_json(
        "/api/art/folder/enable", {"name": "Photos", "enabled": True}
    )
    assert status == 200
    assert "Photos" in _control(client.app)["settings"]["folders_enabled"]


# --------------------------------------------------------------------------- #
# Upload
# --------------------------------------------------------------------------- #


def test_upload_stores_an_image(client):
    client.post_json("/api/art/folder/create", {"name": "Photos"})
    status, body = client.upload(
        [("file", "Beach Day.JPG", _image_bytes())],
        {"folder": "Photos", "title": "Beach"},
    )
    assert status == 200
    assert body["ids"] == ["Photos/beach-day.jpg"]
    assert (client.app.library.root / "Photos" / "beach-day.jpg").is_file()
    pieces = {p["id"]: p for p in _index(client.app)["pieces"]}
    assert pieces["Photos/beach-day.jpg"]["title"] == "Beach"


def test_upload_several_files(client):
    client.post_json("/api/art/folder/create", {"name": "Photos"})
    status, body = client.upload(
        [
            ("file", "one.jpg", _image_bytes(color=(10, 20, 30))),
            ("file", "two.jpg", _image_bytes(color=(40, 50, 60))),
        ],
        {"folder": "Photos"},
    )
    assert status == 200
    assert set(body["ids"]) == {"Photos/one.jpg", "Photos/two.jpg"}


def test_upload_of_a_non_image_is_400(client):
    client.post_json("/api/art/folder/create", {"name": "Photos"})
    status, body = client.upload(
        [("file", "note.txt", b"this is not an image")],
        {"folder": "Photos"},
    )
    assert status == 400
    assert "error" in body


def test_upload_over_the_limit_is_413(client):
    client.post_json("/api/art/folder/create", {"name": "Photos"})
    # A 26 MB payload: over the 25 MB per-file limit and over the request cap.
    oversized = b"\xff" * (26 * 1024 * 1024)
    status, _ = client.upload(
        [("file", "huge.jpg", oversized)],
        {"folder": "Photos"},
    )
    assert status == 413


# --------------------------------------------------------------------------- #
# Delete, restore, move
# --------------------------------------------------------------------------- #


def test_delete_then_restore_round_trips(client):
    _write_image(client.app.library.root / "met-1.jpg")
    client.app.publish()

    status, body = client.post_json("/api/art/delete", {"id": "met-1.jpg"})
    assert status == 200
    trash_id = body["id"]
    assert not (client.app.library.root / "met-1.jpg").exists()
    assert _index(client.app)["pieces"] == []

    status, body = client.post_json("/api/art/restore", {"id": trash_id})
    assert status == 200
    assert body["id"] == "met-1.jpg"
    assert (client.app.library.root / "met-1.jpg").is_file()


def test_move_between_folders(client):
    _write_image(client.app.library.root / "met-1.jpg")
    client.post_json("/api/art/folder/create", {"name": "Photos"})
    status, body = client.post_json(
        "/api/art/move", {"id": "met-1.jpg", "folder": "Photos"}
    )
    assert status == 200
    assert body["id"] == "Photos/met-1.jpg"


# --------------------------------------------------------------------------- #
# Traversal (Review Focus 1)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "bad_id",
    [
        "../etc/passwd",
        "Photos/../../x.jpg",
        "/etc/passwd",
        ".trash/2026-01-01T00-00-00/x.jpg",
        "a/b/c.jpg",
        "Photos/sub/x.jpg",
    ],
)
def test_delete_rejects_traversal_ids(client, bad_id):
    status, body = client.post_json("/api/art/delete", {"id": bad_id})
    assert status in (400, 404)
    # A traversal id is a 400 (never a 404), so nothing outside the root is read.
    if ".." in bad_id or bad_id.startswith("/") or bad_id.count("/") >= 2:
        assert status == 400
    assert "error" in body


@pytest.mark.parametrize(
    "bad_id",
    ["../../etc", "Photos/../../x.jpg", "/abs/path", "a/b/c.jpg"],
)
def test_move_rejects_traversal_ids(client, bad_id):
    status, _ = client.post_json("/api/art/move", {"id": bad_id, "folder": "Collection"})
    assert status == 400


def test_show_rejects_traversal_piece(client):
    status, _ = client.post_json(
        "/api/art/command", {"action": "show", "piece": "../../etc/passwd"}
    )
    assert status == 400


# --------------------------------------------------------------------------- #
# Settings and commands
# --------------------------------------------------------------------------- #


def test_settings_sets_the_interval(client):
    status, body = client.post_json("/api/art/settings", {"rotate_minutes": 120})
    assert status == 200
    assert body["rotate_minutes"] == 120
    assert _control(client.app)["settings"]["rotate_minutes"] == 120


def test_settings_rejects_a_bad_interval(client):
    status, _ = client.post_json("/api/art/settings", {"rotate_minutes": 7})
    assert status == 400


def test_command_next_increments_seq(client):
    status, body = client.post_json("/api/art/command", {"action": "next"})
    assert status == 200
    assert body["command"]["action"] == "next"
    assert body["command"]["seq"] == 1
    assert _control(client.app)["command"]["seq"] == 1


def test_command_show_requires_landscape(client):
    # A portrait piece cannot be shown on the frame.
    _write_image(client.app.library.root / "tall.jpg", size=(200, 400))
    client.app.publish()
    status, _ = client.post_json(
        "/api/art/command", {"action": "show", "piece": "tall.jpg"}
    )
    assert status == 409


def test_command_show_landscape_ok(client):
    _write_image(client.app.library.root / "wide.jpg", size=(400, 200))
    client.app.publish()
    status, body = client.post_json(
        "/api/art/command", {"action": "show", "piece": "wide.jpg"}
    )
    assert status == 200
    assert body["command"]["action"] == "show"
    assert body["command"]["piece"] == "wide.jpg"


def test_command_rejects_unknown_action(client):
    status, _ = client.post_json("/api/art/command", {"action": "spin"})
    assert status == 400


# --------------------------------------------------------------------------- #
# Both public documents are rewritten after a change
# --------------------------------------------------------------------------- #


def test_a_change_rewrites_both_documents(client):
    before_index = client.app.index_path.read_text(encoding="utf-8")
    before_control = client.app.control_path.read_text(encoding="utf-8")

    client.post_json("/api/art/folder/create", {"name": "Photos"})
    client.post_json("/api/art/settings", {"rotate_minutes": 180})

    assert client.app.index_path.read_text(encoding="utf-8") != before_index
    assert client.app.control_path.read_text(encoding="utf-8") != before_control
    assert "Photos" in [f["name"] for f in _index(client.app)["folders"]]
    assert _control(client.app)["settings"]["rotate_minutes"] == 180


def test_bad_json_body_is_400(client):
    status, _ = client.request(
        "POST",
        "/api/art/settings",
        body=b"{not json",
        content_type="application/json",
    )
    assert status == 400


def test_bad_content_length_header_is_400(client):
    # A malformed Content-Length must be a clean 400, not a leaked 500. urllib
    # always sends a valid one, so send the request over a raw socket.
    request = (
        "POST /api/art/settings HTTP/1.1\r\n"
        "Host: 127.0.0.1\r\n"
        "X-Snaphouse: 1\r\n"
        "Content-Type: application/json\r\n"
        "Content-Length: not-a-number\r\n"
        "Connection: close\r\n"
        "\r\n"
    )
    with socket.create_connection(("127.0.0.1", client.port), timeout=5) as sock:
        sock.sendall(request.encode("ascii"))
        status_line = sock.recv(256).decode("latin-1").splitlines()[0]
    assert "400" in status_line


# --------------------------------------------------------------------------- #
# Hotfix: publish the index before thumbnails (metadata-only startup pass)
# --------------------------------------------------------------------------- #


def test_first_publish_lists_everything_before_thumbnails(tmp_path, monkeypatch):
    # The live hub regressed because the first publish generated a thumbnail for
    # every one of 172 pieces (about two minutes) before writing the index, so
    # verify got a 404 and self-update rolled back. The startup publish must list
    # the whole library at once with null thumbs and do no thumbnail work.
    app = _make_app(tmp_path)
    for i in range(200):
        _write_image(app.library.root / f"met-{i:03d}.jpg", size=(40, 30))

    # Inject a slow thumbnailer: if the startup publish touched it at all the
    # test would see a call. It must not be called.
    calls = []

    def slow_thumbnail(*args, **kwargs):
        calls.append(args)
        raise AssertionError("the startup publish must not generate thumbnails")

    monkeypatch.setattr(app.library, "_ensure_thumbnail", slow_thumbnail)

    app.publish()

    index = json.loads(app.index_path.read_text(encoding="utf-8"))
    assert len(index["pieces"]) == 200
    assert all(piece["thumb"] is None for piece in index["pieces"])
    assert calls == []


def test_background_pass_fills_thumbnails_and_republishes(tmp_path):
    app = _make_app(tmp_path)
    for i in range(12):
        _write_image(app.library.root / f"met-{i:02d}.jpg", size=(40, 30))
    app.publish()  # metadata-only: all thumbs null

    # One background maintenance pass generates the thumbnails and republishes.
    app.run_thumbnail_pass()

    index = json.loads(app.index_path.read_text(encoding="utf-8"))
    assert len(index["pieces"]) == 12
    assert all(piece["thumb"] is not None for piece in index["pieces"])


def test_make_server_publishes_without_thumbnail_work(tmp_path, monkeypatch):
    # The publish inside make_server (before the server serves) must be the fast
    # metadata-only pass too, so a frame polling at boot gets a real index.
    app = _make_app(tmp_path)
    for i in range(50):
        _write_image(app.library.root / f"met-{i:02d}.jpg", size=(40, 30))

    monkeypatch.setattr(
        app.library,
        "_ensure_thumbnail",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("no thumbnails at startup")),
    )
    server = artadmin.make_server(app, "127.0.0.1", 0)
    try:
        index = json.loads(app.index_path.read_text(encoding="utf-8"))
        assert len(index["pieces"]) == 50
    finally:
        server.server_close()


# --------------------------------------------------------------------------- #
# Frame control: settings, pin, unpin
# --------------------------------------------------------------------------- #


def test_frame_control_is_published_at_startup(tmp_path):
    # make_server publishes every document before serving, so frame-control.json
    # exists (with defaults) even before the first change.
    app = _make_app(tmp_path)
    server = artadmin.make_server(app, "127.0.0.1", 0)
    try:
        doc = _frame_doc(app)
        assert doc["frames"][FRAME]["pin"] is None
        assert doc["settings"]["now_playing_kinds"] == ["music"]
    finally:
        server.server_close()


def test_frame_settings_update_is_stored_and_published(client):
    status, body = client.post_json(
        "/api/frame/settings", {"now_playing_kinds": ["music", "podcast"]}
    )
    assert status == 200
    assert body["settings"]["now_playing_kinds"] == ["music", "podcast"]
    # The published document reflects the change after the request.
    assert _frame_doc(client.app)["settings"]["now_playing_kinds"] == ["music", "podcast"]


def test_frame_settings_validation_error_is_a_json_400(client):
    status, body = client.post_json(
        "/api/frame/settings", {"now_playing_kinds": ["radio"]}
    )
    assert status == 400
    assert "error" in body


def test_frame_settings_bad_time_is_a_json_400(client):
    status, body = client.post_json(
        "/api/frame/settings", {"quiet_hours": {"start": "24:00"}}
    )
    assert status == 400
    assert "error" in body


def _geocoder(body):
    """A geocoder fetch that serves one recorded body for any city (or None)."""
    def fetch(url):
        if body is None:
            return None
        return json.dumps(body).encode("utf-8")
    return fetch


_SAN_DIEGO_BODY = {
    "results": [
        {
            "name": "San Diego", "latitude": 32.71533, "longitude": -117.15726,
            "timezone": "America/Los_Angeles", "population": 1394928,
            "admin1": "California", "country": "United States", "country_code": "US",
        }
    ]
}


def test_frame_weather_location_geocodes_and_publishes(tmp_path):
    app = _make_app(tmp_path, geocode=_geocoder(_SAN_DIEGO_BODY))
    started = Client(app)
    try:
        status, body = started.post_json(
            "/api/frame/settings", {"weather_location": {"query": "San Diego, CA"}}
        )
        assert status == 200
        assert body["settings"]["weather_location"] == {
            "name": "San Diego, California, United States",
            "latitude": 32.72,
            "longitude": -117.16,
            "timezone": "America/Los_Angeles",
        }
        published = _frame_doc(app)["settings"]["weather_location"]
        assert published["name"] == "San Diego, California, United States"
    finally:
        started.close()


def test_frame_weather_location_no_match_is_a_json_400(tmp_path):
    app = _make_app(tmp_path, geocode=_geocoder({"generationtime_ms": 0.1}))
    started = Client(app)
    try:
        status, body = started.post_json(
            "/api/frame/settings", {"weather_location": {"query": "Nowhereville, ZZ"}}
        )
        assert status == 400
        assert "Nowhereville, ZZ" in body["error"]
    finally:
        started.close()


def test_slow_geocoder_does_not_block_other_writes(tmp_path):
    # The geocode runs before the library write lock is taken, so a slow geocoder
    # (here blocked until released) must not stall a concurrent art-admin write.
    # Under the old shape (geocode under the lock) the pin below would wait out
    # the whole geocode; here it returns promptly.
    started = threading.Event()
    release = threading.Event()

    def slow_fetch(url):
        started.set()
        release.wait(10)
        return json.dumps(_SAN_DIEGO_BODY).encode("utf-8")

    app = _make_app(tmp_path, geocode=slow_fetch)
    client = Client(app)
    try:
        result = {}

        def do_geocode():
            result["status"], result["body"] = client.post_json(
                "/api/frame/settings", {"weather_location": {"query": "San Diego, CA"}}
            )

        geocode_thread = threading.Thread(target=do_geocode)
        geocode_thread.start()
        # The geocode is in flight (and would be holding the lock in the old code).
        assert started.wait(5), "the geocode never started"

        # A concurrent write completes without waiting out the blocked geocode.
        begin = time.monotonic()
        status, body = client.post_json(
            "/api/frame/pin", {"frame": FRAME, "what": "weather", "duration": "forever"}
        )
        elapsed = time.monotonic() - begin
        assert status == 200
        assert elapsed < 3.0, f"a concurrent write stalled {elapsed:.1f}s on the geocode"

        # Let the geocode finish and confirm it still stores its result.
        release.set()
        geocode_thread.join(10)
        assert result["status"] == 200
        assert result["body"]["settings"]["weather_location"]["name"] == (
            "San Diego, California, United States"
        )
    finally:
        release.set()
        client.close()


def test_frame_weather_location_overlong_query_is_a_json_400(tmp_path):
    app = _make_app(tmp_path, geocode=_geocoder(_SAN_DIEGO_BODY))
    started = Client(app)
    try:
        status, body = started.post_json(
            "/api/frame/settings", {"weather_location": {"query": "x" * 200}}
        )
        assert status == 400
        assert "error" in body
    finally:
        started.close()


def test_frame_weather_location_unreachable_is_a_json_502(tmp_path):
    app = _make_app(tmp_path, geocode=_geocoder(None))
    started = Client(app)
    try:
        status, body = started.post_json(
            "/api/frame/settings", {"weather_location": {"query": "San Diego, CA"}}
        )
        assert status == 502
        assert "error" in body
    finally:
        started.close()


def test_frame_pin_sets_and_publishes_the_pin(client):
    status, body = client.post_json(
        "/api/frame/pin", {"frame": FRAME, "what": "weather", "duration": "forever"}
    )
    assert status == 200
    assert body["pin"]["what"] == "weather"
    assert body["pin"]["until"] is None
    assert _frame_doc(client.app)["frames"][FRAME]["pin"]["what"] == "weather"


def test_frame_pin_art_captures_the_current_piece(tmp_path):
    history = tmp_path / "public" / "art-history.json"
    history.parent.mkdir(parents=True, exist_ok=True)
    history.write_text(
        json.dumps({"current": {"file": "a.jpg", "id": "Photos/a.jpg", "title": "X"}, "history": []}),
        encoding="utf-8",
    )
    app = _make_app(tmp_path, art_history=history)
    started = Client(app)
    try:
        status, body = started.post_json(
            "/api/frame/pin", {"frame": FRAME, "what": "art", "duration": "forever"}
        )
        assert status == 200
        assert body["pin"]["piece"] == "Photos/a.jpg"
    finally:
        started.close()


def test_frame_pin_missing_field_is_a_json_400(client):
    status, body = client.post_json("/api/frame/pin", {"frame": FRAME, "what": "weather"})
    assert status == 400
    assert "error" in body


def test_frame_pin_bad_duration_is_a_json_400(client):
    status, body = client.post_json(
        "/api/frame/pin", {"frame": FRAME, "what": "weather", "duration": "2h"}
    )
    assert status == 400
    assert "error" in body


def test_frame_unpin_clears_the_pin(client):
    client.post_json("/api/frame/pin", {"frame": FRAME, "what": "weather", "duration": "forever"})
    status, body = client.post_json("/api/frame/unpin", {"frame": FRAME})
    assert status == 200
    assert body["pin"] is None
    assert _frame_doc(client.app)["frames"][FRAME]["pin"] is None


def test_frame_pin_seq_increases(client):
    _, first = client.post_json(
        "/api/frame/pin", {"frame": FRAME, "what": "weather", "duration": "forever"}
    )
    _, second = client.post_json(
        "/api/frame/pin", {"frame": FRAME, "what": "art", "duration": "forever"}
    )
    assert second["pin"]["seq"] > first["pin"]["seq"]


def test_frame_routes_require_the_header_guard(client):
    for path, payload in (
        ("/api/frame/settings", {"now_playing_kinds": ["music"]}),
        ("/api/frame/pin", {"frame": FRAME, "what": "weather", "duration": "forever"}),
        ("/api/frame/unpin", {"frame": FRAME}),
    ):
        status, body = client.post_json(path, payload, header=False)
        assert status == 403, path
        assert "error" in body
    # Nothing was written: the pin is still unset.
    assert _frame_doc(client.app)["frames"][FRAME]["pin"] is None


# --------------------------------------------------------------------------- #
# Frame-scoped art commands and per-frame isolation over HTTP
# --------------------------------------------------------------------------- #


def test_art_command_without_frame_reaches_every_frame(client):
    # A command with no frame is null-scoped: compatible with today, every frame
    # acts on it.
    status, body = client.post_json("/api/art/command", {"action": "next"})
    assert status == 200
    assert body["command"]["frame"] is None
    assert _control(client.app)["command"]["frame"] is None


def test_art_command_with_frame_is_scoped(client):
    status, body = client.post_json(
        "/api/art/command", {"action": "next", "frame": OTHER}
    )
    assert status == 200
    assert body["command"]["frame"] == OTHER
    assert _control(client.app)["command"]["frame"] == OTHER


def test_art_command_unknown_frame_is_a_404(client):
    status, body = client.post_json(
        "/api/art/command", {"action": "next", "frame": "living-room"}
    )
    assert status == 404
    assert "error" in body
    # Nothing was recorded: the control document still carries no command.
    assert _control(client.app)["command"] is None


def test_art_command_malformed_frame_is_a_400(client):
    status, body = client.post_json(
        "/api/art/command", {"action": "next", "frame": "Office/.."}
    )
    assert status == 400
    assert "error" in body


def test_frame_pin_unknown_frame_is_a_404(client):
    status, body = client.post_json(
        "/api/frame/pin", {"frame": "living-room", "what": "weather", "duration": "forever"}
    )
    assert status == 404
    assert "error" in body


def test_frame_pin_malformed_frame_is_a_400(client):
    status, body = client.post_json(
        "/api/frame/pin", {"frame": "Office", "what": "weather", "duration": "forever"}
    )
    assert status == 400
    assert "error" in body


def test_frame_pin_touches_only_the_named_frame(client):
    client.post_json(
        "/api/frame/pin", {"frame": FRAME, "what": "weather", "duration": "forever"}
    )
    frames = _frame_doc(client.app)["frames"]
    assert frames[FRAME]["pin"]["what"] == "weather"
    assert frames[OTHER]["pin"] is None


def test_frame_settings_per_frame_quiet_hours(client):
    status, body = client.post_json(
        "/api/frame/settings", {"frame": OTHER, "quiet_hours": {"enabled": False}}
    )
    assert status == 200
    assert body["frame"] == OTHER
    assert body["quiet_hours"] == {"enabled": False}
    frames = _frame_doc(client.app)["frames"]
    assert frames[OTHER]["quiet_hours"] == {"enabled": False}
    # The nightstand still inherits the shared quiet hours.
    assert frames[FRAME]["quiet_hours"] is None


def test_frame_settings_unknown_frame_is_a_404(client):
    status, body = client.post_json(
        "/api/frame/settings", {"frame": "living-room", "quiet_hours": None}
    )
    assert status == 404
    assert "error" in body


def test_frame_doc_lists_every_frame(client):
    assert set(_frame_doc(client.app)["frames"]) == {FRAME, OTHER}
