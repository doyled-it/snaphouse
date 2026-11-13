"""Tests for snaphouse.avrcp_meta, the Bluetooth AVRCP stream control script.

snapserver runs this script for the Bluetooth stream and talks to it over
stdin/stdout with newline-delimited JSON-RPC 2.0 (Snapcast's stream plugin API).
The script watches BlueZ `org.bluez.MediaPlayer1` and turns the phone's AVRCP
track and status into Snapcast stream properties, so a now-playing title and
artist reach `Server.GetStatus`.

Two things are pinned here:

1. `track_to_metadata` maps a BlueZ `Track` dict to Snapcast metadata, dropping
   missing keys and empty strings. The BlueZ-specific bits (D-Bus, the signal
   loop) are not exercised; they need real hardware and are confirmed on site.
2. The control-script handshake: the script announces itself with
   `Plugin.Stream.Ready`, answers `GetProperties`, and emits exactly one
   `Plugin.Stream.Player.Properties` notification per track change. A sender that
   gives no metadata still plays: its notification carries `playbackStatus` and
   no title (Review Focus 5).
"""

from __future__ import annotations

import io
import json
import threading
import time

import pytest

from snaphouse import avrcp_meta


class FakeClock:
    """A monotonic clock the tests advance by hand, to pin position at emit time."""

    def __init__(self, now: float = 0.0) -> None:
        self.t = float(now)

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


# A full BlueZ MediaPlayer1 Track property, the way BlueZ exposes it over D-Bus:
# string tags plus uint32 TrackNumber and uint32 Duration in milliseconds.
FULL_TRACK = {
    "Title": "Soul Town",
    "Artist": "Klaus Doldinger's Passport",
    "Album": "Doldinger",
    "TrackNumber": 6,
    "Duration": 305293,
}


def test_track_to_metadata_maps_a_full_track():
    meta = avrcp_meta.track_to_metadata(FULL_TRACK)

    assert meta["title"] == "Soul Town"
    # Snapcast's artist field is a list of strings, even for a single artist.
    assert meta["artist"] == ["Klaus Doldinger's Passport"]
    assert meta["album"] == "Doldinger"
    # Snapcast expects trackNumber as a number; a string makes snapserver log
    # "failed to read tag trackNumber: type must be number, but is string".
    assert meta["trackNumber"] == 6
    assert isinstance(meta["trackNumber"], int)
    # BlueZ Duration is milliseconds; Snapcast duration is seconds.
    assert meta["duration"] == 305.293


def test_genre_maps_to_list():
    # Snapcast's genre field is a list, even for a single genre.
    assert avrcp_meta.track_to_metadata({"Title": "t", "Genre": "Podcast"})["genre"] == ["Podcast"]


def test_position_anchor_extrapolates_and_freezes():
    # MediaPlayer1.Position arrives as uint32 ms; the serve loop anchors it and
    # extrapolates while playing, then freezes it on pause (Review Focus 1).
    clock = FakeClock(0.0)
    p = avrcp_meta.Player(clock=clock)
    out = io.StringIO()
    avrcp_meta.process_event(p, ("status", "playing"), out)
    avrcp_meta.process_event(p, ("position", 10000), out)  # 10 s
    clock.advance(5)
    assert p.properties()["position"] == pytest.approx(15.0)
    avrcp_meta.process_event(p, ("status", "paused"), out)
    clock.advance(100)
    assert p.properties()["position"] == pytest.approx(15.0)


def test_new_track_without_position_drops_the_old_anchor():
    # Ruling R4: a new track (different identity) with no accompanying Position
    # must drop the previous track's position anchor rather than extrapolate it.
    clock = FakeClock(0.0)
    p = avrcp_meta.Player(clock=clock)
    out = io.StringIO()
    avrcp_meta.process_event(p, ("track", {"Title": "A"}), out)
    avrcp_meta.process_event(p, ("status", "playing"), out)
    avrcp_meta.process_event(p, ("position", 10000), out)  # 10 s, at t=0
    clock.advance(100)
    avrcp_meta.process_event(p, ("track", {"Title": "B"}), out)  # new track, no Position
    assert "position" not in p.properties()


