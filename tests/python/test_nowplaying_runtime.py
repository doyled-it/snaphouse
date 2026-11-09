"""Tests for the now-playing runtime: the service around the pure Tracker.

The runtime follows snapserver over its TCP control port, folds notifications
into the Tracker, resolves art on a background worker, and writes
``now-playing.json`` and ``play-history.json`` atomically. These tests drive it
against a fake snapserver (a real localhost listener in the test) so the socket
loop, the connect-time ``Server.GetStatus``, reconnection, and the file writes
are all exercised without a real snapserver and without the network. The art
lookup is a fake, so no test touches the catalogues.
"""

from __future__ import annotations

import json
import logging
import socket
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from snaphouse.nowplaying import Service, Tracker, load_history

TZ = timezone(timedelta(hours=-8))
_EPOCH = datetime(2026, 11, 8, 20, 0, 0, tzinfo=TZ)

LOG = logging.getLogger("test.nowplaying")

# The service writes the two JSON files into this subdirectory of the public
# root (owned by snapserver on the hub), so it never needs to own the shared
# public web root.
STATE = "nowplaying"


def _now_factory():
    """A monotonically advancing timezone-aware clock for the service."""
    counter = {"n": 0}

    def now():
        counter["n"] += 1
        return _EPOCH + timedelta(seconds=counter["n"])

    return now


class FakeArtLookup:
    """An ArtLookup stand-in: records calls, returns a preset URL (or None)."""

    def __init__(self, url=None):
        self.url = url
        self.calls = []

    def find(self, title, artist, album, kind, genre):
        self.calls.append((title, artist, album, kind, genre))
        return self.url


class FakeSnapserver:
    """A localhost listener that plays the part of snapserver's control port."""

    def __init__(self):
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(5)
        self._sock.settimeout(5.0)
        self.host, self.port = self._sock.getsockname()
        self.accepted = 0

    def accept(self):
        conn, _ = self._sock.accept()
        conn.settimeout(5.0)
        self.accepted += 1
        return _Conn(conn)

    def close(self):
        self._sock.close()


class _Conn:
    def __init__(self, conn):
        self._conn = conn
        self._buf = b""

    def read_message(self) -> dict:
        while b"\n" not in self._buf:
            chunk = self._conn.recv(65536)
            if not chunk:
                raise AssertionError("connection closed before a full message")
            self._buf += chunk
        line, self._buf = self._buf.split(b"\n", 1)
        return json.loads(line)

    def send(self, obj: dict) -> None:
        self._conn.sendall((json.dumps(obj) + "\n").encode("utf-8"))

    def close(self) -> None:
        self._conn.close()


def _service(server: FakeSnapserver, public_root: Path, *, art_lookup=None, history=None):
    tracker = Tracker("House", history or [])
    return Service(
        host=server.host,
        port=server.port,
        public_root=public_root,
        house_stream="House",
        tracker=tracker,
        art_lookup=art_lookup or FakeArtLookup(),
        now=_now_factory(),
        log=LOG,
        tick_interval=0.02,
        socket_timeout=0.02,
        backoff_start=0.01,
        backoff_cap=0.05,
        worker_poll=0.02,
    )


def _run(service: Service) -> threading.Thread:
    thread = threading.Thread(target=service.run, daemon=True)
    thread.start()
    return thread


