#!/opt/snaphouse/.venv/bin/python
"""AirPlay metadata for the Snapcast "AirPlay" stream.

snapserver starts this script as the `controlscript` for the AirPlay stream and
speaks newline-delimited JSON-RPC 2.0 with it over stdin/stdout (Snapcast's
stream plugin API, doc/json_rpc_api/stream_plugin.md). The script reads the
metadata pipe that the hub's shairport-sync writes (the type/code/length/data
item stream shairport emits when run with a metadata pipe and cover art) and
republishes the now-playing title, artist, album, playback status, and a cover
*URL* as Snapcast stream properties, so a now-playing card shows up in
`Server.GetStatus` for the room picker and the nightstand frame.

Why a URL and never the image bytes: snapserver's own airplay source embedded
the full-resolution cover as base64 `artData` in the stream properties, which
made every `Server.GetStatus` megabytes long and stalled the realtime stream
reader (issue #38). Here the cover is downscaled to a small JPEG, written once to
the hub's web root under /cover/, and only its URL travels in the properties.

The shebang points at the on-device uv venv (`/opt/snaphouse/.venv`), so
snapserver runs this with the interpreter that has Pillow (the hub-only `hub`
extra; see pyproject.toml and roles/selfupdate). Pillow is imported lazily in the
downscale path so the testable core imports anywhere.

Design notes, mirroring lib/snaphouse/avrcp_meta.py:

- The pipe framing, the item-to-property mapping, the playback-state mapping, and
  the cover downscaling are pure functions, unit tested in
  tests/python/test_airplay_meta.py. The IO loop (`main`: opening the pipe,
  reading stdin) needs the real hub and is confirmed on site, so it is excluded
  from coverage.
- The house does not control the AirPlay sender, so the stream reports
  `canControl: false`; snapserver will not forward control or volume commands
  here. The sender's volume is applied to the audio by shairport-sync itself
  (software volume), so the whole house attenuates at the source.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import socket
import sys
import tempfile
import threading
from io import BytesIO
from pathlib import Path
from typing import Any, Iterable, Optional


# Where shairport-sync writes its metadata item stream, and where the downscaled
# covers are published (the hub's nginx root serves this dir at /cover/). Both
# are overridable on the command line for tests.
DEFAULT_PIPE = "/run/snaphouse/airplay-metadata"
DEFAULT_COVER_DIR = "/var/lib/snaphouse/public/cover"

# Cover size budget: the frame and the room picker only ever show a small image,
# so cap the long edge and the file size and never ship more than that.
COVER_MAX_PX = 600
COVER_MAX_BYTES = 150_000

# shairport-sync 'ssnc' play-state codes to a Snapcast playbackStatus. A flush is
# shairport pausing or clearing its buffer (also sent at a track skip), reported
# as paused; anything else playing/stopped.
_PLAY_STATE = {
    "pbeg": "playing",
    "prsm": "playing",
    "pend": "stopped",
    "pfls": "paused",
}


# --------------------------------------------------------------------------- #
# Parsing the shairport-sync metadata pipe
# --------------------------------------------------------------------------- #

_ITEM_RE = re.compile(r"<item>(.*?)</item>", re.DOTALL)
_TYPE_RE = re.compile(r"<type>([0-9a-fA-F]+)</type>")
_CODE_RE = re.compile(r"<code>([0-9a-fA-F]+)</code>")
_DATA_RE = re.compile(r"<data[^>]*>(.*?)</data>", re.DOTALL)


def decode_fourcc(hex_str: str) -> str:
    """Decode shairport's 8-hex-digit type/code into its four ASCII characters.

    shairport writes a 32-bit tag as eight hex digits; 0x636f7265 is 'core',
    0x73736e63 is 'ssnc'. A malformed value decodes to the empty string.
    """
    try:
        return bytes.fromhex(hex_str).decode("latin-1")
    except ValueError:
        return ""


def parse_item(block: str) -> tuple[str, str, bytes]:
    """Parse one `<item>...</item>` body into (type, code, data bytes).

    The data element is base64 (shairport writes `encoding="base64"`); it is
    decoded to raw bytes, which are the UTF-8 text for a metadata item or the
    image for a picture item. A missing or unparseable data element yields b"".
    """
    type_match = _TYPE_RE.search(block)
    code_match = _CODE_RE.search(block)
    type_str = decode_fourcc(type_match.group(1)) if type_match else ""
    code_str = decode_fourcc(code_match.group(1)) if code_match else ""
    data = b""
    data_match = _DATA_RE.search(block)
    if data_match:
        try:
            data = base64.b64decode("".join(data_match.group(1).split()))
        except (ValueError, base64.binascii.Error):
            data = b""
    return (type_str, code_str, data)


class PipeParser:
    """Incremental reader for shairport's item stream.

    shairport writes a run of `<item>...</item>` elements with no root element,
    and an item (a cover picture especially) can span several reads. `feed`
    buffers partial input and returns the complete items it can extract.
    """

    def __init__(self) -> None:
        self._buffer = ""

    def feed(self, text: str) -> list[tuple[str, str, bytes]]:
        self._buffer += text
        items: list[tuple[str, str, bytes]] = []
        while True:
            match = _ITEM_RE.search(self._buffer)
            if not match:
                break
            items.append(parse_item(match.group(1)))
            self._buffer = self._buffer[match.end():]
        return items


# --------------------------------------------------------------------------- #
# Cover art: downscale to a small JPEG and publish its URL
# --------------------------------------------------------------------------- #


def downscale_cover(
    data: bytes, *, max_px: int = COVER_MAX_PX, max_bytes: int = COVER_MAX_BYTES
) -> bytes:
    """Downscale raw cover-art bytes to a small JPEG, returning the JPEG bytes.

    The long edge is capped at `max_px` (aspect preserved) and the JPEG quality
    is stepped down until the result fits `max_bytes`, so even a large photo
    becomes a small file. Pillow is imported here, not at module load, because it
    is a hub-only dependency.
    """
    from PIL import Image

    image = Image.open(BytesIO(data))
    # JPEG has no alpha; flatten anything else (P, RGBA, CMYK) onto RGB.
    if image.mode != "RGB":
        image = image.convert("RGB")
    image.thumbnail((max_px, max_px))

    encoded = b""
    for quality in (85, 75, 65, 55, 45, 35):
        buffer = BytesIO()
        image.save(buffer, format="JPEG", quality=quality, optimize=True)
        encoded = buffer.getvalue()
        if len(encoded) <= max_bytes:
            break
    return encoded


def build_art_url(host: str, digest: str) -> str:
    """The URL the hub serves a stored cover at (nginx /cover/ on port 80)."""
    return f"http://{host}/cover/{digest}.jpg"


def store_cover(data: bytes, cover_dir: Path, host: str) -> Optional[str]:
    """Downscale and store a cover, returning its URL, or None on any failure.

    The file name is the content hash of the downscaled JPEG, so identical art
    reuses one file and the URL is immutable. The write is atomic (temp file then
    os.replace) and world-readable so nginx can serve it. A decode or write
    failure returns None, so bad art never breaks the metadata stream.
    """
    if not data:
        return None
    try:
        jpeg = downscale_cover(data)
    except Exception:
        return None
    digest = hashlib.sha256(jpeg).hexdigest()[:32]
    cover_dir = Path(cover_dir)
    target = cover_dir / f"{digest}.jpg"
    try:
        cover_dir.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            fd, tmp_name = tempfile.mkstemp(dir=cover_dir, prefix=".cover.", suffix=".jpg")
            try:
                with os.fdopen(fd, "wb") as handle:
                    handle.write(jpeg)
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
    except OSError:
        return None
    return build_art_url(host, digest)


# --------------------------------------------------------------------------- #
# The player state and its Snapcast properties
# --------------------------------------------------------------------------- #


def build_properties(player: "Player") -> dict:
    """Assemble a Snapcast stream properties object from the AirPlay state.

    The metadata block is left out entirely when there is nothing to show, so the
    notification says "playing, no title" rather than carrying an empty block.
    The cover travels only as `artUrl`, never as inline `artData`. The capability
    flags report that the house does not control this stream.
    """
    properties: dict[str, Any] = {
        "playbackStatus": player.playback_status,
        "canGoNext": False,
        "canGoPrevious": False,
        "canPlay": False,
        "canPause": False,
        "canSeek": False,
        "canControl": False,
    }
    metadata: dict[str, Any] = {}
    if player.title:
        metadata["title"] = player.title
    if player.artist:
        metadata["artist"] = [player.artist]
    if player.album:
        metadata["album"] = player.album
    if player.art_url:
        metadata["artUrl"] = player.art_url
    if metadata:
        properties["metadata"] = metadata
    return properties


class Player:
    """The last AirPlay state seen on the pipe, rendered into stream properties."""

    def __init__(self) -> None:
        self.title: Optional[str] = None
        self.artist: Optional[str] = None
        self.album: Optional[str] = None
        self.art_url: Optional[str] = None
        self.playback_status: str = "stopped"

    def properties(self) -> dict:
        return build_properties(self)


def apply_item(
    player: Player,
    type_str: str,
    code_str: str,
    data: bytes,
    *,
    cover_dir: Path,
    host: str,
) -> bool:
    """Apply one pipe item to the player; return True when a property changed.

    Text metadata (core/minm, asar, asal) is accumulated and committed to the
    stream on the metadata-bundle end (ssnc/mden). A picture (ssnc/PICT) is
    downscaled, stored, and its URL set. Play-state codes set the playback
    status. The caller emits a properties notification when this returns True.
    """
    if type_str == "core":
        if code_str == "minm":
            player.title = data.decode("utf-8", "replace") or None
        elif code_str == "asar":
            player.artist = data.decode("utf-8", "replace") or None
        elif code_str == "asal":
            player.album = data.decode("utf-8", "replace") or None
        # Wait for the bundle end to emit, so title/artist/album land together.
        return False

    if type_str == "ssnc":
        if code_str == "mden":
            return True
        if code_str == "PICT":
            player.art_url = store_cover(data, cover_dir, host) if data else None
            return True
        if code_str in _PLAY_STATE:
            player.playback_status = _PLAY_STATE[code_str]
            return True

    return False


# --------------------------------------------------------------------------- #
# The Snapcast stream-plugin protocol (shared shape with avrcp_meta.py)
# --------------------------------------------------------------------------- #


def ready_notification() -> dict:
    return {"jsonrpc": "2.0", "method": "Plugin.Stream.Ready"}


def properties_notification(properties: dict) -> dict:
    return {
        "jsonrpc": "2.0",
        "method": "Plugin.Stream.Player.Properties",
        "params": properties,
    }


def handle_request(request: dict, player: Player) -> Optional[dict]:
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


# stdout is written from the stdin and pipe readers at runtime, so every write is
# serialized under this lock; one message is composed into a line first, then
# written and flushed while holding the lock, so two cannot interleave.
_WRITE_LOCK = threading.Lock()


def write_message(out: Any, message: dict) -> None:
    line = json.dumps(message) + "\n"
    with _WRITE_LOCK:
        out.write(line)
        out.flush()


def process_event(player: Player, event: tuple, out: Any, *, cover_dir: Path, host: str) -> None:
    """Apply one event and emit the JSON-RPC it produces.

    Events are `("request", dict)` for a server request and
    `("item", type, code, data)` for one shairport metadata item.
    """
    kind = event[0]
    if kind == "request":
        response = handle_request(event[1], player)
        if response is not None:
            write_message(out, response)
    elif kind == "item":
        _type, code, data = event[1], event[2], event[3]
        if apply_item(player, event[1], code, data, cover_dir=cover_dir, host=host):
            write_message(out, properties_notification(player.properties()))


def serve(
    player: Player,
    events: Iterable[tuple],
    out: Any,
    *,
    cover_dir: Path = Path(DEFAULT_COVER_DIR),
    host: str = "localhost",
) -> None:
    """Announce readiness, then process a stream of events in order.

    This is the control-script loop with its inputs already merged into one
    iterable, which is what the tests drive. `main` feeds it live stdin requests
    and items parsed from the shairport pipe.
    """
    write_message(out, ready_notification())
    for event in events:
        process_event(player, event, out, cover_dir=cover_dir, host=host)


def _parse_args(argv: Optional[list[str]]) -> argparse.Namespace:
    # snapserver passes --stream and, with HTTP enabled, --snapcast-host and
    # --snapcast-port. The host is used for the cover URL when given; otherwise
    # this host's own mDNS name is used, which is what the frame reaches.
    parser = argparse.ArgumentParser(description="Snapcast AirPlay metadata bridge")
    parser.add_argument("--stream", default=None)
    parser.add_argument("--snapcast-host", default=None)
    parser.add_argument("--snapcast-port", default=None)
    parser.add_argument("--pipe", default=DEFAULT_PIPE)
    parser.add_argument("--cover-dir", default=DEFAULT_COVER_DIR)
    args, _unknown = parser.parse_known_args(argv)
    return args


def _pipe_events(pipe_path: str) -> Iterable[tuple]:  # pragma: no cover
    """Yield ("item", type, code, data) as shairport writes to the metadata pipe.

    The pipe is opened read-only; shairport creates it, so a missing pipe is
    retried. Confirmed on the real hub, not in CI, so the blocking IO lives here.
    """
    import time

    parser = PipeParser()
    while True:
        try:
            fd = os.open(pipe_path, os.O_RDONLY)
        except OSError:
            time.sleep(1.0)
            continue
        with os.fdopen(fd, "r", encoding="utf-8", errors="replace") as pipe:
            while True:
                chunk = pipe.read(65536)
                if chunk == "":
                    break  # writer closed; reopen and keep following
                for item in parser.feed(chunk):
                    yield ("item", item[0], item[1], item[2])


def main(argv: Optional[list[str]] = None) -> int:  # pragma: no cover
    """Run the control script against stdin and the live shairport pipe."""
    import queue

    args = _parse_args(argv)
    out = sys.stdout
    host = args.snapcast_host or f"{socket.gethostname()}.local"
    cover_dir = Path(args.cover_dir)
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

    def read_pipe() -> None:
        try:
            for event in _pipe_events(args.pipe):
                events.put(event)
        except Exception as exc:  # noqa: BLE001 - last resort, keep the stream alive
            write_message(
                out,
                {
                    "jsonrpc": "2.0",
                    "method": "Plugin.Stream.Log",
                    "params": {"severity": "error", "message": f"pipe loop stopped: {exc}"},
                },
            )

    threading.Thread(target=read_requests, daemon=True).start()
    threading.Thread(target=read_pipe, daemon=True).start()

    write_message(out, ready_notification())
    while True:
        event = events.get()
        if event is None:
            return 0
        process_event(player, event, out, cover_dir=cover_dir, host=host)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
