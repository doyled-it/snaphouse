#!/opt/snaphouse/.venv/bin/python
"""Bluetooth AVRCP metadata for the Snapcast "Bluetooth" stream.

snapserver starts this script as the `controlscript` for the Bluetooth stream and
speaks newline-delimited JSON-RPC 2.0 with it over stdin/stdout (Snapcast's
stream plugin API, doc/json_rpc_api/stream_plugin.md). The script watches BlueZ
`org.bluez.MediaPlayer1` over the system bus and republishes the connected
phone's AVRCP track and status as Snapcast stream properties, so a now-playing
title and artist show up in `Server.GetStatus` for the room picker and the
nightstand frame.

The shebang points at the on-device uv venv (`/opt/snaphouse/.venv`), so
snapserver runs this with the interpreter that has the D-Bus library, not the
system python. The venv and that dependency are installed by the roles; see
`roles/metadata` and `pyproject.toml`.

Design notes:

- The protocol and the AVRCP-to-Snapcast mapping are pure functions, unit tested
  in `tests/python/test_avrcp_meta.py`. The D-Bus plumbing (`main` and the signal
  loop) needs real hardware and is confirmed on site, so the heavy import of the
  D-Bus library is deferred into `main`, keeping the testable core importable
  anywhere.
- The house controls playback from the sending phone, not the other way round, so
  the stream reports `canControl: false`: snapserver will not forward control or
  volume commands to this script. The phone's AVRCP volume is applied to the
  audio itself by bluealsa, which does this by default in 4.0 (see `roles/hub_input`).
- A sender that publishes no metadata still plays; its properties carry only a
  playback status and no title (Review Focus 5).
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
from typing import Any, Iterable


# The AVRCP interface BlueZ exposes the phone's media player on. The player
# object path is dynamic (it carries the adapter and device), so the signal loop
# discovers it rather than hard-coding it.
MEDIA_PLAYER_INTERFACE = "org.bluez.MediaPlayer1"
PROPERTIES_INTERFACE = "org.freedesktop.DBus.Properties"

# BlueZ MediaPlayer1 `Status` to Snapcast `playbackStatus`. BlueZ also reports
# the two seek states and `error`; a seek is still playback, anything unknown is
# reported as stopped so the stream degrades quietly.
_PLAYBACK_STATUS = {
    "playing": "playing",
    "paused": "paused",
    "stopped": "stopped",
    "forward-seek": "playing",
    "reverse-seek": "playing",
}


def _clean_string(value: Any) -> str | None:
    """Return a non-empty string, or None for a missing or empty tag."""
    if isinstance(value, str) and value != "":
        return value
    return None


def _clean_int(value: Any) -> int | None:
    """Return a non-negative int, or None. bool is not a number here."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value >= 0:
        return value
    return None


def track_to_metadata(track: dict) -> dict:
    """Map a BlueZ `MediaPlayer1.Track` dict to Snapcast stream metadata.

    Missing keys are omitted and empty strings are dropped, so an untagged field
    never reaches snapserver as an empty value. BlueZ gives string tags plus
    uint32 `TrackNumber` and uint32 `Duration` in milliseconds; Snapcast wants
    `artist` as a list and `duration` in seconds.
    """
    metadata: dict[str, Any] = {}

    title = _clean_string(track.get("Title"))
    if title is not None:
        metadata["title"] = title

    artist = _clean_string(track.get("Artist"))
    if artist is not None:
        metadata["artist"] = [artist]

    album = _clean_string(track.get("Album"))
    if album is not None:
        metadata["album"] = album

    track_number = _clean_int(track.get("TrackNumber"))
    if track_number is not None:
        # Snapcast's trackNumber is a string.
        metadata["trackNumber"] = str(track_number)

    duration_ms = _clean_int(track.get("Duration"))
    if duration_ms:
        metadata["duration"] = duration_ms / 1000.0

    return metadata


def status_to_playback_status(status: Any) -> str:
    """Map a BlueZ `MediaPlayer1.Status` to a Snapcast `playbackStatus`."""
    return _PLAYBACK_STATUS.get(status, "stopped")


def build_properties(track: dict, status: Any) -> dict:
    """Assemble a Snapcast stream properties object from AVRCP state.

    The metadata block is left out entirely when there is no metadata, so the
    notification says "playing, no title" rather than carrying an empty block
    (Review Focus 5). The capability flags report that the house does not control
    this stream: playback is driven from the phone.
    """
    properties: dict[str, Any] = {
        "playbackStatus": status_to_playback_status(status),
        "canGoNext": False,
        "canGoPrevious": False,
        "canPlay": False,
        "canPause": False,
        "canSeek": False,
        "canControl": False,
    }
    metadata = track_to_metadata(track)
    if metadata:
        properties["metadata"] = metadata
    return properties


class Player:
    """The last AVRCP state seen from BlueZ, rendered into stream properties."""

    def __init__(self, track: dict | None = None, status: Any = None) -> None:
        self.track: dict = track or {}
        self.status: Any = status

    def properties(self) -> dict:
        return build_properties(self.track, self.status)


def ready_notification() -> dict:
    return {"jsonrpc": "2.0", "method": "Plugin.Stream.Ready"}


def properties_notification(properties: dict) -> dict:
    return {
        "jsonrpc": "2.0",
        "method": "Plugin.Stream.Player.Properties",
        "params": properties,
    }


def handle_request(request: dict, player: Player) -> dict | None:
    """Answer a JSON-RPC request from snapserver.

    Only `GetProperties` is acted on; the stream is not controllable, so any
    control or set request is declined with a JSON-RPC "method not found" error.
    A request with no id (a notification) gets no reply.
    """
    request_id = request.get("id")
    method = request.get("method", "")
    short = method.rsplit(".", 1)[-1]

    if short == "GetProperties":
        if request_id is None:
            return None
        return {"jsonrpc": "2.0", "id": request_id, "result": player.properties()}

    if request_id is None:
        return None
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": -32601, "message": f"Method not found: {method}"},
    }