def _wait_for(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def _house_props(**kwargs):
    metadata = {"title": kwargs.get("title", "Song")}
    if "artist" in kwargs:
        metadata["artist"] = [kwargs["artist"]]
    if "album" in kwargs:
        metadata["album"] = kwargs["album"]
    if "duration" in kwargs:
        metadata["duration"] = kwargs["duration"]
    props = {"playbackStatus": kwargs.get("status", "playing"), "metadata": metadata}
    if "position" in kwargs:
        props["position"] = kwargs["position"]
    return props


def _server_object(house_props, *, house_status="playing"):
    return {
        "streams": [
            {"id": "AirPlay", "status": "playing", "properties": {}},
            {"id": "Bluetooth", "status": "idle", "properties": {}},
            {"id": "House", "status": house_status, "properties": house_props},
        ],
        "groups": [
            {
                "stream_id": "House",
                "muted": False,
                "clients": [
                    {
                        "connected": True,
                        "config": {"name": "Office", "volume": {"muted": False}},
                        "host": {"name": "Office"},
                    }
                ],
            }
        ],
    }


def test_connect_sends_get_status(tmp_path):
    server = FakeSnapserver()
    service = _service(server, tmp_path)
    thread = _run(service)
    try:
        conn = server.accept()
        request = conn.read_message()
        assert request["method"] == "Server.GetStatus"
        assert request["jsonrpc"] == "2.0"
        assert "id" in request
    finally:
        service.stop()
        thread.join(timeout=5)
        server.close()


def test_on_properties_writes_now_playing(tmp_path):
    server = FakeSnapserver()
    service = _service(server, tmp_path)
    thread = _run(service)
    out = tmp_path / STATE / "now-playing.json"
    try:
        conn = server.accept()
        conn.read_message()  # the connect-time Server.GetStatus
        # A House status that is playing (so the stream is not idle), then a
        # fresh properties notification carrying the track.
        conn.send({"id": 1, "jsonrpc": "2.0", "result": {"server": _server_object({})}})
        conn.send(
            {
                "jsonrpc": "2.0",
                "method": "Stream.OnProperties",
                "params": {
                    "id": "House",
                    "properties": _house_props(
                        title="Real Song", artist="A Band", album="A Record",
                        position=12.0, duration=200.0,
                    ),
                },
            }
        )

        assert _wait_for(
            lambda: out.is_file() and json.loads(out.read_text()).get("track")
        )
        doc = json.loads(out.read_text())
        assert doc["track"]["title"] == "Real Song"
        assert doc["track"]["artist"] == "A Band"
        assert doc["track"]["status"] == "playing"
        assert doc["track"]["position"] == 12.0
        assert doc["rooms"] == ["Office"]
    finally:
        service.stop()
        thread.join(timeout=5)
        server.close()


def test_input_properties_trigger_a_status_refresh(tmp_path):
    # A Stream.OnProperties for an input stream (not House) must make the service
    # re-request Server.GetStatus, so House's forwarded properties are picked up
    # even if snapserver only notifies on the input.
    server = FakeSnapserver()
    service = _service(server, tmp_path)
    thread = _run(service)
    try:
        conn = server.accept()
        conn.read_message()  # connect-time GetStatus
        conn.send(
            {
                "jsonrpc": "2.0",
                "method": "Stream.OnProperties",
                "params": {"id": "AirPlay", "properties": _house_props(title="X")},
            }
        )
        # The service should ask for status again.
        refresh = conn.read_message()
        assert refresh["method"] == "Server.GetStatus"
    finally:
        service.stop()
        thread.join(timeout=5)
        server.close()


def test_reconnects_after_a_dropped_connection(tmp_path):
    server = FakeSnapserver()
    service = _service(server, tmp_path)
    thread = _run(service)
    try:
        conn = server.accept()
        assert conn.read_message()["method"] == "Server.GetStatus"
        conn.close()  # drop the connection

        # The service reconnects and sends Server.GetStatus again.
        conn2 = server.accept()
        assert conn2.read_message()["method"] == "Server.GetStatus"
        assert server.accepted >= 2
    finally:
        service.stop()
        thread.join(timeout=5)
        server.close()


def test_background_art_lookup_fills_cover(tmp_path):
    server = FakeSnapserver()
    art = FakeArtLookup(url="/cover/lookup/abc.jpg")
    service = _service(server, tmp_path, art_lookup=art)
    thread = _run(service)
    out = tmp_path / STATE / "now-playing.json"
    try:
        conn = server.accept()
        conn.read_message()
        conn.send({"id": 1, "jsonrpc": "2.0", "result": {"server": _server_object({})}})
        conn.send(
            {
                "jsonrpc": "2.0",
                "method": "Stream.OnProperties",
                "params": {
                    "id": "House",
                    "properties": _house_props(
                        title="Coverless", artist="Band", album="Record"
                    ),
                },
            }
        )

        assert _wait_for(
            lambda: out.is_file()
            and (json.loads(out.read_text()).get("track") or {}).get("art_url")
            == "/cover/lookup/abc.jpg"
        )
        assert art.calls
        assert art.calls[0][0] == "Coverless"
    finally:
        service.stop()
        thread.join(timeout=5)
        server.close()


def test_drain_art_results_wires_miss_and_found(tmp_path):
    # The runtime hop the retry depends on: _drain_art_results must route a
    # worker result with no cover to art_missing (scheduling a retry) and a
    # found cover to set_art (stopping retries). Deleting the else branch that
    # calls art_missing would leave this test red.
    clock = {"t": 0.0}
    tracker = Tracker("House", [], clock=lambda: clock["t"])
    service = Service(
        host="localhost",
        port=0,
        public_root=tmp_path,
        house_stream="House",
        tracker=tracker,
        art_lookup=FakeArtLookup(),
        now=_now_factory(),
        log=LOG,
    )
    key = ("A", "Ar", "Al")
    tracker.on_properties("House", _house_props(title="A", artist="Ar", album="Al"), _EPOCH)
    # A request is in flight (handed out once; suppressed while pending).
    assert tracker.wants_art() == (key, "music", None)
    assert tracker.wants_art() is None

    # A worker result with no cover: the retry is scheduled and fires only after
    # the back-off.
    service._art_results.put((key, "music", None, None))
    service._drain_art_results()
    assert tracker.wants_art() is None  # inside the 2-minute back-off
    clock["t"] = 120.0
    assert tracker.wants_art() == (key, "music", None)  # retried after the back-off

    # A worker result with a cover: it lands and retries stop.
    service._art_results.put((key, "music", None, "/cover/lookup/x.jpg"))
    service._drain_art_results()
    assert tracker.now_playing_doc(_EPOCH)["track"]["art_url"] == "/cover/lookup/x.jpg"
    clock["t"] = 1_000_000.0
    assert tracker.wants_art() is None  # a cover landed; no further lookups


def test_files_exist_even_before_any_snapserver_data(tmp_path):
    # The service writes both files at startup so verify never finds them
    # missing, even if snapserver has not answered yet.
    server = FakeSnapserver()
    service = _service(server, tmp_path)
    thread = _run(service)
    try:
        assert _wait_for(lambda: (tmp_path / STATE / "now-playing.json").is_file())
        assert _wait_for(lambda: (tmp_path / STATE / "play-history.json").is_file())
        now_playing = json.loads((tmp_path / STATE / "now-playing.json").read_text())
        assert "track" in now_playing
        history = json.loads((tmp_path / STATE / "play-history.json").read_text())
        assert history == {"plays": []}
        # No partial temp files are left behind by the atomic writes.
        assert not list((tmp_path / STATE).glob("*.tmp"))
        assert not list((tmp_path / STATE).glob(".*.tmp"))
    finally:
        service.stop()
        thread.join(timeout=5)
        server.close()


def test_load_history_missing_is_empty(tmp_path):
    assert load_history(tmp_path / "nope.json", LOG) == []


def test_load_history_corrupt_starts_empty(tmp_path, caplog):
    path = tmp_path / "play-history.json"
    path.write_text("{ this is not json")
    with caplog.at_level(logging.WARNING):
        assert load_history(path, LOG) == []
    assert any("corrupt" in record.message for record in caplog.records)


def test_load_history_reads_valid_plays(tmp_path):
    path = tmp_path / "play-history.json"
    plays = [{"title": "A", "artist": "B", "album": "C"}]
    path.write_text(json.dumps({"plays": plays}))
    assert load_history(path, LOG) == plays
