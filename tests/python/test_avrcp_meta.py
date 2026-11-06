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

from snaphouse import avrcp_meta


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
    assert meta["trackNumber"] == "6"
    # BlueZ Duration is milliseconds; Snapcast duration is seconds.
    assert meta["duration"] == 305.293


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
    assert props["canControl"] is False


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
