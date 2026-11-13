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
- While a phone's media player is present the stream is controllable: it reports
  `canControl`, `canPlay`, `canPause`, `canGoNext` and `canGoPrevious`, and a
  `Plugin.Stream.Player.Control` request becomes a `MediaPlayer1` method call
  (Play, Pause, Next, Previous) on that player. With no player (the phone
  disconnected) every flag is false and a control request gets a JSON-RPC error.
  Seek and volume set requests are still declined. The phone's AVRCP volume is
  applied to the audio itself by bluealsa, which does this by default in 4.0 (see
  `roles/hub_input`).
- A sender that publishes no metadata still plays; its properties carry only a
  playback status and no title (Review Focus 5).
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from typing import Any, Callable, Iterable


# The AVRCP interface BlueZ exposes the phone's media player on. The player
# object path is dynamic (it carries the adapter and device), so the signal loop
# discovers it rather than hard-coding it.
MEDIA_PLAYER_INTERFACE = "org.bluez.MediaPlayer1"
PROPERTIES_INTERFACE = "org.freedesktop.DBus.Properties"

# On a track change the previous track's position anchor is dropped, unless a
# MediaPlayer1.Position for the new track arrived within this many seconds before
# it: BlueZ can report Position just before or alongside the new Track, so a very
# recent one is kept as the new track's position.
POSITION_KEEP_WINDOW_SECONDS = 3.0

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


def _track_identity(track: dict) -> tuple:
    """A track's identity (title, artist, album), for telling a real track change
    from a same-track metadata resend."""
    return (track.get("Title"), track.get("Artist"), track.get("Album"))


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

    genre = _clean_string(track.get("Genre"))
    if genre is not None:
        # Snapcast wants genre as a list, even for a single genre.
        metadata["genre"] = [genre]

    track_number = _clean_int(track.get("TrackNumber"))
    if track_number is not None:
        # Snapcast wants trackNumber as a number; a string makes snapserver 0.35
        # log "failed to read tag trackNumber: type must be number, but is string".
        metadata["trackNumber"] = track_number

    duration_ms = _clean_int(track.get("Duration"))
    if duration_ms:
        metadata["duration"] = duration_ms / 1000.0

    return metadata


def status_to_playback_status(status: Any) -> str:
    """Map a BlueZ `MediaPlayer1.Status` to a Snapcast `playbackStatus`."""
    return _PLAYBACK_STATUS.get(status, "stopped")


# Snapcast control command to BlueZ MediaPlayer1 method. playPause is resolved
# against the current status in `control_method`; seek is not supported.
_CONTROL_METHODS = {
    "play": "Play",
    "pause": "Pause",
    "next": "Next",
    "previous": "Previous",
}


def control_method(command: str, status: Any) -> str | None:
    """Map a Snapcast control command to a `MediaPlayer1` method name, or None
    when the command is not supported (seek, or anything unknown)."""
    if command == "playPause":
        return "Pause" if status_to_playback_status(status) == "playing" else "Play"
    return _CONTROL_METHODS.get(command)


def build_properties(
    track: dict,
    status: Any,
    *,
    position: float | None = None,
    controllable: bool = True,
) -> dict:
    """Assemble a Snapcast stream properties object from AVRCP state.

    The metadata block is left out entirely when there is no metadata, so the
    notification says "playing, no title" rather than carrying an empty block
    (Review Focus 5). When known, the extrapolated `position` (seconds) rides at
    the top level. The capability flags are true while a phone's media player is
    present (`controllable`) and all false otherwise; seeking is never offered.
    """
    properties: dict[str, Any] = {
        "playbackStatus": status_to_playback_status(status),
        "canGoNext": controllable,
        "canGoPrevious": controllable,
        "canPlay": controllable,
        "canPause": controllable,
        "canSeek": False,
        "canControl": controllable,
    }
    if position is not None:
        properties["position"] = position
    metadata = track_to_metadata(track)
    if metadata:
        properties["metadata"] = metadata
    return properties


