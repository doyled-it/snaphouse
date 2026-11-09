"""Tests for nowplaying.py, the hub's now-playing tracker (pure logic).

The tracker folds snapserver state (``Server.GetStatus`` server objects and
``Stream.OnProperties`` notifications) into the two published shapes:
``now-playing.json`` and ``play-history.json``. These tests pin that reduction
with no sockets and no files; Task 4 adds the runtime around it.

Position carries Task 1 semantics: every properties update that carries
``position`` already holds the extrapolated value at the moment of emit, so the
tracker anchors ``(position, arrival time)`` on each one without any stale-value
filtering.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from snaphouse.nowplaying import Tracker, classify, rewrite_art_url

TZ = timezone(timedelta(hours=-8))
_EPOCH = datetime(2026, 11, 8, 20, 0, 0, tzinfo=TZ)


def dt(seconds: float = 0.0) -> datetime:
    """A timezone-aware time ``seconds`` past a fixed epoch."""
    return _EPOCH + timedelta(seconds=seconds)


def props(
    *,
    title="Song",
    artist="Artist",
    album="Album",
    status="playing",
    position=None,
    duration=None,
    genre=None,
    art=None,
):
    """A House stream ``properties`` object as Task 1's bridges emit it."""
    metadata = {}
    if title is not None:
        metadata["title"] = title
    if artist is not None:
        metadata["artist"] = [artist]
    if album is not None:
        metadata["album"] = album
    if duration is not None:
        metadata["duration"] = duration
    if genre is not None:
        metadata["genre"] = genre
    if art is not None:
        metadata["artUrl"] = art
    out = {"playbackStatus": status}
    if position is not None:
        out["position"] = position
    if metadata:
        out["metadata"] = metadata
    return out


def server(
    stream_props,
    *,
    house_status="playing",
    airplay="playing",
    bluetooth="idle",
    rooms=("Bedroom", "Office"),
    house_stream="House",
):
    """A ``Server.GetStatus`` server object with the House stream and rooms."""
    streams = [
        {"id": "AirPlay", "status": airplay, "properties": {}},
        {"id": "Bluetooth", "status": bluetooth, "properties": {}},
        {"id": house_stream, "status": house_status, "properties": stream_props},
    ]
    clients = [
        {
            "connected": True,
            "config": {"name": name, "volume": {"muted": False}},
            "host": {"name": name},
        }
        for name in rooms
    ]
    groups = [{"stream_id": house_stream, "muted": False, "clients": list(clients)}]
    return {"streams": streams, "groups": groups}


def test_classify():
    assert classify(1200, None) == "long_form"
    assert classify(1199, None) == "music"
    assert classify(None, ["Podcast"]) == "long_form"
    assert classify(4000, ["Electronic"]) == "long_form"  # kind by duration
    assert classify(None, None) == "music"
    # Genre match is case-insensitive and covers audiobook and spoken word.
    assert classify(300, ["audiobook"]) == "long_form"
    assert classify(300, ["Spoken Word"]) == "long_form"
    assert classify(300, ["Rock"]) == "music"


def test_rewrite_art_url():
    assert rewrite_art_url(None) is None
    assert rewrite_art_url("/cover/a.jpg") == "/cover/a.jpg"
    assert rewrite_art_url("/cover/lookup/b.jpg") == "/cover/lookup/b.jpg"
    assert rewrite_art_url("http://house.local/cover/a.jpg") == "/cover/a.jpg"
    assert rewrite_art_url("http://anyhost/cover/lookup/b.jpg") == "/cover/lookup/b.jpg"
    assert rewrite_art_url("https://example.com/image.jpg") is None
    assert rewrite_art_url("") is None


