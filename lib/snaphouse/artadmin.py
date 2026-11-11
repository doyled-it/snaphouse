"""The hub's art admin service: a loopback HTTP API over Task 1's library.

This is the one hub service that writes the art library. It wraps
``snaphouse.artlib.Library`` in a stdlib ``ThreadingHTTPServer`` bound to
``127.0.0.1:8781`` and exposes the small POST API the room-picker page calls
(upload, delete, restore, move, folders, settings, commands). nginx proxies
``/api/art/`` to it and caps the upload size; the service runs as the ``art``
user that owns the library, so SMB access keeps working.

It also publishes the two same-origin documents the page and the frame read:
``art-index.json`` (the full library listing from ``Library.scan``) and
``art-control.json`` (the rotation settings and last command). Both are rewritten
atomically after every change and by a background rescan every 60 seconds, so an
edit made over SMB shows up without an API call. The documents and the thumbnail
directory live in a dedicated subdirectory owned by this service rather than the
root-owned public web root, so re-owning never fights the web role (the art-share
idempotency gate in CI).

The design is in ``docs/superpowers/specs/2026-11-09-art-rotation-design.md``;
the library module carries the validation and the file shapes. Everything here is
HTTP plumbing: the header guard, method and route dispatch, multipart and JSON
parsing, size limits, and turning an ``ArtError`` into a JSON error response.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import tempfile
import threading
import urllib.parse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional

from snaphouse.artlib import THUMBNAIL_BATCH, ArtError, Library
from snaphouse.framecontrol import DEFAULT_LEGACY_FRAME, FrameControl, urllib_geocode


# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #

#: Default loopback port; nginx proxies /api/art/ here. Never bound off 127.0.0.1.
DEFAULT_PORT = 8781

#: Seconds between background rescans, so SMB edits show up without an API call.
RESCAN_SECONDS = 60

#: The custom header every write must carry. A cross-site form cannot set it
#: without a CORS preflight the server never answers, so it blocks CSRF.
GUARD_HEADER = "X-Snaphouse"
GUARD_VALUE = "1"

#: The built-in folder name is reserved case-insensitively at the API: the scan
#: reserves the exact spelling, so a folder named "collection" (any case) would
#: collide with it. Create and rename refuse any case variant with a 409.
_RESERVED_FOLDER = "collection"

#: Hard cap on a request body, matching nginx's ``client_max_body_size 26m``
#: (one 25 MB file plus multipart overhead). A larger Content-Length is refused
#: before the body is read, so an oversized or lying request allocates nothing.
MAX_REQUEST_BYTES = 26 * 1024 * 1024

_API_PREFIX = "/api/art/"


# --------------------------------------------------------------------------- #
# Multipart parsing (stdlib only; cgi.FieldStorage is gone in 3.13)
# --------------------------------------------------------------------------- #


class _UploadPart:
    """One file part of a multipart upload: its form field name, the client's
    filename, and the raw bytes."""

    def __init__(self, name: str, filename: str, data: bytes) -> None:
        self.name = name
        self.filename = filename
        self.data = data


def _parse_multipart(content_type: str, body: bytes) -> tuple[dict, list[_UploadPart]]:
    """Parse a ``multipart/form-data`` body into plain fields and file parts.

    Returns ``(fields, files)``: ``fields`` maps a form name to its decoded
    string value (the last wins on a repeat), ``files`` is every part that
    carried a filename. Raises ``ArtError(400, ...)`` if the body is not a
    multipart form this server can read, so a malformed upload is a clean 400
    rather than a crash.
    """
    boundary = _multipart_boundary(content_type)
    delimiter = b"--" + boundary
    fields: dict = {}
    files: list[_UploadPart] = []

    # Split on the delimiter; the first chunk is the preamble and the last is the
    # closing "--\r\n", both discarded.
    chunks = body.split(delimiter)
    for chunk in chunks[1:]:
        if chunk in (b"", b"--\r\n", b"--", b"\r\n"):
            continue
        if chunk.startswith(b"--"):
            break
        chunk = chunk[2:] if chunk.startswith(b"\r\n") else chunk
        header_blob, sep, data = chunk.partition(b"\r\n\r\n")
        if not sep:
            continue
        if data.endswith(b"\r\n"):
            data = data[:-2]
        name, filename = _part_disposition(header_blob)
        if name is None:
            continue
        if filename is not None:
            files.append(_UploadPart(name, filename, data))
        else:
            fields[name] = data.decode("utf-8", errors="replace")
    return fields, files


def _multipart_boundary(content_type: str) -> bytes:
    for token in content_type.split(";"):
        token = token.strip()
        if token.lower().startswith("boundary="):
            value = token[len("boundary="):].strip().strip('"')
            if value:
                return value.encode("latin-1")
    raise ArtError(400, "That upload was not a valid form submission.")


def _part_disposition(header_blob: bytes) -> tuple[Optional[str], Optional[str]]:
    """Pull the form field name and filename out of a part's headers."""
    name: Optional[str] = None
    filename: Optional[str] = None
    for line in header_blob.split(b"\r\n"):
        text = line.decode("utf-8", errors="replace")
        if text.lower().startswith("content-disposition:"):
            for token in text.split(";")[1:]:
                token = token.strip()
                if token.startswith("name="):
                    name = token[len("name="):].strip().strip('"')
                elif token.startswith("filename="):
                    filename = token[len("filename="):].strip().strip('"')
    return name, filename


