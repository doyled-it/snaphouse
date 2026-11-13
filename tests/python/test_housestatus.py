"""Tests for snaphouse.housestatus: the hub-side health aggregator.

Android Chrome cannot reliably resolve .local mDNS names, so the room-picker
page must not fetch each machine's status.json from the browser. Instead the hub
polls every machine over the LAN on a 60-second timer and writes one same-origin
file, house-status.json, that the page reads. These tests hold the aggregator to
the shape the page expects: one entry per machine in order, a null status for a
machine that could not be reached, an ISO checked_at, and an atomic write.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from snaphouse import housestatus
from snaphouse.housestatus import (
    aggregate,
    main,
    publish_art_history,
    publish_frame_image,
    publish_frame_meta,
    publish_frames,
    select_art_history,
    select_frame_meta,
    select_status,
    write_art_history,
    write_house_status,
)
from snaphouse.housestatus import _load_config

NOW = datetime(2026, 11, 6, 21, 15, 0, tzinfo=timezone(-timedelta(hours=8)))

# The machine hostnames are the current (post-rename) names; the frame is
# labelled distinctly so it does not collide with the bedroom machine.
TARGETS = [
    {"name": "house", "url": "http://house.local/status.json"},
    {"name": "office", "url": "http://office.local/status.json"},
    {"name": "nightstand", "url": "http://nightstand.local/status.json"},
    {"name": "nightstand-frame", "url": "http://nightstand.local/nightstand/status.json"},
]


def _status(host):
    return {
        "host": host,
        "repo": "snaphouse",
        "commit": "abc",
        "main_commit": "abc",
        "last_good": "abc",
        "verify": "pass",
        "checked_at": "2026-11-06T21:14:00-08:00",
    }


def test_aggregate_maps_every_target_in_order():
    fetched = {t["url"]: _status(t["name"]) for t in TARGETS}
    doc = aggregate(TARGETS, lambda url: fetched[url], lambda: NOW)
    assert [m["name"] for m in doc["machines"]] == [
        "house",
        "office",
        "nightstand",
        "nightstand-frame",
    ]
    assert doc["machines"][0]["status"]["host"] == "house"


def test_aggregate_records_an_unreachable_machine_as_null():
    def fetch(url):
        if url == TARGETS[1]["url"]:
            return None
        return _status("x")

    doc = aggregate(TARGETS, fetch, lambda: NOW)
    by_name = {m["name"]: m["status"] for m in doc["machines"]}
    assert by_name["office"] is None
    assert by_name["house"] is not None


def test_aggregate_treats_a_fetch_error_as_unreachable():
    def fetch(url):
        raise RuntimeError("boom")

    doc = aggregate(TARGETS, fetch, lambda: NOW)
    assert all(m["status"] is None for m in doc["machines"])


def test_aggregate_carries_the_installed_flag_and_defaults_to_installed():
    targets = [
        {"name": "house", "url": "http://house.local/status.json"},
        {"name": "panel", "url": "http://panel.local/status.json", "installed": False},
    ]
    doc = aggregate(targets, lambda url: None, lambda: NOW)
    by_name = {m["name"]: m for m in doc["machines"]}
    assert by_name["house"]["installed"] is True
    assert by_name["panel"]["installed"] is False


def test_aggregate_stamps_checked_at_from_now():
    doc = aggregate(TARGETS, lambda url: None, lambda: NOW)
    assert doc["checked_at"] == NOW.isoformat()


def test_select_status_keeps_only_the_pinned_fields():
    parsed = {
        "host": "office",
        "repo": "snaphouse",
        "commit": "abc",
        "main_commit": "abc",
        "last_good": "abc",
        "verify": "pass",
        "checked_at": "2026-11-06T21:14:00-08:00",
        # Not part of the pinned schema; must not be republished.
        "secret": "do not copy",
        "script": "<img onerror=...>",
    }
    selected = select_status(parsed)
    assert set(selected) == {
        "host",
        "repo",
        "commit",
        "main_commit",
        "last_good",
        "verify",
        "checked_at",
    }
    assert "secret" not in selected
    assert "script" not in selected


def test_select_status_rejects_non_objects_and_empty_bodies():
    assert select_status("not a dict") is None
    assert select_status(["a", "list"]) is None
    assert select_status({"unrelated": 1}) is None


def test_write_house_status_is_atomic_json_readable_by_nginx(tmp_path):
    out = tmp_path / "house-status.json"
    doc = aggregate(TARGETS, lambda url: None, lambda: NOW)
    write_house_status(out, doc)
    # No stray temp file is left behind.
    assert [p.name for p in tmp_path.iterdir()] == ["house-status.json"]
    reread = json.loads(out.read_text())
    assert reread == doc
    # world-readable so nginx (www-data) can serve it.
    assert (out.stat().st_mode & 0o777) == 0o644


# The hub also republishes the frame's art-history.json same-origin. It is
# fetched from the nightstand host with the same hardening as the status fetch
# (no redirects, size cap, short timeout), and only the pinned contract fields
# are kept, so a malformed or hostile body cannot be served through the hub. The
# shape is in docs/cross-repo-features.md.


def _piece(**overrides):
    piece = {
        "file": "aic-12345.jpg",
        "title": "A Still Life",
        "artist": "A Painter",
        "source_url": "https://example.org/artworks/12345",
        "shown_at": "2026-11-06T21:14:00-08:00",
    }
    piece.update(overrides)
    return piece


def test_select_art_history_keeps_current_and_history_fields():
    current = _piece()
    older = _piece(file="aic-9.jpg", title="Older", shown_at="2026-11-05T09:00:00-08:00")
    selected = select_art_history({"current": current, "history": [current, older]})
    assert set(selected) == {"current", "history"}
    assert set(selected["current"]) == {"file", "title", "artist", "source_url", "shown_at", "id"}
    assert [p["file"] for p in selected["history"]] == ["aic-12345.jpg", "aic-9.jpg"]


def test_select_art_history_keeps_a_safe_piece_id():
    # Newer entries carry an id (the piece id relative to the art folder) that the
    # page deletes by; it may be one folder deep. A safe id whose last segment is
    # the entry's file is passed through.
    selected = select_art_history(
        {
            "current": _piece(file="beach.jpg", id="Photos/beach.jpg"),
            "history": [_piece(id="aic-12345.jpg")],
        }
    )
    assert selected["current"]["id"] == "Photos/beach.jpg"
    assert selected["history"][0]["id"] == "aic-12345.jpg"


def test_select_art_history_nulls_a_missing_or_unsafe_piece_id():
    # Older entries have no id (the page falls back to file); a traversal or a
    # two-folder-deep id is rejected rather than republished.
    selected = select_art_history(
        {
            "current": _piece(id="../secret.jpg"),
            "history": [_piece(), _piece(id="a/b/c.jpg"), _piece(id=".hidden.jpg")],
        }
    )
    assert selected["current"]["id"] is None
    assert selected["history"][0]["id"] is None
    assert selected["history"][1]["id"] is None
    assert selected["history"][2]["id"] is None


def test_select_art_history_drops_a_collection_prefixed_id():
    # A piece id whose first segment is the reserved Collection folder is resolved
    # back to the top level by the hub's scan, so deleting by it would hit the
    # wrong file; reject it (case-insensitive) but keep the entry via `file`.
    selected = select_art_history(
        {
            "current": _piece(file="x.jpg", id="Collection/x.jpg"),
            "history": [_piece(file="x.jpg", id="collection/x.jpg")],
        }
    )
    assert selected["current"]["id"] is None
    assert selected["current"]["file"] == "x.jpg"
    assert selected["history"][0]["id"] is None


def test_select_art_history_drops_an_id_whose_last_segment_is_not_the_file():
    # The id must name the same file as the entry, or a delete by id would act on
    # a different piece than the one shown.
    selected = select_art_history(
        {
            "current": _piece(file="a.jpg", id="Photos/b.jpg"),
            "history": [_piece(file="a.jpg", id="b.jpg")],
        }
    )
    assert selected["current"]["id"] is None
    assert selected["current"]["file"] == "a.jpg"
    assert selected["history"][0]["id"] is None


def test_select_art_history_strips_fields_outside_the_contract():
    current = _piece(secret="do not copy", script="<img onerror=...>")
    selected = select_art_history({"current": current, "history": [current]})
    assert "secret" not in selected["current"]
    assert "script" not in selected["current"]
    assert "secret" not in selected["history"][0]


def test_select_art_history_rejects_non_objects_and_a_missing_history():
    assert select_art_history("not a dict") is None
    assert select_art_history(["a", "list"]) is None
    assert select_art_history({"current": None}) is None


def test_select_art_history_allows_an_empty_but_well_formed_body():
    selected = select_art_history({"current": None, "history": []})
    assert selected == {"current": None, "history": []}


def test_select_art_history_drops_malformed_pieces_and_a_bad_current():
    selected = select_art_history(
        {
            "current": {"title": "no file"},
            "history": [
                _piece(),
                {"file": "x.jpg"},  # no title
                {"file": "y.jpg", "title": "no stamp"},  # no shown_at
                {"file": "../secret.jpg", "title": "escape", "shown_at": "2026-11-01T00:00:00-08:00"},
                7,
            ],
        }
    )
    assert selected["current"] is None
    assert [p["file"] for p in selected["history"]] == ["aic-12345.jpg"]


def test_select_art_history_caps_history_at_fifty():
    history = [_piece(file=f"aic-{i}.jpg") for i in range(80)]
    selected = select_art_history({"current": history[0], "history": history})
    assert len(selected["history"]) == 50
    assert selected["history"][0]["file"] == "aic-0.jpg"


def test_select_art_history_drops_a_non_https_source_url_but_keeps_the_piece():
    selected = select_art_history(
        {
            "current": _piece(source_url="http://insecure.example/obj"),
            "history": [_piece(source_url="javascript:alert(1)")],
        }
    )
    assert selected["current"]["source_url"] is None
    assert selected["current"]["file"] == "aic-12345.jpg"
    assert selected["history"][0]["source_url"] is None


def test_select_art_history_coerces_a_bad_artist_to_null():
    selected = select_art_history({"current": _piece(artist=7), "history": [_piece(artist="")]})
    assert selected["current"]["artist"] is None
    assert selected["history"][0]["artist"] is None


def test_write_art_history_is_atomic_json_readable_by_nginx(tmp_path):
    out = tmp_path / "art-history.json"
    doc = {"current": _piece(), "history": [_piece()]}
    write_art_history(out, doc)
    assert [p.name for p in tmp_path.iterdir()] == ["art-history.json"]
    assert json.loads(out.read_text()) == doc
    assert (out.stat().st_mode & 0o777) == 0o644


# The art-history republish is best-effort: the brief requires that a failed or
# malformed fetch, or a failed write, leaves the last good copy byte-identical and
# never crashes the status aggregation. A failed write is also surfaced once to
# stderr (journald) rather than swallowed silently.


class _FakeResponse:
    """A minimal urllib response for driving http_fetch in a test."""

    def __init__(self, body: bytes, status: int = 200):
        self._body = body
        self.status = status

    def read(self, limit: int = -1) -> bytes:
        return self._body if limit < 0 else self._body[:limit]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeOpener:
    """Stands in for the module opener; hands back one canned response."""

    def __init__(self, response: _FakeResponse):
        self._response = response

    def open(self, request, timeout=None):
        return self._response


def test_http_fetch_art_history_rejects_malformed_json(monkeypatch):
    monkeypatch.setattr(housestatus, "_OPENER", _FakeOpener(_FakeResponse(b"{not json")))
    assert (
        housestatus.http_fetch("http://x/art-history.json", select=select_art_history)
        is None
    )


def test_http_fetch_art_history_enforces_the_size_cap(monkeypatch):
    oversize = b'{"current":null,"history":[]}' + b" " * (housestatus.MAX_STATUS_BYTES + 1)
    monkeypatch.setattr(housestatus, "_OPENER", _FakeOpener(_FakeResponse(oversize)))
    assert (
        housestatus.http_fetch("http://x/art-history.json", select=select_art_history)
        is None
    )


def test_publish_art_history_keeps_last_good_when_the_fetch_yields_none(tmp_path, monkeypatch):
    # A failed or malformed fetch reaches publish as None (http_fetch collapses
    # both). The existing published file must be left exactly as it was.
    out = tmp_path / "art-history.json"
    seeded = b'{"current": null, "history": []}\n'
    out.write_bytes(seeded)
    monkeypatch.setattr(housestatus, "http_fetch", lambda url, *, timeout=0, select=None: None)
    publish_art_history("http://x/art-history.json", out)
    assert out.read_bytes() == seeded


def test_publish_art_history_logs_and_keeps_last_good_when_the_write_fails(
    tmp_path, monkeypatch, capsys
):
    out = tmp_path / "art-history.json"
    seeded = b'{"current": null, "history": []}\n'
    out.write_bytes(seeded)
    monkeypatch.setattr(
        housestatus,
        "http_fetch",
        lambda url, *, timeout=0, select=None: {"current": None, "history": []},
    )

    def _boom(path, document):
        raise OSError("disk full")

    monkeypatch.setattr(housestatus, "write_art_history", _boom)
    # Must not raise, and must leave the last good copy untouched.
    publish_art_history("http://x/art-history.json", out)
    assert out.read_bytes() == seeded
    err = capsys.readouterr().err
    assert "art-history" in err
    assert "disk full" in err


def _write_targets(tmp_path):
    targets = tmp_path / "targets.json"
    targets.write_text(json.dumps([{"name": "house", "url": "http://house.local/status.json"}]))
    return targets


def test_main_completes_the_status_poll_when_the_art_step_fails(tmp_path, monkeypatch):
    # The art fetch raising must not touch the exit code, the house-status write,
    # or a pre-seeded art-history.json.
    house_out = tmp_path / "house-status.json"
    art_out = tmp_path / "art-history.json"
    seeded = b'{"current": null, "history": []}\n'
    art_out.write_bytes(seeded)

    def fake_fetch(url, *, timeout=0, select=select_status):
        if select is select_art_history:
            raise RuntimeError("frame unreachable in a surprising way")
        if select is select_frame_meta:
            return None
        return _status("house")

    monkeypatch.setattr(housestatus, "http_fetch", fake_fetch)
    monkeypatch.setattr(housestatus, "fetch_frame_image", lambda url, *, timeout=0: None)

    code = main(
        [
            "--targets",
            str(_write_targets(tmp_path)),
            "--out",
            str(house_out),
            "--art-history-out",
            str(art_out),
            "--frame-image-out",
            str(tmp_path / "frame.png"),
            "--frame-meta-out",
            str(tmp_path / "frame.json"),
        ]
    )
    assert code == 0
    # The primary poll finished and wrote its file.
    house_doc = json.loads(house_out.read_text())
    assert house_doc["machines"][0]["status"]["host"] == "house"
    # The last good art history is untouched.
    assert art_out.read_bytes() == seeded


def test_main_republishes_art_history_on_a_good_fetch(tmp_path, monkeypatch):
    house_out = tmp_path / "house-status.json"
    art_out = tmp_path / "art-history.json"

    def fake_fetch(url, *, timeout=0, select=select_status):
        if select is select_art_history:
            return {"current": _piece(), "history": [_piece()]}
        if select is select_frame_meta:
            return None
        return _status("house")

    monkeypatch.setattr(housestatus, "http_fetch", fake_fetch)
    monkeypatch.setattr(housestatus, "fetch_frame_image", lambda url, *, timeout=0: None)

    code = main(
        [
            "--targets",
            str(_write_targets(tmp_path)),
            "--out",
            str(house_out),
            "--art-history-out",
            str(art_out),
            "--frame-image-out",
            str(tmp_path / "frame.png"),
            "--frame-meta-out",
            str(tmp_path / "frame.json"),
        ]
    )
    assert code == 0
    art_doc = json.loads(art_out.read_text())
    assert art_doc["current"]["file"] == "aic-12345.jpg"
    assert (art_out.stat().st_mode & 0o777) == 0o644


# The hub also republishes the nightstand frame mirror same-origin: frame.png (the
# exact image last pushed to the panel) and frame.json ({updated_at, screen}). Both
# use the art-history hardening: no redirects, a size cap, a shape or signature
# check, an atomic write, and the last good copy kept on any failure. The shapes
# are the "Frame mirror" contract in docs/cross-repo-features.md.

_FRAME_META = {"updated_at": "2026-11-09T07:00:00-08:00", "screen": "art"}


def test_select_frame_meta_keeps_updated_at_and_screen():
    assert select_frame_meta(dict(_FRAME_META)) == _FRAME_META


def test_select_frame_meta_strips_fields_outside_the_contract():
    body = dict(_FRAME_META)
    body["secret"] = "do not copy"
    body["script"] = "<img onerror=...>"
    assert select_frame_meta(body) == _FRAME_META


def test_select_frame_meta_rejects_malformed_bodies():
    assert select_frame_meta("not a dict") is None
    assert select_frame_meta(["a", "list"]) is None
    assert select_frame_meta({"screen": "art"}) is None  # no updated_at
    assert select_frame_meta({"updated_at": "t"}) is None  # no screen
    assert select_frame_meta({"updated_at": "t", "screen": ""}) is None  # empty screen


def test_fetch_frame_image_accepts_a_png(monkeypatch):
    body = housestatus.PNG_SIGNATURE + b"pixels"
    monkeypatch.setattr(housestatus, "_OPENER", _FakeOpener(_FakeResponse(body)))
    assert housestatus.fetch_frame_image("http://x/frame.png") == body


def test_fetch_frame_image_rejects_a_non_png(monkeypatch):
    monkeypatch.setattr(housestatus, "_OPENER", _FakeOpener(_FakeResponse(b"<html>not a png</html>")))
    assert housestatus.fetch_frame_image("http://x/frame.png") is None


def test_fetch_frame_image_enforces_the_size_cap(monkeypatch):
    oversize = housestatus.PNG_SIGNATURE + b"x" * (housestatus.MAX_FRAME_IMAGE_BYTES + 1)
    monkeypatch.setattr(housestatus, "_OPENER", _FakeOpener(_FakeResponse(oversize)))
    assert housestatus.fetch_frame_image("http://x/frame.png") is None


def test_fetch_frame_image_rejects_a_non_200(monkeypatch):
    body = housestatus.PNG_SIGNATURE + b"pixels"
    monkeypatch.setattr(housestatus, "_OPENER", _FakeOpener(_FakeResponse(body, status=404)))
    assert housestatus.fetch_frame_image("http://x/frame.png") is None


def test_publish_frame_image_writes_a_valid_png_atomically(tmp_path, monkeypatch):
    out = tmp_path / "frame.png"
    body = housestatus.PNG_SIGNATURE + b"new-frame"
    monkeypatch.setattr(housestatus, "fetch_frame_image", lambda url, *, timeout=0: body)
    publish_frame_image("http://x/frame.png", out)
    assert out.read_bytes() == body
    assert [p.name for p in tmp_path.iterdir()] == ["frame.png"]
    assert (out.stat().st_mode & 0o777) == 0o644


def test_publish_frame_image_keeps_last_good_when_the_fetch_yields_none(tmp_path, monkeypatch):
    out = tmp_path / "frame.png"
    seeded = housestatus.PNG_SIGNATURE + b"old-good-frame"
    out.write_bytes(seeded)
    monkeypatch.setattr(housestatus, "fetch_frame_image", lambda url, *, timeout=0: None)
    publish_frame_image("http://x/frame.png", out)
    assert out.read_bytes() == seeded


def test_publish_frame_image_logs_and_keeps_last_good_when_the_write_fails(
    tmp_path, monkeypatch, capsys
):
    out = tmp_path / "frame.png"
    seeded = housestatus.PNG_SIGNATURE + b"old"
    out.write_bytes(seeded)
    monkeypatch.setattr(
        housestatus, "fetch_frame_image", lambda url, *, timeout=0: housestatus.PNG_SIGNATURE + b"new"
    )

    def _boom(path, data):
        raise OSError("disk full")

    monkeypatch.setattr(housestatus, "write_frame_image", _boom)
    publish_frame_image("http://x/frame.png", out)
    assert out.read_bytes() == seeded
    err = capsys.readouterr().err
    assert "frame.png" in err
    assert "disk full" in err


def test_publish_frame_meta_writes_validated_fields(tmp_path, monkeypatch):
    out = tmp_path / "frame.json"
    monkeypatch.setattr(
        housestatus, "http_fetch", lambda url, *, timeout=0, select=None: dict(_FRAME_META)
    )
    publish_frame_meta("http://x/frame.json", out)
    assert json.loads(out.read_text()) == _FRAME_META
    assert [p.name for p in tmp_path.iterdir()] == ["frame.json"]
    assert (out.stat().st_mode & 0o777) == 0o644


def test_publish_frame_meta_keeps_last_good_when_the_fetch_yields_none(tmp_path, monkeypatch):
    out = tmp_path / "frame.json"
    seeded = b'{"updated_at": "t", "screen": "art"}\n'
    out.write_bytes(seeded)
    monkeypatch.setattr(housestatus, "http_fetch", lambda url, *, timeout=0, select=None: None)
    publish_frame_meta("http://x/frame.json", out)
    assert out.read_bytes() == seeded


def test_main_republishes_the_frame_mirror(tmp_path, monkeypatch):
    house_out = tmp_path / "house-status.json"
    frame_png = tmp_path / "frame.png"
    frame_json = tmp_path / "frame.json"
    png_body = housestatus.PNG_SIGNATURE + b"mirror"

    def fake_fetch(url, *, timeout=0, select=select_status):
        if select is select_art_history:
            return {"current": None, "history": []}
        if select is select_frame_meta:
            return dict(_FRAME_META)
        return _status("house")

    monkeypatch.setattr(housestatus, "http_fetch", fake_fetch)
    monkeypatch.setattr(housestatus, "fetch_frame_image", lambda url, *, timeout=0: png_body)

    code = main(
        [
            "--targets",
            str(_write_targets(tmp_path)),
            "--out",
            str(house_out),
            "--art-history-out",
            str(tmp_path / "art-history.json"),
            "--frame-image-out",
            str(frame_png),
            "--frame-meta-out",
            str(frame_json),
        ]
    )
    assert code == 0
    assert frame_png.read_bytes() == png_body
    assert json.loads(frame_json.read_text()) == _FRAME_META


def test_main_completes_the_status_poll_when_the_frame_step_fails(tmp_path, monkeypatch):
    house_out = tmp_path / "house-status.json"
    frame_png = tmp_path / "frame.png"
    seeded = housestatus.PNG_SIGNATURE + b"old"
    frame_png.write_bytes(seeded)

    def fake_fetch(url, *, timeout=0, select=select_status):
        if select is select_frame_meta:
            raise RuntimeError("frame unreachable in a surprising way")
        if select is select_art_history:
            return None
        return _status("house")

    def boom_image(url, *, timeout=0):
        raise RuntimeError("frame png unreachable in a surprising way")

    monkeypatch.setattr(housestatus, "http_fetch", fake_fetch)
    monkeypatch.setattr(housestatus, "fetch_frame_image", boom_image)

    code = main(
        [
            "--targets",
            str(_write_targets(tmp_path)),
            "--out",
            str(house_out),
            "--art-history-out",
            str(tmp_path / "art-history.json"),
            "--frame-image-out",
            str(frame_png),
            "--frame-meta-out",
            str(tmp_path / "frame.json"),
        ]
    )
    assert code == 0
    assert json.loads(house_out.read_text())["machines"][0]["status"]["host"] == "house"
    # The last good mirror image is untouched.
    assert frame_png.read_bytes() == seeded


# The hub now republishes several frames, each by its inventory id, into
# <frames root>/<id>/{art-history.json, frame.png, frame.json}. The old single
# paths keep serving the nightstand's copy for this release (tested above); these
# cover the per-frame fan-out: every frame's three files, an id validated before
# any path is built (Review Focus 5), and one unreachable or not-yet-installed
# frame skipped without disturbing the others or the poll (Review Focus 1).


def _frame_fetchers(have_data):
    """Fake http_fetch and fetch_frame_image keyed on the frame id in the URL.

    `have_data` is the set of frame ids whose fetches succeed; any other id's
    fetches yield None (unreachable or not yet installed).
    """

    def fake_http_fetch(url, *, timeout=0, select=select_status):
        frame_id = url.split("/frames-src/", 1)[1].split("/", 1)[0]
        if frame_id not in have_data:
            return None
        if select is select_art_history:
            return {"current": _piece(), "history": [_piece()]}
        if select is select_frame_meta:
            return dict(_FRAME_META)
        return None

    def fake_fetch_image(url, *, timeout=0):
        frame_id = url.split("/frames-src/", 1)[1].split("/", 1)[0]
        if frame_id not in have_data:
            return None
        return housestatus.PNG_SIGNATURE + b"mirror-" + frame_id.encode()

    return fake_http_fetch, fake_fetch_image


def _frame(frame_id):
    # The source base the hub fetches from; a /frames-src/<id>/ path the fakes key
    # on (the real base is http://<id>.local/nightstand/).
    return {"id": frame_id, "base": f"http://{frame_id}.local/frames-src/{frame_id}/"}


def test_publish_frames_republishes_each_frame_by_id(tmp_path, monkeypatch):
    root = tmp_path / "frames"
    http, image = _frame_fetchers(have_data={"nightstand", "office"})
    monkeypatch.setattr(housestatus, "http_fetch", http)
    monkeypatch.setattr(housestatus, "fetch_frame_image", image)

    publish_frames([_frame("nightstand"), _frame("office")], root)

    for frame_id in ("nightstand", "office"):
        art = json.loads((root / frame_id / "art-history.json").read_text())
        assert art["current"]["file"] == "aic-12345.jpg"
        assert (root / frame_id / "frame.png").read_bytes().startswith(housestatus.PNG_SIGNATURE)
        assert json.loads((root / frame_id / "frame.json").read_text()) == _FRAME_META
        # world-readable so nginx can serve it.
        assert ((root / frame_id / "frame.json").stat().st_mode & 0o777) == 0o644


def test_publish_frames_skips_an_unreachable_frame_and_still_publishes_others(
    tmp_path, monkeypatch
):
    # Review Focus 1: the office before its Inky arrives 404s every file. It is
    # skipped (no files written) and the nightstand is published normally.
    root = tmp_path / "frames"
    http, image = _frame_fetchers(have_data={"nightstand"})
    monkeypatch.setattr(housestatus, "http_fetch", http)
    monkeypatch.setattr(housestatus, "fetch_frame_image", image)

    publish_frames([_frame("nightstand"), _frame("office")], root)

    assert (root / "nightstand" / "frame.png").exists()
    # The unreachable frame left no files behind (an empty dir or no dir are both
    # fine; what matters is nginx 404s the files so the page shows it offline).
    office = root / "office"
    assert not office.exists() or not any(office.iterdir())


def test_publish_frames_does_not_raise_when_one_frame_fetch_errors(tmp_path, monkeypatch):
    root = tmp_path / "frames"

    def boom_http(url, *, timeout=0, select=select_status):
        if "office" in url:
            raise RuntimeError("surprising failure")
        if select is select_art_history:
            return {"current": _piece(), "history": [_piece()]}
        if select is select_frame_meta:
            return dict(_FRAME_META)
        return None

    monkeypatch.setattr(housestatus, "http_fetch", boom_http)
    monkeypatch.setattr(housestatus, "fetch_frame_image", lambda url, *, timeout=0: None)

    # A raising fetch for one frame must not stop the loop or escape.
    publish_frames([_frame("office"), _frame("nightstand")], root)
    assert (root / "nightstand" / "art-history.json").exists()


def test_publish_frames_rejects_an_invalid_frame_id(tmp_path, monkeypatch):
    # Review Focus 5: a malformed id (traversal, path separator, uppercase, too
    # long) is rejected before any path is built, so nothing is ever written
    # outside the frames root.
    root = tmp_path / "frames"
    root.mkdir()
    called = []
    monkeypatch.setattr(
        housestatus,
        "http_fetch",
        lambda url, *, timeout=0, select=None: called.append(url) or None,
    )
    monkeypatch.setattr(
        housestatus, "fetch_frame_image", lambda url, *, timeout=0: called.append(url) or None
    )

    bad = [
        {"id": "../evil", "base": "http://x.local/nightstand/"},
        {"id": "a/b", "base": "http://x.local/nightstand/"},
        {"id": "Office", "base": "http://x.local/nightstand/"},
        {"id": "9lead", "base": "http://x.local/nightstand/"},
        {"id": "x" * 40, "base": "http://x.local/nightstand/"},
        {"id": "ok\n", "base": "http://x.local/nightstand/"},
        {"id": "ok", "base": "ftp://x.local/nightstand/"},
        {"id": "ok", "base": 7},
        "not a dict",
    ]
    publish_frames(bad, root)

    # No fetch was attempted for any rejected frame, and nothing escaped the root.
    assert called == []
    assert not (tmp_path / "evil").exists()
    assert list(root.iterdir()) == []


def test_load_config_accepts_a_bare_list_as_targets_with_no_frames(tmp_path):
    path = tmp_path / "targets.json"
    path.write_text(json.dumps([{"name": "house", "url": "http://house.local/status.json"}]))
    targets, frames = _load_config(path)
    assert targets == [{"name": "house", "url": "http://house.local/status.json"}]
    assert frames == []


def test_load_config_reads_targets_and_frames_from_an_object(tmp_path):
    path = tmp_path / "targets.json"
    path.write_text(
        json.dumps(
            {
                "targets": [{"name": "house", "url": "http://house.local/status.json"}],
                "frames": [{"id": "nightstand", "base": "http://nightstand.local/nightstand/"}],
            }
        )
    )
    targets, frames = _load_config(path)
    assert targets[0]["name"] == "house"
    assert frames[0]["id"] == "nightstand"


def test_main_republishes_every_frame(tmp_path, monkeypatch):
    house_out = tmp_path / "house-status.json"
    frames_root = tmp_path / "frames"
    targets = tmp_path / "targets.json"
    targets.write_text(
        json.dumps(
            {
                "targets": [{"name": "house", "url": "http://house.local/status.json"}],
                "frames": [_frame("nightstand"), _frame("office")],
            }
        )
    )
    http, image = _frame_fetchers(have_data={"nightstand", "office"})

    def fake_http(url, *, timeout=0, select=select_status):
        if "/frames-src/" in url:
            return http(url, timeout=timeout, select=select)
        if select is select_art_history or select is select_frame_meta:
            return None
        return _status("house")

    monkeypatch.setattr(housestatus, "http_fetch", fake_http)
    monkeypatch.setattr(
        housestatus,
        "fetch_frame_image",
        lambda url, *, timeout=0: image(url) if "/frames-src/" in url else None,
    )

    code = main(
        [
            "--targets",
            str(targets),
            "--out",
            str(house_out),
            "--art-history-out",
            str(tmp_path / "art-history.json"),
            "--frame-image-out",
            str(tmp_path / "frame.png"),
            "--frame-meta-out",
            str(tmp_path / "frame.json"),
            "--frames-root",
            str(frames_root),
        ]
    )
    assert code == 0
    assert json.loads(house_out.read_text())["machines"][0]["status"]["host"] == "house"
    for frame_id in ("nightstand", "office"):
        assert (frames_root / frame_id / "frame.png").exists()