def test_position_just_before_a_new_track_is_kept():
    # Ruling R4: a Position that arrives within the keep window before a new
    # Track is kept as the new track's position (BlueZ reports them close).
    clock = FakeClock(0.0)
    p = avrcp_meta.Player(clock=clock)
    out = io.StringIO()
    avrcp_meta.process_event(p, ("track", {"Title": "A"}), out)
    avrcp_meta.process_event(p, ("status", "playing"), out)
    avrcp_meta.process_event(p, ("position", 10000), out)  # 10 s, at t=0
    clock.advance(1)
    avrcp_meta.process_event(p, ("track", {"Title": "B"}), out)  # 1 s later, kept
    assert p.properties()["position"] == pytest.approx(11.0)  # 10 s anchor + 1 s played


def test_track_to_metadata_omits_missing_keys_and_empty_strings():
    # Only a title and an artist are tagged; Album is present but empty, which
    # BlueZ does for an untagged field, and must be dropped rather than sent as "".
    meta = avrcp_meta.track_to_metadata(
        {"Title": "Untitled", "Artist": "Nobody", "Album": ""}
    )

    assert meta == {"title": "Untitled", "artist": ["Nobody"]}


def test_track_to_metadata_on_an_empty_track_is_empty():
    assert avrcp_meta.track_to_metadata({}) == {}


def test_status_to_playback_status_maps_bluez_values():
    assert avrcp_meta.status_to_playback_status("playing") == "playing"
    assert avrcp_meta.status_to_playback_status("paused") == "paused"
    assert avrcp_meta.status_to_playback_status("stopped") == "stopped"
    # BlueZ reports a seek as its own status; the stream is still playing.
    assert avrcp_meta.status_to_playback_status("forward-seek") == "playing"
    assert avrcp_meta.status_to_playback_status("reverse-seek") == "playing"
    # An error or an unknown value is reported as stopped, never crashes.
    assert avrcp_meta.status_to_playback_status("error") == "stopped"
    assert avrcp_meta.status_to_playback_status(None) == "stopped"


def test_build_properties_for_an_empty_track_has_only_playback_status():
    # Review Focus 5: a Bluetooth sender with no metadata still plays. The
    # properties carry the playback status and no metadata, so Server.GetStatus
    # simply lacks a title rather than failing.
    props = avrcp_meta.build_properties({}, "playing")

    assert props["playbackStatus"] == "playing"
    assert "metadata" not in props
    # The capability flags must still be present and well-formed.
    assert props["canControl"] is True


def _read_messages(out: io.StringIO) -> list[dict]:
    return [json.loads(line) for line in out.getvalue().splitlines() if line.strip()]


def test_handshake_emits_ready_then_answers_get_properties_then_one_notice_per_track():
    out = io.StringIO()
    player = avrcp_meta.Player()

    get_properties = {"id": 1, "jsonrpc": "2.0", "method": "Plugin.Stream.Player.GetProperties"}
    second_track = {"Title": "Soul Town", "Artist": "Passport"}
    events = [
        ("request", get_properties),
        ("track", FULL_TRACK),
        ("track", second_track),
    ]

    avrcp_meta.serve(player, events, out)

    messages = _read_messages(out)

    # First line is the readiness announcement.
    assert messages[0] == {"jsonrpc": "2.0", "method": "Plugin.Stream.Ready"}

    # The GetProperties request is answered with a result carrying its id.
    responses = [m for m in messages if m.get("id") == 1]
    assert len(responses) == 1
    assert "result" in responses[0]
    assert responses[0]["result"]["playbackStatus"] == "stopped"

    # Exactly one Properties notification per track change, in order.
    notices = [m for m in messages if m.get("method") == "Plugin.Stream.Player.Properties"]
    assert len(notices) == 2
    assert notices[0]["params"]["metadata"]["title"] == "Soul Town"
    assert notices[1]["params"]["metadata"]["artist"] == ["Passport"]


def test_a_track_change_without_metadata_notifies_without_a_title():
    out = io.StringIO()
    player = avrcp_meta.Player()

    avrcp_meta.serve(player, [("status", "playing"), ("track", {})], out)

    notices = [
        m
        for m in _read_messages(out)
        if m.get("method") == "Plugin.Stream.Player.Properties"
    ]
    # The empty track still produces a notification, with a playback status and
    # no metadata block (Review Focus 5).
    assert notices
    last = notices[-1]["params"]
    assert last["playbackStatus"] == "playing"
    assert "metadata" not in last