def test_doc_for_playing_track():
    tracker = Tracker("House", [])
    tracker.on_server(
        server(
            props(
                title="Title",
                artist="Artist",
                album="Album",
                status="playing",
                position=100.0,
                duration=300.0,
                genre=["Pop"],
                art="http://house.local/cover/x.jpg",
            ),
            rooms=("Bedroom", "Office"),
        ),
        dt(0),
    )
    doc = tracker.now_playing_doc(dt(0))
    assert doc["updated_at"] == dt(0).isoformat()
    assert doc["rooms"] == ["Bedroom", "Office"]
    track = doc["track"]
    assert track == {
        "title": "Title",
        "artist": "Artist",
        "album": "Album",
        "kind": "music",
        "genre": ["Pop"],
        "source": "airplay",
        "status": "playing",
        "duration": 300.0,
        "position": 100.0,
        "position_at": dt(0).isoformat(),
        "started_at": dt(0).isoformat(),
        "art_url": "/cover/x.jpg",
    }
    assert tracker.now_playing_changed is True


def test_genre_published_and_persisted_in_history():
    # The genre the tracker already parses is published on the now-playing track
    # and persisted on the history play, so a consumer can route by it.
    tracker = Tracker("House", [], count_after_s=30)
    tracker.on_server(
        server(
            props(
                title="Chapter 1",
                artist="A Narrator",
                album="A Book",
                genre=["Audiobook"],
                position=0.0,
            )
        ),
        dt(0),
    )
    track = tracker.now_playing_doc(dt(0))["track"]
    assert track["genre"] == ["Audiobook"]
    assert track["kind"] == "long_form"
    tracker.tick(dt(30))
    play = tracker.history_doc()["plays"][0]
    assert play["genre"] == ["Audiobook"]
    assert play["title"] == "Chapter 1"


def test_track_without_genre_publishes_null():
    tracker = Tracker("House", [])
    tracker.on_server(server(props(title="No Genre", genre=None)), dt(0))
    assert tracker.now_playing_doc(dt(0))["track"]["genre"] is None


def test_older_history_without_genre_stays_valid():
    # An entry written before the genre field has no genre key; it is additive,
    # so loading and re-emitting such history leaves the entry untouched.
    seed = [
        {
            "title": "Old",
            "artist": None,
            "album": None,
            "kind": "music",
            "source": "airplay",
            "duration": None,
            "started_at": dt(-1000).isoformat(),
            "art_url": None,
        }
    ]
    tracker = Tracker("House", seed)
    play = tracker.history_doc()["plays"][0]
    assert "genre" not in play
    assert play["title"] == "Old"


def test_source_follows_playing_input():
    tracker = Tracker("House", [])
    tracker.on_server(
        server(props(), airplay="idle", bluetooth="playing"),
        dt(0),
    )
    assert tracker.now_playing_doc(dt(0))["track"]["source"] == "bluetooth"


def test_track_without_artist_or_album():
    tracker = Tracker("House", [])
    tracker.on_server(
        server(props(title="Radio Stream", artist=None, album=None)),
        dt(0),
    )
    track = tracker.now_playing_doc(dt(0))["track"]
    assert track is not None
    assert track["title"] == "Radio Stream"
    assert track["artist"] is None
    assert track["album"] is None


def test_pause_holds_then_clears():
    tracker = Tracker("House", [], hold_s=600)
    tracker.on_server(server(props(status="playing", position=0.0)), dt(0))
    tracker.on_properties("House", props(status="paused", position=5.0), dt(10))
    tracker.tick(dt(10 + 599))
    held = tracker.now_playing_doc(dt(10 + 599))["track"]
    assert held is not None
    assert held["status"] == "paused"
    tracker.tick(dt(10 + 600))
    assert tracker.now_playing_doc(dt(10 + 600))["track"] is None


def test_play_counted_after_30s_once():
    tracker = Tracker("House", [], count_after_s=30)
    tracker.on_server(server(props(title="X", position=0.0)), dt(0))
    tracker.tick(dt(29))
    assert tracker.history_doc()["plays"] == []
    tracker.tick(dt(30))
    plays = tracker.history_doc()["plays"]
    assert len(plays) == 1
    assert plays[0]["title"] == "X"
    assert plays[0]["started_at"] == dt(0).isoformat()
    assert tracker.history_changed is True
    tracker.tick(dt(300))
    assert len(tracker.history_doc()["plays"]) == 1


