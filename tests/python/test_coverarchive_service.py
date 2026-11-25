"""The now-playing service feeds the cover collection (generated images only)."""

from __future__ import annotations

import json
from pathlib import Path

from snaphouse.coverarchive import CoverArchive
from snaphouse.nowplaying import Service, Tracker

from test_nowplaying_runtime import (
    STATE,
    FakeArtLookup,
    FakeSnapserver,
    LOG,
    _house_props,
    _now_factory,
    _run,
    _server_object,
    _wait_for,
)


def _jpeg(n: int) -> bytes:
    return b"\xff\xd8\xff\xe0" + bytes([n]) * 40 + b"\xff\xd9"


def _service(server, root: Path, art, history=None):
    archive = CoverArchive(root)
    service = Service(
        host=server.host,
        port=server.port,
        public_root=root,
        house_stream="House",
        tracker=Tracker("House", history or []),
        art_lookup=art,
        cover_archive=archive,
        now=_now_factory(),
        log=LOG,
        tick_interval=0.02,
        socket_timeout=0.02,
        backoff_start=0.01,
        backoff_cap=0.05,
        worker_poll=0.02,
    )
    return service


def _play(server, **props):
    conn = server.accept()
    conn.read_message()
    conn.send({"id": 1, "jsonrpc": "2.0", "result": {"server": _server_object({})}})
    conn.send(
        {
            "jsonrpc": "2.0",
            "method": "Stream.OnProperties",
            "params": {"id": "House", "properties": _house_props(**props)},
        }
    )
    return conn


def _archived(root: Path) -> list[dict]:
    path = root / STATE / "cover-archive.json"
    if not path.is_file():
        return []
    return json.loads(path.read_text())["covers"]


def test_a_found_music_cover_joins_the_collection(tmp_path):
    (tmp_path / "cover").mkdir(parents=True)
    (tmp_path / "cover" / "abc.jpg").write_bytes(_jpeg(1))
    server = FakeSnapserver()
    service = _service(server, tmp_path, FakeArtLookup(url="/cover/abc.jpg"))
    thread = _run(service)
    try:
        _play(server, title="Song", artist="Band", album="Record", duration=200)
        assert _wait_for(lambda: len(_archived(tmp_path)) == 1)
        cover = _archived(tmp_path)[0]
        assert (cover["album"], cover["artist"]) == ("Record", "Band")
    finally:
        service.stop()
        thread.join(timeout=5)
        server.close()


def test_a_podcast_cover_is_left_out(tmp_path):
    (tmp_path / "cover").mkdir(parents=True)
    (tmp_path / "cover" / "abc.jpg").write_bytes(_jpeg(1))
    server = FakeSnapserver()
    art = FakeArtLookup(url="/cover/abc.jpg")
    service = _service(server, tmp_path, art)
    thread = _run(service)
    try:
        _play(server, title="Ep 1", artist="Host", album="Show", duration=3000, genre=["Podcast"])
        assert _wait_for(lambda: bool(art.calls))
        assert _wait_for(
            lambda: (
                json.loads((tmp_path / STATE / "now-playing.json").read_text()).get("track") or {}
            ).get("art_url")
            == "/cover/abc.jpg"
        )
        import time

        time.sleep(0.3)
        assert _archived(tmp_path) == []
    finally:
        service.stop()
        thread.join(timeout=5)
        server.close()


def test_start_seeds_an_empty_collection_from_the_lookup_cache(tmp_path):
    (tmp_path / "cover" / "lookup").mkdir(parents=True)
    (tmp_path / "cover" / "lookup" / "a.jpg").write_bytes(_jpeg(1))
    server = FakeSnapserver()
    service = _service(server, tmp_path, FakeArtLookup())
    thread = _run(service)
    try:
        assert _wait_for(lambda: len(_archived(tmp_path)) == 1)
    finally:
        service.stop()
        thread.join(timeout=5)
        server.close()
