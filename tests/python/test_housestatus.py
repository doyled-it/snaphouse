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

from snaphouse.housestatus import (
    aggregate,
    select_art_history,
    select_status,
    write_art_history,
    write_house_status,
)

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
    assert set(selected["current"]) == {"file", "title", "artist", "source_url", "shown_at"}
    assert [p["file"] for p in selected["history"]] == ["aic-12345.jpg", "aic-9.jpg"]


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