def test_skip_under_30s_not_counted():
    tracker = Tracker("House", [], count_after_s=30)
    tracker.on_server(server(props(title="A", position=0.0)), dt(0))
    tracker.tick(dt(10))
    tracker.on_server(server(props(title="B", position=0.0)), dt(10))
    tracker.tick(dt(45))
    titles = [play["title"] for play in tracker.history_doc()["plays"]]
    assert "A" not in titles
    assert titles == ["B"]


def test_resume_after_long_pause_same_play():
    tracker = Tracker("House", [], count_after_s=30, hold_s=600)
    tracker.on_server(
        server(props(title="A", duration=300.0, position=0.0)), dt(0)
    )
    tracker.tick(dt(30))
    assert len(tracker.history_doc()["plays"]) == 1
    # Pause, then let the 10 minute hold clear the track.
    tracker.on_properties(
        "House", props(title="A", duration=300.0, status="paused", position=30.0), dt(31)
    )
    tracker.tick(dt(31 + 600))
    assert tracker.now_playing_doc(dt(31 + 600))["track"] is None
    # Resume the same track within duration + 600 s of its start.
    tracker.on_server(
        server(props(title="A", duration=300.0, position=40.0)), dt(700)
    )
    tracker.tick(dt(700 + 30))
    plays = tracker.history_doc()["plays"]
    assert len(plays) == 1
    assert plays[0]["started_at"] == dt(0).isoformat()
    assert tracker.now_playing_doc(dt(700 + 30))["track"]["started_at"] == dt(0).isoformat()


def test_history_trimmed_to_50():
    seed = [
        {
            "title": f"seed{i}",
            "artist": None,
            "album": None,
            "kind": "music",
            "source": "airplay",
            "duration": None,
            "started_at": dt(-(i + 1) * 1000).isoformat(),
            "art_url": None,
        }
        for i in range(50)
    ]
    tracker = Tracker("House", seed, count_after_s=30, max_plays=50)
    tracker.on_server(server(props(title="NEW", position=0.0)), dt(0))
    tracker.tick(dt(30))
    plays = tracker.history_doc()["plays"]
    assert len(plays) == 50
    assert plays[0]["title"] == "NEW"
    assert all(play["title"] != "seed49" for play in plays)


def test_restart_mid_track_keeps_started_at():
    started = dt(-100)
    seed = [
        {
            "title": "A",
            "artist": "Ar",
            "album": "Al",
            "kind": "music",
            "source": "airplay",
            "duration": 300.0,
            "started_at": started.isoformat(),
            "art_url": None,
        }
    ]
    tracker = Tracker("House", seed, count_after_s=30)
    tracker.on_server(
        server(props(title="A", artist="Ar", album="Al", duration=300.0, position=100.0)),
        dt(0),
    )
    assert tracker.now_playing_doc(dt(0))["track"]["started_at"] == started.isoformat()
    tracker.tick(dt(30))
    assert len(tracker.history_doc()["plays"]) == 1


def test_stale_position_resend_does_not_reanchor_backward():
    tracker = Tracker("House", [])
    tracker.on_server(
        server(props(title="A", position=100.0, duration=300.0)), dt(0)
    )
    # 50 s later an unrelated resend (art arriving). Task 1 freezes position to
    # the emit-time value, so it carries 150, not the stale 100.
    tracker.on_properties(
        "House",
        props(title="A", position=150.0, duration=300.0, art="http://house.local/cover/x.jpg"),
        dt(50),
    )
    track = tracker.now_playing_doc(dt(50))["track"]
    assert track["position"] == 150.0
    assert track["position_at"] == dt(50).isoformat()


def test_get_status_does_not_reanchor_an_existing_position():
    # Snapserver returns the House properties exactly as last emitted; its
    # position is not re-extrapolated to now. A GetStatus refresh (fired by a
    # volume or mute change) must not re-anchor that stale value, or the bar jumps
    # backward (Review Focus 1).
    tracker = Tracker("House", [])
    tracker.on_properties("House", props(position=100.0, duration=300.0), dt(0))
    track = tracker.now_playing_doc(dt(0))["track"]
    assert track["position"] == 100.0
    assert track["position_at"] == dt(0).isoformat()

    # 300 s later a snapshot carries the same, now-stale, position 100.
    tracker.on_server(server(props(position=100.0, duration=300.0)), dt(300))
    track = tracker.now_playing_doc(dt(300))["track"]
    # The anchor is untouched, so a consumer still computes 100 + 300 = 400.
    assert track["position"] == 100.0
    assert track["position_at"] == dt(0).isoformat()