# --------------------------------------------------------------------------- #
# The app
# --------------------------------------------------------------------------- #


class ArtAdmin:
    """The HTTP surface over a ``Library``, plus the two published documents.

    ``index_path`` and ``control_path`` are the public ``art-index.json`` and
    ``art-control.json`` files; both are rewritten atomically after every change
    and by the background maintenance pass. One lock serialises every library
    write and every publish, so the threaded server never races itself on the
    filesystem.

    Publishing is metadata-only (fast): it lists the whole library at once with a
    null ``thumb`` for anything not thumbnailed yet, so even a large library
    (the hub's 172 museum pieces) is served immediately and verify never 404s.
    The background pass then generates the thumbnails and republishes in batches.
    """

    def __init__(
        self,
        library: Library,
        *,
        index_path: Path,
        control_path: Path,
        frame: FrameControl,
        frame_control_path: Path,
        log: Optional[logging.Logger] = None,
    ) -> None:
        self.library = library
        self.index_path = Path(index_path)
        self.control_path = Path(control_path)
        # Frame control (now-playing kinds, quiet hours, morning weather, and the
        # pin) rides along with the library in this one service: it publishes
        # frame-control.json next to the two art documents and exposes the small
        # /api/frame/ write API with the same header guard.
        self.frame = frame
        self.frame_control_path = Path(frame_control_path)
        self.log = log or logging.getLogger("snaphouse.artadmin")
        self._lock = threading.Lock()
        # Ends the background maintenance loop; `_wake` nudges it to run a pass
        # now (after an upload) instead of waiting out the interval.
        self._stop = threading.Event()
        self._wake = threading.Event()
        # The routing table: path -> bound handler. Each handler does the library
        # call and returns the JSON result; publishing happens around it.
        self._routes = {
            "/api/art/upload": self._upload,
            "/api/art/delete": self._delete,
            "/api/art/restore": self._restore,
            "/api/art/move": self._move,
            "/api/art/folder/create": self._folder_create,
            "/api/art/folder/rename": self._folder_rename,
            "/api/art/folder/delete": self._folder_delete,
            "/api/art/folder/enable": self._folder_enable,
            "/api/art/settings": self._settings,
            "/api/art/command": self._command,
            "/api/frame/settings": self._frame_settings,
            "/api/frame/pin": self._frame_pin,
            "/api/frame/unpin": self._frame_unpin,
        }

    # -- publishing -------------------------------------------------------- #

    def publish(self) -> None:
        """Rewrite both public documents from the library's current state.

        A metadata-only scan: it reads the library fresh (so SMB edits are
        reflected) and prunes stale thumbnails, but generates none, so it is fast
        even on a large library and a not-yet-thumbnailed piece gets ``thumb:
        null``. ``control`` returns the persisted settings and last command. Both
        files are written atomically, so a reader never sees a half-written one.
        """
        index = self.library.scan(ensure_thumbnails=False)
        _atomic_write_json(self.index_path, index)
        control = self.library.control()
        _atomic_write_json(self.control_path, control)
        _atomic_write_json(self.frame_control_path, self.frame.document())

    def _locked_publish(self) -> None:
        with self._lock:
            self.publish()

    def run_thumbnail_pass(self) -> int:
        """Generate any missing thumbnails and republish as they land.

        The slow decode runs without the lock (so API writes are not blocked);
        each batch of thumbnails triggers a locked republish so the new thumbs
        appear in the index promptly. Returns the number generated. Also serves
        as the periodic rescan: it republishes once at the end to pick up SMB
        edits even when no thumbnail needed generating.
        """
        generated = self.library.ensure_thumbnails(
            batch_size=THUMBNAIL_BATCH,
            on_batch=self._locked_publish,
            should_stop=self._stop.is_set,
        )
        self._locked_publish()
        return generated

    def maintain_forever(self, interval: float = RESCAN_SECONDS) -> None:
        """Background loop: fill missing thumbnails (batched republishes), then
        wait for the interval or a wake signal and rescan again.

        An upload wakes the loop so the new piece's thumbnail appears without
        waiting out the interval. Any error is logged and the loop continues; a
        transient filesystem error (an SMB edit mid-scan) must not kill it.
        """
        while not self._stop.is_set():
            try:
                self.run_thumbnail_pass()
            except Exception:  # noqa: BLE001 - the loop must survive any error
                self.log.exception("art maintenance pass failed")
            self._wake.wait(interval)
            self._wake.clear()

    def wake(self) -> None:
        """Nudge the background loop to run a pass now (after an upload)."""
        self._wake.set()

    def stop(self) -> None:
        """End the background loop."""
        self._stop.set()
        self._wake.set()

    # -- request handling -------------------------------------------------- #

    def handle_post(self, handler: "_Handler") -> None:
        path = urllib.parse.urlsplit(handler.path).path
        route = self._routes.get(path)
        if route is None:
            handler.send_api_error(HTTPStatus.NOT_FOUND, "Unknown endpoint.")
            return
        if handler.headers.get(GUARD_HEADER) != GUARD_VALUE:
            handler.send_api_error(HTTPStatus.FORBIDDEN, "That request is not allowed.")
            return

        try:
            length = _content_length(handler.headers.get("Content-Length"))
        except ArtError as error:
            handler.send_api_error(error.status, error.message)
            return
        if length > MAX_REQUEST_BYTES:
            # Drain the body before answering so the client's send completes and
            # it reads a clean 413 rather than a reset connection. nginx caps the
            # body at the edge too; this is the service's own enforcement.
            _drain(handler.rfile, length)
            handler.send_api_error(
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                "That upload is larger than the 25 MB limit.",
            )
            return
        body = handler.rfile.read(length) if length else b""

        # Slow, network-bound work (the weather geocode) runs here, before the
        # write lock is taken, so a slow geocoder (up to its 10 s timeout) never
        # stalls the thumbnail pass or another write. This mirrors
        # run_thumbnail_pass, which does its slow decode outside the lock. The
        # prepared value feeds the locked step; most routes produce none.
        try:
            prepared = self._prepare(path, body)
        except ArtError as error:
            handler.send_api_error(error.status, error.message)
            return
        except Exception:  # noqa: BLE001 - never leak a traceback to the page
            self.log.exception("art admin prepare failed: %s", path)
            handler.send_api_error(
                HTTPStatus.INTERNAL_SERVER_ERROR, "Something went wrong."
            )
            return

        with self._lock:
            try:
                result = route(handler, body, prepared)
                self.publish()
            except ArtError as error:
                handler.send_api_error(error.status, error.message)
                return
            except Exception:  # noqa: BLE001 - never leak a traceback to the page
                self.log.exception("art admin request failed: %s", path)
                handler.send_api_error(
                    HTTPStatus.INTERNAL_SERVER_ERROR, "Something went wrong."
                )
                return
        # A write may have added a piece with no thumbnail yet (upload, restore,
        # move); wake the background pass so its thumbnail appears without waiting
        # out the interval.
        self.wake()
        handler.send_api_json(HTTPStatus.OK, result)

    def _prepare(self, path: str, body: bytes):
        """Resolve a route's slow, network-bound input before the write lock.

        Only the frame-settings route needs this: a weather-city write is
        geocoded here (a network call up to the 10 s timeout) so it never runs
        under the library lock. Returns the prepared value the route consumes
        under the lock, or ``None`` for a route with no pre-lock work.
        """
        if path == "/api/frame/settings":
            data = _json_body(body)
            # ``frame`` is optional and selects the scope: a per-frame write (a
            # known frame id) sets that frame's quiet hours, and a shared write
            # (no frame) sets the shared settings. Validate the frame here, before
            # the lock, so an unknown (404) or malformed (400) id is rejected
            # without any work; the shared path geocodes a weather city here too.
            frame = data.get("frame")
            if frame is not None:
                self.frame.require_frame(frame)
            subset = {key: value for key, value in data.items() if key != "frame"}
            prepared = self.frame.prepare_settings(subset, frame=frame)
            return {"frame": frame, "subset": prepared}
        return None

    # -- endpoint handlers ------------------------------------------------- #

    def _upload(self, handler: "_Handler", body: bytes, prepared=None) -> dict:
        content_type = handler.headers.get("Content-Type", "")
        if "multipart/form-data" not in content_type.lower():
            raise ArtError(400, "That upload was not a valid form submission.")
        fields, files = _parse_multipart(content_type, body)
        if not files:
            raise ArtError(400, "No file was uploaded.")
        folder = fields.get("folder", "")
        title = fields.get("title")
        artist = fields.get("artist")
        ids = [
            self.library.upload(part.filename, part.data, folder, title, artist)
            for part in files
        ]
        return {"ids": ids}

    def _delete(self, handler: "_Handler", body: bytes, prepared=None) -> dict:
        data = _json_body(body)
        return {"id": self.library.delete(_require_str(data, "id"))}

    def _restore(self, handler: "_Handler", body: bytes, prepared=None) -> dict:
        data = _json_body(body)
        return {"id": self.library.restore(_require_str(data, "id"))}

    def _move(self, handler: "_Handler", body: bytes, prepared=None) -> dict:
        data = _json_body(body)
        new_id = self.library.move(
            _require_str(data, "id"), _require_str(data, "folder")
        )
        return {"id": new_id}

    def _folder_create(self, handler: "_Handler", body: bytes, prepared=None) -> dict:
        data = _json_body(body)
        name = _require_str(data, "name")
        _reject_reserved_name(name)
        return {"folder": self.library.create_folder(name)}

    def _folder_rename(self, handler: "_Handler", body: bytes, prepared=None) -> dict:
        data = _json_body(body)
        target = _require_str(data, "to")
        _reject_reserved_name(target)
        folder = self.library.rename_folder(_require_str(data, "from"), target)
        return {"folder": folder}

    def _folder_delete(self, handler: "_Handler", body: bytes, prepared=None) -> dict:
        data = _json_body(body)
        return {"folder": self.library.delete_folder(_require_str(data, "name"))}

    def _folder_enable(self, handler: "_Handler", body: bytes, prepared=None) -> dict:
        data = _json_body(body)
        name = _require_str(data, "name")
        enabled = data.get("enabled")
        if not isinstance(enabled, bool):
            raise ArtError(400, "The enabled flag must be true or false.")
        return {"name": name, "enabled": self.library.set_folder_enabled(name, enabled)}

    def _settings(self, handler: "_Handler", body: bytes, prepared=None) -> dict:
        data = _json_body(body)
        minutes = data.get("rotate_minutes")
        if not isinstance(minutes, int) or isinstance(minutes, bool):
            raise ArtError(400, "That is not one of the allowed rotation intervals.")
        return {"rotate_minutes": self.library.set_interval(minutes)}

    def _command(self, handler: "_Handler", body: bytes, prepared=None) -> dict:
        data = _json_body(body)
        action = data.get("action")
        if not isinstance(action, str):
            raise ArtError(400, "That is not a valid rotation command.")
        piece = data.get("piece")
        if piece is not None and not isinstance(piece, str):
            raise ArtError(400, "That is not a valid piece id.")
        # ``frame`` scopes the command to one frame: a known frame id, or absent
        # (null) for every frame. A malformed id is a 400 and an unknown frame a
        # 404, so a command never names an arbitrary frame (Review Focus 5).
        frame = data.get("frame")
        if frame is not None:
            self.frame.require_frame(frame)
        return {"command": self.library.command(action, piece, frame)}

    # -- frame control endpoints ------------------------------------------- #

    def _frame_settings(self, handler: "_Handler", body: bytes, prepared=None) -> dict:
        # The subset was parsed, its frame validated, and any weather-city query
        # geocoded in _prepare, outside the write lock; here, under the lock, it is
        # only merged and stored, so no network call runs while the lock is held. A
        # shared write (no frame) returns the full settings; a per-frame write
        # returns that frame's stored quiet hours.
        frame = prepared["frame"]
        result = self.frame.update_settings(prepared["subset"], frame=frame)
        if frame is None:
            return {"settings": result}
        return {"frame": frame, "quiet_hours": result}

    def _frame_pin(self, handler: "_Handler", body: bytes, prepared=None) -> dict:
        data = _json_body(body)
        frame = _require_str(data, "frame")
        what = _require_str(data, "what")
        duration = _require_str(data, "duration")
        return {"pin": self.frame.pin(frame, what, duration)}

    def _frame_unpin(self, handler: "_Handler", body: bytes, prepared=None) -> dict:
        data = _json_body(body)
        frame = _require_str(data, "frame")
        self.frame.unpin(frame)
        return {"pin": None}