class Player:
    """The last AVRCP state seen from BlueZ, rendered into stream properties."""

    def __init__(
        self,
        track: dict | None = None,
        status: Any = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.track: dict = track or {}
        self.status: Any = status
        # Position anchor: the position (seconds) last reported by BlueZ and the
        # monotonic time it was reported at, from which the position is
        # extrapolated while playing and frozen while paused or stopped.
        self._clock = clock
        self._anchor_pos: float | None = None
        self._anchor_at: float | None = None
        # The monotonic time a MediaPlayer1.Position last set the anchor, so a
        # very recent Position is recognised as belonging to a just-changed track.
        self._position_at: float | None = None
        # The D-Bus object path of the phone's MediaPlayer1, or None when no phone
        # player is present. The stream is controllable exactly when this is set.
        self.path: str | None = None

    def current_position(self) -> float | None:
        """The track position in seconds now, or None when no anchor is known.

        Extrapolated forward while playing and held at the anchor while paused or
        stopped, so a properties resend for an unrelated change never carries a
        stale position (Review Focus 1). Clamped to `[0, duration]` when the
        track's duration is known.
        """
        if self._anchor_pos is None or self._anchor_at is None:
            return None
        if status_to_playback_status(self.status) == "playing":
            position = self._anchor_pos + (self._clock() - self._anchor_at)
        else:
            position = self._anchor_pos
        duration = track_to_metadata(self.track).get("duration")
        if duration is not None:
            position = max(0.0, min(position, duration))
        return position

    def anchor_position(self, position_ms: Any) -> None:
        """Anchor the position from a BlueZ `MediaPlayer1.Position` value (ms)."""
        value = _clean_int(position_ms)
        if value is None:
            return
        now = self._clock()
        self._anchor_pos = value / 1000.0
        self._anchor_at = now
        self._position_at = now

    def set_track(self, track: dict | None) -> None:
        """Apply a new track, dropping a stale position anchor on a real change.

        A track's identity is (title, artist, album). When it differs from the
        current track's, the previous track's position anchor is dropped so it
        does not extrapolate against the new track, unless a Position arrived
        within the keep window before this track (BlueZ can report the two close
        together). A same-track resend leaves the anchor untouched.
        """
        new_track = track or {}
        if _track_identity(new_track) != _track_identity(self.track):
            recent_position = (
                self._position_at is not None
                and (self._clock() - self._position_at) <= POSITION_KEEP_WINDOW_SECONDS
            )
            if not recent_position:
                self._anchor_pos = None
                self._anchor_at = None
                self._position_at = None
        self.track = new_track

    def set_status(self, status: Any) -> None:
        """Apply a new status, re-anchoring so a pause freezes and a resume
        continues from the position the track reached."""
        position = self.current_position()
        if position is not None:
            self._anchor_pos = position
            self._anchor_at = self._clock()
        self.status = status

    def properties(self) -> dict:
        return build_properties(
            self.track,
            self.status,
            position=self.current_position(),
            controllable=self.path is not None,
        )


def ready_notification() -> dict:
    return {"jsonrpc": "2.0", "method": "Plugin.Stream.Ready"}


def properties_notification(properties: dict) -> dict:
    return {
        "jsonrpc": "2.0",
        "method": "Plugin.Stream.Player.Properties",
        "params": properties,
    }


def _error(request_id: Any, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def handle_request(
    request: dict, player: Player, control: Callable[[str], None]
) -> dict | None:
    """Answer a JSON-RPC request from snapserver.

    `GetProperties` answers with the current properties. `Control` maps the
    command to a `MediaPlayer1` method and calls `control(method_name)`: an
    unknown command (seek, or a typo) is -32602, and no player path or a failing
    `control` is -32603. Anything else is declined with "method not found". A
    request with no id (a notification) gets no reply.
    """
    request_id = request.get("id")
    method = request.get("method", "")
    short = method.rsplit(".", 1)[-1]

    if request_id is None:
        return None

    if short == "GetProperties":
        return {"jsonrpc": "2.0", "id": request_id, "result": player.properties()}

    if short == "Control":
        params = request.get("params")
        command = params.get("command") if isinstance(params, dict) else None
        name = control_method(command, player.status) if isinstance(command, str) else None
        if name is None:
            return _error(request_id, -32602, f"Unsupported command: {command}")
        if player.path is None:
            return _error(request_id, -32603, "No phone is connected")
        try:
            control(name)
        except Exception as exc:  # noqa: BLE001 - any failure is reported, never raised
            return _error(request_id, -32603, f"Control failed: {exc}")
        return {"jsonrpc": "2.0", "id": request_id, "result": "ok"}

    return _error(request_id, -32601, f"Method not found: {method}")


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


def _no_control(_method: str) -> None:
    raise RuntimeError("control is not available")


def process_event(
    player: Player,
    event: tuple,
    out: Any,
    control: Callable[[str], None] = _no_control,
) -> None:
    """Apply one event and emit the JSON-RPC it produces.

    Events are `("request", dict)` for a server request, `("track", dict)` for a
    new AVRCP track, `("status", value)` for a new playback status, and
    `("position", ms)` for a new `MediaPlayer1.Position` value, and
    `("player", path | None)` when the phone's media player appears or goes away.
    """
    kind = event[0]
    if kind == "request":
        response = handle_request(event[1], player, control)
        if response is not None:
            write_message(out, response)
    elif kind == "track":
        player.set_track(event[1])
        write_message(out, properties_notification(player.properties()))
    elif kind == "status":
        player.set_status(event[1])
        write_message(out, properties_notification(player.properties()))
    elif kind == "position":
        player.anchor_position(event[1])
        write_message(out, properties_notification(player.properties()))
    elif kind == "player":
        player.path = event[1]
        write_message(out, properties_notification(player.properties()))


def serve(
    player: Player,
    events: Iterable[tuple],
    out: Any,
    control: Callable[[str], None] = _no_control,
) -> None:
    """Announce readiness, then process a stream of events in order.

    This is the control-script loop with its inputs already merged into one
    iterable, which is what the tests drive. `main` feeds it live events from
    stdin and D-Bus.
    """
    write_message(out, ready_notification())
    for event in events:
        process_event(player, event, out, control)


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
    """Yield ("track", dict), ("status", value) and ("player", path | None) as
    BlueZ MediaPlayer1 changes.

    This is the hardware-facing half: it watches `PropertiesChanged` on the AVRCP
    interface over the system bus. It is confirmed on the real hub (build step 6),
    not in CI, so the D-Bus library is imported here rather than at module load.
    """
    from jeepney import DBusAddress, HeaderFields, MatchRule, message_bus, new_method_call
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

    # The player object appears and disappears with the phone's connection.
    removed = MatchRule(
        type="signal",
        interface="org.freedesktop.DBus.ObjectManager",
        member="InterfacesRemoved",
    )
    connection.send_and_get_reply(message_bus.AddMatch(removed.serialise()))

    current_path: str | None = None

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
        current_path = _path
        yield ("player", _path)
        if "Track" in player:
            yield ("track", {k: unwrap(v) for k, v in unwrap(player["Track"]).items()})
        if "Status" in player:
            yield ("status", unwrap(player["Status"]))
        if "Position" in player:
            yield ("position", unwrap(player["Position"]))

    while True:
        message = connection.receive()
        body = message.body
        member = message.header.fields.get(HeaderFields.member)
        if member == "InterfacesRemoved":
            removed_path, interfaces = body[0], body[1]
            if MEDIA_PLAYER_INTERFACE in interfaces and removed_path == current_path:
                current_path = None
                yield ("player", None)
            continue
        if len(body) < 2:
            continue
        interface, changed = body[0], body[1]
        if interface != MEDIA_PLAYER_INTERFACE:
            continue
        signal_path = message.header.fields.get(HeaderFields.path)
        if signal_path and signal_path != current_path:
            current_path = signal_path
            yield ("player", signal_path)
        if "Track" in changed:
            yield ("track", {k: unwrap(v) for k, v in unwrap(changed["Track"]).items()})
        if "Status" in changed:
            yield ("status", unwrap(changed["Status"]))
        if "Position" in changed:
            yield ("position", unwrap(changed["Position"]))


class _BluezControl:  # pragma: no cover
    """Calls a method on the phone's `MediaPlayer1` over its own D-Bus connection.

    The signal reader owns a blocking connection that sits in `receive()`, so
    control calls get a separate one, opened on first use and dropped on any
    failure so the next call starts clean. The call waits at most 2 seconds.
    """

    TIMEOUT_SECONDS = 2.0

    def __init__(self, player: Player) -> None:
        self._player = player
        self._connection: Any = None

    def __call__(self, method: str) -> None:
        from jeepney import DBusAddress, HeaderFields, MessageType, new_method_call
        from jeepney.io.blocking import open_dbus_connection

        path = self._player.path
        if path is None:
            raise RuntimeError("no player")
        try:
            if self._connection is None:
                self._connection = open_dbus_connection(bus="SYSTEM")
            address = DBusAddress(path, bus_name="org.bluez", interface=MEDIA_PLAYER_INTERFACE)
            reply = self._connection.send_and_get_reply(
                new_method_call(address, method), timeout=self.TIMEOUT_SECONDS
            )
            if reply.header.message_type == MessageType.error:
                name = reply.header.fields.get(HeaderFields.error_name, "error")
                raise RuntimeError(str(name))
        except Exception:
            connection, self._connection = self._connection, None
            if connection is not None:
                try:
                    connection.close()
                except Exception:  # noqa: BLE001
                    pass
            raise


def main(argv: list[str] | None = None) -> int:  # pragma: no cover
    """Run the control script against stdin and the live BlueZ signal stream."""
    import queue

    _parse_args(argv)
    out = sys.stdout
    player = Player()
    events: "queue.Queue[tuple | None]" = queue.Queue()
    control = _BluezControl(player)

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
        process_event(player, event, out, control)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