class _NonAtomicStream:
    """A stream whose write is deliberately non-atomic: it appends one character
    at a time and yields the interpreter between characters.

    At runtime the control script writes stdout from two threads (the event loop
    and the D-Bus reader's error path). If write_message did not serialize its
    writes, two messages written through a stream like this would interleave
    their characters and produce corrupt, unparseable lines. With the writes
    serialized, every line stays whole no matter how the threads are scheduled.
    """

    def __init__(self) -> None:
        self.chars: list[str] = []

    def write(self, text: str) -> None:
        for char in text:
            self.chars.append(char)
            time.sleep(0)  # a yield point, so an unserialized writer interleaves

    def flush(self) -> None:
        pass

    def getvalue(self) -> str:
        return "".join(self.chars)


def test_concurrent_writes_never_interleave_json_lines():
    out = _NonAtomicStream()
    threads_count = 16
    per_thread = 40

    def writer(thread_id: int) -> None:
        for n in range(per_thread):
            avrcp_meta.write_message(
                out, {"jsonrpc": "2.0", "id": f"{thread_id}-{n}", "value": thread_id}
            )

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(threads_count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    lines = [line for line in out.getvalue().splitlines() if line]
    # Every line parses as whole JSON (no interleaving corrupted any of them)...
    parsed = [json.loads(line) for line in lines]
    # ...and exactly the messages that were written came out, each once.
    expected = {f"{i}-{n}" for i in range(threads_count) for n in range(per_thread)}
    assert {message["id"] for message in parsed} == expected
    assert len(parsed) == threads_count * per_thread


# --- control (Plugin.Stream.Player.Control) ---

_CAN_FLAGS = ("canControl", "canPlay", "canPause", "canGoNext", "canGoPrevious")


def test_build_properties_is_controllable_by_default_but_never_seekable():
    props = avrcp_meta.build_properties({}, "playing")
    assert all(props[flag] is True for flag in _CAN_FLAGS)
    assert props["canSeek"] is False


def test_build_properties_reports_nothing_controllable_without_a_player():
    props = avrcp_meta.build_properties({}, "stopped", controllable=False)
    assert all(props[flag] is False for flag in _CAN_FLAGS)
    assert props["canSeek"] is False


def test_player_is_controllable_exactly_when_it_has_a_path():
    player = avrcp_meta.Player()
    assert player.path is None
    assert player.properties()["canControl"] is False
    player.path = "/org/bluez/hci0/dev_X/player0"
    assert player.properties()["canControl"] is True
    player.path = None
    assert player.properties()["canControl"] is False


@pytest.mark.parametrize(
    "command,status,expected",
    [
        ("playPause", "playing", "Pause"),
        ("playPause", "paused", "Play"),
        ("playPause", "stopped", "Play"),
        ("play", "playing", "Play"),
        ("pause", "paused", "Pause"),
        ("next", "playing", "Next"),
        ("previous", "paused", "Previous"),
        ("seek", "playing", None),
        ("bogus", "playing", None),
    ],
)
def test_control_method_maps_snapcast_commands_to_mediaplayer1(command, status, expected):
    assert avrcp_meta.control_method(command, status) == expected


def _control_request(command, request_id=7):
    return {
        "id": request_id,
        "jsonrpc": "2.0",
        "method": "Plugin.Stream.Player.Control",
        "params": {"command": command, "params": {}},
    }


def _player_with_path(status="playing"):
    player = avrcp_meta.Player(status=status)
    player.path = "/org/bluez/hci0/dev_X/player0"
    return player


def test_control_request_calls_the_method_and_answers_ok():
    calls = []
    response = avrcp_meta.handle_request(
        _control_request("playPause"), _player_with_path("playing"), calls.append
    )
    assert calls == ["Pause"]
    assert response == {"jsonrpc": "2.0", "id": 7, "result": "ok"}


def test_control_request_with_an_unknown_command_is_invalid_params():
    calls = []
    response = avrcp_meta.handle_request(
        _control_request("seek"), _player_with_path(), calls.append
    )
    assert calls == []
    assert response["error"]["code"] == -32602
    assert response["id"] == 7


def test_control_request_without_a_player_is_an_internal_error():
    # Review Focus 1: the phone is gone, so there is nothing to control.
    calls = []
    response = avrcp_meta.handle_request(
        _control_request("next"), avrcp_meta.Player(), calls.append
    )
    assert calls == []
    assert response["error"]["code"] == -32603


def test_control_request_whose_call_raises_is_an_internal_error():
    def boom(_name):
        raise RuntimeError("org.bluez.Error.NotAvailable")

    response = avrcp_meta.handle_request(_control_request("next"), _player_with_path(), boom)
    assert response["error"]["code"] == -32603
    assert "NotAvailable" in response["error"]["message"]


def test_control_request_without_an_id_gets_no_reply():
    request = _control_request("next")
    del request["id"]
    calls = []
    assert avrcp_meta.handle_request(request, _player_with_path(), calls.append) is None


def test_other_requests_are_still_declined_and_get_properties_still_answers():
    player = _player_with_path()
    declined = avrcp_meta.handle_request(
        {"id": 2, "method": "Plugin.Stream.Player.SetVolume", "params": {"volume": 5}},
        player,
        lambda _n: None,
    )
    assert declined["error"]["code"] == -32601
    got = avrcp_meta.handle_request(
        {"id": 3, "method": "Plugin.Stream.Player.GetProperties"}, player, lambda _n: None
    )
    assert got["result"]["canControl"] is True


def test_player_events_set_and_clear_the_path_and_notify():
    out = io.StringIO()
    player = avrcp_meta.Player()
    path = "/org/bluez/hci0/dev_X/player0"

    avrcp_meta.process_event(player, ("player", path), out)
    avrcp_meta.process_event(player, ("player", None), out)

    assert player.path is None
    notices = [
        m
        for m in _read_messages(out)
        if m.get("method") == "Plugin.Stream.Player.Properties"
    ]
    assert [n["params"]["canControl"] for n in notices] == [True, False]


def test_a_control_request_event_reaches_the_injected_control():
    out = io.StringIO()
    player = _player_with_path("paused")
    calls = []

    avrcp_meta.process_event(player, ("request", _control_request("playPause")), out, calls.append)

    assert calls == ["Play"]
    assert _read_messages(out) == [{"jsonrpc": "2.0", "id": 7, "result": "ok"}]


def test_disconnect_then_control_returns_an_error_and_does_not_crash():
    out = io.StringIO()
    player = avrcp_meta.Player()
    events = [
        ("player", "/org/bluez/hci0/dev_X/player0"),
        ("player", None),
        ("request", _control_request("next")),
    ]
    avrcp_meta.serve(player, events, out, lambda _n: None)
    last = _read_messages(out)[-1]
    assert last["error"]["code"] == -32603


def _variant(value):
    return ("v", value)


def test_player_events_emit_path_then_initial_track_status_position():
    props = {
        "Track": _variant({"Title": _variant("Soul Town"), "Artist": _variant("A")}),
        "Status": _variant("paused"),
        "Position": _variant(4000),
    }
    events = list(avrcp_meta.player_events("/org/bluez/hci0/dev_X/player0", props))
    assert events == [
        ("player", "/org/bluez/hci0/dev_X/player0"),
        ("track", {"Title": "Soul Town", "Artist": "A"}),
        ("status", "paused"),
        ("position", 4000),
    ]


def test_player_events_with_no_properties_still_announces_the_player():
    # A phone that connects paused may carry no Track at all; it must still be
    # controllable at once.
    assert list(avrcp_meta.player_events("/p", {})) == [("player", "/p")]


@pytest.mark.parametrize(
    "current, signal, expected",
    [
        (None, "/p0", True),
        ("/p0", "/p0", True),
        ("/p0", "/p1", False),
        ("/p0", None, False),
        (None, None, False),
    ],
)
def test_follows_player_only_adopts_a_path_when_none_is_held(current, signal, expected):
    assert avrcp_meta.follows_player(current, signal) is expected