def test_get_status_seeds_the_first_anchor():
    # When the service's first information is a GetStatus snapshot (start or just
    # after a reconnect), there is no anchor yet, so the snapshot's position seeds
    # one.
    tracker = Tracker("House", [])
    tracker.on_server(server(props(position=50.0, duration=300.0)), dt(0))
    track = tracker.now_playing_doc(dt(0))["track"]
    assert track["position"] == 50.0
    assert track["position_at"] == dt(0).isoformat()


def test_get_status_anchors_position_for_a_new_key():
    # A snapshot that brings a different track than the one anchored carries that
    # new track's own position, and there is no anchor for it yet, so it is
    # anchored.
    tracker = Tracker("House", [])
    tracker.on_properties("House", props(title="A", position=100.0), dt(0))
    tracker.on_server(server(props(title="B", position=10.0)), dt(300))
    track = tracker.now_playing_doc(dt(300))["track"]
    assert track["title"] == "B"
    assert track["position"] == 10.0
    assert track["position_at"] == dt(300).isoformat()


def test_wants_art_once_per_key():
    tracker = Tracker("House", [])
    tracker.on_server(
        server(props(title="A", artist="Ar", album="Al", genre=["Podcast"])),
        dt(0),
    )
    want = tracker.wants_art()
    assert want == (("A", "Ar", "Al"), "long_form", ["Podcast"])
    assert tracker.wants_art() is None
    tracker.set_art(("A", "Ar", "Al"), "/cover/lookup/x.jpg")
    assert tracker.wants_art() is None
    assert tracker.now_playing_doc(dt(0))["track"]["art_url"] == "/cover/lookup/x.jpg"


def test_rewrite_art_url_rejects_protocol_relative():
    # A protocol-relative "//host/..." URL starts with "/", but a browser
    # resolves it to another origin, so it must not pass through as a same-origin
    # path. It is rejected like any other foreign host.
    assert rewrite_art_url("//evil.example/cover/x.jpg") is None
    assert rewrite_art_url("//evil.example/cover/lookup/x.jpg") is None
    assert rewrite_art_url("//evil.example/other.jpg") is None
    # A genuine same-origin path is still kept.
    assert rewrite_art_url("/cover/x.jpg") == "/cover/x.jpg"


def test_wants_art_again_after_key_change():
    # A no-art track that replays must ask for art again: the per-key suppression
    # clears when the current key changes, so ArtLookup (answering from its disk
    # cache) is consulted again rather than the cover staying missing forever.
    tracker = Tracker("House", [])
    tracker.on_server(server(props(title="A", artist="Ar", album="Al")), dt(0))
    assert tracker.wants_art() == (("A", "Ar", "Al"), "music", None)
    # Suppressed while the same track stays current.
    assert tracker.wants_art() is None

    tracker.on_properties("House", props(title="B", artist="Br", album="Bl"), dt(5))
    assert tracker.wants_art() == (("B", "Br", "Bl"), "music", None)

    # The same no-art track comes back; it is asked for again.
    tracker.on_properties("House", props(title="A", artist="Ar", album="Al"), dt(10))
    assert tracker.wants_art() == (("A", "Ar", "Al"), "music", None)
    assert tracker.wants_art() is None


def test_wants_art_none_when_art_present():
    tracker = Tracker("House", [])
    tracker.on_server(
        server(props(title="A", art="http://house.local/cover/x.jpg")), dt(0)
    )
    assert tracker.wants_art() is None