# stdout is written from two threads at runtime (the main event loop and the
# D-Bus reader's error path), so every write is serialized under this lock. One
# JSON-RPC message is composed into a single line first, then written and flushed
# while holding the lock, so two messages can never interleave on the wire.
_WRITE_LOCK = threading.Lock()


def write_message(out: Any, message: dict) -> None:
    line = json.dumps(message) + "\n"
    with _WRITE_LOCK:
        out.write(line)
        out.flush()


def process_event(player: Player, event: tuple, out: Any) -> None:
    """Apply one event and emit the JSON-RPC it produces.

    Events are `("request", dict)` for a server request, `("track", dict)` for a
    new AVRCP track, and `("status", value)` for a new playback status.
    """
    kind = event[0]
    if kind == "request":
        response = handle_request(event[1], player)
        if response is not None:
            write_message(out, response)
    elif kind == "track":
        player.track = event[1] or {}
        write_message(out, properties_notification(player.properties()))
    elif kind == "status":
        player.status = event[1]
        write_message(out, properties_notification(player.properties()))


def serve(player: Player, events: Iterable[tuple], out: Any) -> None:
    """Announce readiness, then process a stream of events in order.

    This is the control-script loop with its inputs already merged into one
    iterable, which is what the tests drive. `main` feeds it live events from
    stdin and D-Bus.
    """
    write_message(out, ready_notification())
    for event in events:
        process_event(player, event, out)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    # snapserver always passes --stream and, with HTTP enabled, --snapcast-host
    # and --snapcast-port. They are accepted and ignored: this script reads from
    # BlueZ, not from snapserver's HTTP API.
    parser = argparse.ArgumentParser(description="Snapcast Bluetooth AVRCP metadata bridge")
    parser.add_argument("--stream", default=None)
    parser.add_argument("--snapcast-host", default=None)
    parser.add_argument("--snapcast-port", default=None)
    args, _unknown = parser.parse_known_args(argv)
    return args


def _dbus_events(log: Any = None) -> Iterable[tuple]:  # pragma: no cover
    """Yield ("track", dict) and ("status", value) as BlueZ MediaPlayer1 changes.

    This is the hardware-facing half: it watches `PropertiesChanged` on the AVRCP
    interface over the system bus. It is confirmed on the real hub (build step 6),
    not in CI, so the D-Bus library is imported here rather than at module load.
    """
    from jeepney import DBusAddress, MatchRule, message_bus, new_method_call
    from jeepney.io.blocking import open_dbus_connection

    def unwrap(value: Any) -> Any:
        # jeepney returns a variant as a (signature, value) tuple; the payload is
        # the second item. Dicts of variants are unwrapped one level deep.
        if isinstance(value, tuple) and len(value) == 2 and isinstance(value[0], str):
            return value[1]
        return value

    connection = open_dbus_connection(bus="SYSTEM")

    # Ask the bus to deliver MediaPlayer1 PropertiesChanged signals.
    rule = MatchRule(
        type="signal",
        interface=PROPERTIES_INTERFACE,
        member="PropertiesChanged",
    )
    rule.add_arg_condition(0, MEDIA_PLAYER_INTERFACE)
    connection.send_and_get_reply(message_bus.AddMatch(rule.serialise()))

    # Emit the current state once, so a track already playing at startup shows up
    # without waiting for the next change.
    object_manager = DBusAddress(
        "/", bus_name="org.bluez", interface="org.freedesktop.DBus.ObjectManager"
    )
    managed = connection.send_and_get_reply(
        new_method_call(object_manager, "GetManagedObjects")
    )
    for _path, interfaces in (managed.body[0] if managed.body else {}).items():
        player = interfaces.get(MEDIA_PLAYER_INTERFACE)
        if not player:
            continue
        if "Track" in player:
            yield ("track", {k: unwrap(v) for k, v in unwrap(player["Track"]).items()})
        if "Status" in player:
            yield ("status", unwrap(player["Status"]))

    while True:
        message = connection.receive()
        body = message.body
        if len(body) < 2:
            continue
        interface, changed = body[0], body[1]
        if interface != MEDIA_PLAYER_INTERFACE:
            continue
        if "Track" in changed:
            yield ("track", {k: unwrap(v) for k, v in unwrap(changed["Track"]).items()})
        if "Status" in changed:
            yield ("status", unwrap(changed["Status"]))


def main(argv: list[str] | None = None) -> int:  # pragma: no cover
    """Run the control script against stdin and the live BlueZ signal stream."""
    import queue

    _parse_args(argv)
    out = sys.stdout
    player = Player()
    events: "queue.Queue[tuple | None]" = queue.Queue()

    def read_requests() -> None:
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                events.put(("request", json.loads(line)))
            except json.JSONDecodeError:
                continue
        events.put(None)

    def read_dbus() -> None:
        try:
            for event in _dbus_events():
                events.put(event)
        except Exception as exc:  # noqa: BLE001 - last resort, keep the stream alive
            write_message(
                out,
                {
                    "jsonrpc": "2.0",
                    "method": "Plugin.Stream.Log",
                    "params": {"severity": "error", "message": f"D-Bus loop stopped: {exc}"},
                },
            )

    threading.Thread(target=read_requests, daemon=True).start()
    threading.Thread(target=read_dbus, daemon=True).start()

    write_message(out, ready_notification())
    while True:
        event = events.get()
        if event is None:
            return 0
        process_event(player, event, out)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