# --------------------------------------------------------------------------- #
# Request parsing helpers
# --------------------------------------------------------------------------- #


def _content_length(raw: Optional[str]) -> int:
    if not raw:
        return 0
    try:
        value = int(raw)
    except ValueError:
        raise ArtError(400, "That request had a bad length.")
    return max(value, 0)


def _json_body(body: bytes) -> dict:
    try:
        data = json.loads(body.decode("utf-8")) if body else {}
    except (ValueError, UnicodeDecodeError):
        raise ArtError(400, "That request body was not valid JSON.")
    if not isinstance(data, dict):
        raise ArtError(400, "That request body was not a JSON object.")
    return data


def _require_str(data: dict, key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value:
        raise ArtError(400, "A required field was missing.")
    return value


def _reject_reserved_name(name: str) -> None:
    """Refuse a folder name that is the built-in folder in any case.

    The scan reserves the exact spelling ``Collection``; a user folder named
    ``collection`` in any other case would clash with it, so create and rename
    refuse every case variant with a 409 rather than letting one be made.
    """
    if name.strip().casefold() == _RESERVED_FOLDER:
        raise ArtError(409, "A folder named Collection already exists.")


def _drain(rfile, length: int) -> None:
    """Read and discard up to ``length`` bytes, in bounded chunks."""
    remaining = length
    while remaining > 0:
        chunk = rfile.read(min(remaining, 65536))
        if not chunk:
            break
        remaining -= len(chunk)


# --------------------------------------------------------------------------- #
# The HTTP server
# --------------------------------------------------------------------------- #


class _Handler(BaseHTTPRequestHandler):
    """Delegates every request to the app on the server; serves only the API."""

    protocol_version = "HTTP/1.1"
    server_version = "snaphouse-artadmin"

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler naming
        self.server.app.handle_post(self)

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler naming
        # The service serves only the write API; nginx serves the static
        # documents. A GET to an API route is the wrong method (405); anything
        # else is a 404.
        path = urllib.parse.urlsplit(self.path).path
        if path == _API_PREFIX.rstrip("/") or path.startswith(_API_PREFIX):
            self.send_api_error(
                HTTPStatus.METHOD_NOT_ALLOWED, "Use POST for this endpoint."
            )
        else:
            self.send_api_error(HTTPStatus.NOT_FOUND, "Unknown endpoint.")

    def send_api_json(self, status: HTTPStatus, payload, *, close: bool = False) -> None:
        body = json.dumps(payload).encode("utf-8")
        if close:
            self.close_connection = True
        self.send_response(int(status))
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if int(status) == int(HTTPStatus.METHOD_NOT_ALLOWED):
            self.send_header("Allow", "POST")
        self.end_headers()
        self.wfile.write(body)

    def send_api_error(self, status: HTTPStatus, message: str, *, close: bool = False) -> None:
        self.send_api_json(status, {"error": message}, close=close)

    def log_message(self, format: str, *args) -> None:  # noqa: A002 - signature
        # Route request logging through the service logger (quiet by default)
        # rather than straight to stderr, so journald shows one coherent stream.
        logging.getLogger("snaphouse.artadmin").debug(
            "%s - %s", self.address_string(), format % args
        )


def make_server(app: ArtAdmin, host: str, port: int) -> ThreadingHTTPServer:
    """Build a threaded HTTP server bound to ``host:port`` serving ``app``.

    The app is attached to the server so each handler reaches it. Pass ``0`` as
    the port for an ephemeral port (the tests do this); production binds
    ``127.0.0.1:8781``. Both public documents are published once here, before the
    server starts serving, so a frame polling at boot gets a real index rather
    than a missing file.
    """
    server = ThreadingHTTPServer((host, port), _Handler)
    server.daemon_threads = True
    server.app = app  # type: ignore[attr-defined]
    app.publish()
    return server


def _atomic_write_json(target: Path, payload) -> None:
    """Write ``payload`` as pretty JSON to ``target`` atomically (0644).

    A reader (nginx, the frame, the page) never sees a half-written document:
    either the old one or the complete new one. The file is world-readable so
    nginx can serve it; it sits in this service's own subdirectory, never the
    root-owned public web root.
    """
    target = Path(target)
    directory = target.parent
    directory.mkdir(parents=True, exist_ok=True)
    data = json.dumps(payload, indent=2).encode("utf-8")
    fd, tmp_name = tempfile.mkstemp(dir=directory, prefix=".art.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
            os.fchmod(handle.fileno(), 0o644)
        os.replace(tmp_name, target)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="The hub's art admin service (loopback HTTP API)."
    )
    parser.add_argument("--root", default="/srv/snaphouse/art")
    parser.add_argument("--public-root", default="/var/lib/snaphouse/public")
    parser.add_argument("--state", default="/var/lib/snaphouse/art-control.json")
    parser.add_argument(
        "--frame-state", default="/var/lib/snaphouse/artadmin/frame-control.json"
    )
    # The inventory frame ids (repeatable), one per frame host. The published
    # frame-control.json lists exactly these, each with its own quiet hours and
    # pin. The service reads each frame's own republished art history (written by
    # the house-status timer under /frames/<id>/) for that frame's art pin.
    parser.add_argument("--frame", action="append", dest="frames", metavar="ID")
    # The frame the pre-per-frame state migrates into: the old single top-level pin
    # becomes this frame's on first load under the new shape.
    parser.add_argument("--legacy-frame", default=DEFAULT_LEGACY_FRAME)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    log = logging.getLogger("snaphouse.artadmin")

    public_root = Path(args.public_root)
    library = Library(
        root=Path(args.root),
        thumbs=public_root / "art-thumbs",
        state=Path(args.state),
    )
    # The frame list comes from the inventory (one --frame per frame host); fall
    # back to the single original frame if none was passed. Each frame reads its
    # own republished art history under /frames/<id>/art-history.json for its art
    # pin. The geocoder fetch is injected so a weather-city write resolves a typed
    # place through Open-Meteo's keyless geocoding API. Only the city name leaves
    # the LAN; the module holds the URL, the selection, and the error handling.
    frame_ids = args.frames if args.frames else [DEFAULT_LEGACY_FRAME]
    art_history = {
        fid: public_root / "frames" / fid / "art-history.json" for fid in frame_ids
    }
    frame = FrameControl(
        Path(args.frame_state),
        frames=frame_ids,
        legacy_frame=args.legacy_frame,
        art_history=art_history,
        geocode=urllib_geocode,
    )
    app = ArtAdmin(
        library,
        index_path=public_root / "art" / "art-index.json",
        control_path=public_root / "art" / "art-control.json",
        frame=frame,
        frame_control_path=public_root / "art" / "frame-control.json",
        log=log,
    )
    # make_server publishes both documents (metadata-only, fast) before the
    # server starts serving, so a frame polling at boot gets a real index before
    # any request arrives, even while the thumbnails are still being generated.
    server = make_server(app, args.host, args.port)
    maintainer = threading.Thread(
        target=app.maintain_forever, name="art-maintain", daemon=True
    )
    maintainer.start()

    log.info("art admin listening on %s:%s", args.host, args.port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:  # pragma: no cover - signal path
        pass
    finally:
        app.stop()
        server.shutdown()
        server.server_close()
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