def test_history_art_through_keep_art():
    calls = []

    def keep(url):
        calls.append(url)
        return f"/cover/history/{url.rsplit('/', 1)[-1]}" if url else url

    tracker = Tracker("House", [], count_after_s=30, keep_art=keep)
    tracker.on_server(
        server(props(title="A", artist="Ar", album="Al")), dt(0)
    )
    tracker.tick(dt(30))
    # Recorded before art lands: keep_art(None) stays None.
    assert tracker.history_doc()["plays"][0]["art_url"] is None
    tracker.set_art(("A", "Ar", "Al"), "/cover/lookup/x.jpg")
    assert tracker.history_doc()["plays"][0]["art_url"] == "/cover/history/x.jpg"
    assert "/cover/lookup/x.jpg" in calls
    # The now-playing doc keeps the raw cover path, not the history copy.
    assert tracker.now_playing_doc(dt(30))["track"]["art_url"] == "/cover/lookup/x.jpg"


def test_record_time_art_through_keep_art():
    def keep(url):
        return f"/cover/history/{url.rsplit('/', 1)[-1]}" if url else url

    tracker = Tracker("House", [], count_after_s=30, keep_art=keep)
    tracker.on_server(
        server(props(title="B", art="http://house.local/cover/y.jpg")), dt(0)
    )
    tracker.tick(dt(30))
    play = tracker.history_doc()["plays"][0]
    assert play["art_url"] == "/cover/history/y.jpg"


def test_empty_rooms_still_published():
    tracker = Tracker("House", [])
    tracker.on_server(server(props(title="A"), rooms=()), dt(0))
    doc = tracker.now_playing_doc(dt(0))
    assert doc["rooms"] == []
    assert doc["track"] is not None


def test_wants_art_again_when_metadata_fills_in():
    # The live bug: the phone's first AVRCP update for a Bluetooth podcast
    # episode carries title, artist, and album but no duration or genre, so the
    # one lookup goes out as "music" (the wrong route) and finds nothing. A
    # later update adds the duration and a Podcast genre; the track still has no
    # cover, so a second request must go out on the corrected route, and the
    # cover then lands without a service restart.
    key = ("Ep 5", "The Show", "The Show")
    tracker = Tracker("House", [])
    tracker.on_properties(
        "House",
        props(title="Ep 5", artist="The Show", album="The Show"),
        dt(0),
    )
    # First sight: no duration, no genre, so the route is music.
    assert tracker.wants_art() == (key, "music", None)
    # Suppressed while nothing about the route has changed.
    assert tracker.wants_art() is None
    # That lookup came back empty (wrong route).
    tracker.art_missing(key)

    # Later metadata arrives: a duration and a Podcast genre.
    tracker.on_properties(
        "House",
        props(title="Ep 5", artist="The Show", album="The Show", duration=1800, genre=["Podcast"]),
        dt(1),
    )
    # The route changed, and the track still has no cover, so a fresh request
    # goes out on the podcast route.
    assert tracker.wants_art() == (key, "long_form", ["Podcast"])
    assert tracker.wants_art() is None

    tracker.set_art(key, "/cover/lookup/show.jpg")
    assert tracker.now_playing_doc(dt(1))["track"]["art_url"] == "/cover/lookup/show.jpg"
    assert tracker.wants_art() is None


def test_looked_up_cover_survives_same_track_updates():
    # A looked-up cover set by set_art must stay on screen. The source's later
    # updates for the same track (a position resend, a metadata resend) carry no
    # artUrl for a Bluetooth track, and must not wipe the resolved cover. A
    # genuinely new track clears it, and an update that does carry an artUrl
    # still replaces it.
    key = ("A", "Ar", "Al")
    tracker = Tracker("House", [])
    tracker.on_properties("House", props(title="A", artist="Ar", album="Al"), dt(0))
    tracker.set_art(key, "/cover/lookup/x.jpg")
    assert tracker.now_playing_doc(dt(0))["track"]["art_url"] == "/cover/lookup/x.jpg"

    # A position update for the same track (no artUrl) keeps the cover.
    tracker.on_properties(
        "House", props(title="A", artist="Ar", album="Al", position=12.0), dt(1)
    )
    assert tracker.now_playing_doc(dt(1))["track"]["art_url"] == "/cover/lookup/x.jpg"

    # A same-track metadata resend (a duration fills in, still no artUrl) keeps it.
    tracker.on_properties(
        "House", props(title="A", artist="Ar", album="Al", duration=200), dt(2)
    )
    assert tracker.now_playing_doc(dt(2))["track"]["art_url"] == "/cover/lookup/x.jpg"

    # A genuinely new track clears the cover.
    tracker.on_properties("House", props(title="B", artist="Br", album="Bl"), dt(3))
    assert tracker.now_playing_doc(dt(3))["track"]["art_url"] is None

    # An update that carries an artUrl still replaces the cover.
    tracker.on_properties(
        "House",
        props(title="B", artist="Br", album="Bl", art="http://house.local/cover/y.jpg"),
        dt(4),
    )
    assert tracker.now_playing_doc(dt(4))["track"]["art_url"] == "/cover/y.jpg"


def test_wants_art_retries_empty_lookup_after_backoff():
    # A lookup that returns None (a transient failure) is retried for the same
    # track after a back-off: 2 minutes, then 10 minutes, at most 3 attempts per
    # play. An injected clock drives the back-off without real waiting.
    clock = {"t": 0.0}
    key = ("A", "Ar", "Al")
    tracker = Tracker("House", [], clock=lambda: clock["t"])
    tracker.on_properties("House", props(title="A", artist="Ar", album="Al"), dt(0))

    # Attempt 1 at first sight.
    assert tracker.wants_art() == (key, "music", None)
    assert tracker.wants_art() is None  # pending, awaiting the result
    tracker.art_missing(key)
    assert tracker.wants_art() is None  # back-off not elapsed yet

    clock["t"] = 119.0
    assert tracker.wants_art() is None  # still inside the 2-minute wait
    clock["t"] = 120.0
    # Attempt 2 after the 2-minute back-off.
    assert tracker.wants_art() == (key, "music", None)
    assert tracker.wants_art() is None
    tracker.art_missing(key)

    clock["t"] = 120.0 + 599.0
    assert tracker.wants_art() is None  # inside the 10-minute wait
    clock["t"] = 120.0 + 600.0
    # Attempt 3 after the 10-minute back-off.
    assert tracker.wants_art() == (key, "music", None)
    tracker.art_missing(key)

    clock["t"] = 1_000_000.0
    # No fourth attempt: at most 3 per play.
    assert tracker.wants_art() is None


def test_wants_art_backoff_stops_once_a_cover_lands():
    clock = {"t": 0.0}
    key = ("A", "Ar", "Al")
    tracker = Tracker("House", [], clock=lambda: clock["t"])
    tracker.on_properties("House", props(title="A", artist="Ar", album="Al"), dt(0))
    assert tracker.wants_art() == (key, "music", None)
    tracker.art_missing(key)
    clock["t"] = 120.0
    # The retry recovers a cover.
    assert tracker.wants_art() == (key, "music", None)
    tracker.set_art(key, "/cover/lookup/x.jpg")
    clock["t"] = 1_000_000.0
    assert tracker.wants_art() is None  # a cover landed; no more retries


def test_art_attempt_budget_resets_on_a_new_play():
    clock = {"t": 0.0}
    tracker = Tracker("House", [], clock=lambda: clock["t"])
    a = ("A", "Ar", "Al")
    tracker.on_properties("House", props(title="A", artist="Ar", album="Al"), dt(0))
    assert tracker.wants_art() == (a, "music", None)
    tracker.art_missing(a)
    clock["t"] = 120.0
    assert tracker.wants_art() == (a, "music", None)
    tracker.art_missing(a)
    clock["t"] = 720.0
    assert tracker.wants_art() == (a, "music", None)
    tracker.art_missing(a)
    clock["t"] = 1_000_000.0
    assert tracker.wants_art() is None  # the budget is spent for this play

    # A new track resets the attempt budget.
    b = ("B", "Br", "Bl")
    tracker.on_properties("House", props(title="B", artist="Br", album="Bl"), dt(1))
    assert tracker.wants_art() == (b, "music", None)
