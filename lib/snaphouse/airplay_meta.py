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
import time
from io import BytesIO
from pathlib import Path
from typing import Any, Callable, Iterable, Optional


# Where shairport-sync writes its metadata item stream, and where the downscaled
# covers are published (the hub's nginx root serves this dir at /cover/). Both
# are overridable on the command line for tests.
DEFAULT_PIPE = "/run/snaphouse/airplay-metadata"
DEFAULT_COVER_DIR = "/var/lib/snaphouse/public/cover"

# Cover size budget: the frame and the room picker only ever show a small image,
# so cap the long edge and the file size and never ship more than that.
COVER_MAX_PX = 600
COVER_MAX_BYTES = 150_000

# How many covers to keep in the cover directory. Covers are content-addressed,
# so duplicates never accumulate, but distinct tracks would grow the directory
# without bound on this unattended appliance; keep only the most recent ones and
# prune the rest after each write.
MAX_COVERS = 50

# Backoff before reopening the metadata pipe after a read failure, so a broken
# pipe cannot spin the reader.
PIPE_RETRY_SECONDS = 1.0

# RTP audio frames per second: ssnc/prgr progress frame numbers are counted at
# this rate, so a frame delta divided by this is seconds.
RTP_FRAMES_PER_SECOND = 44100

# At a real track change (mden) the previous track's duration and position are
# dropped, unless a progress frame (ssnc/prgr) for the new track arrived within
# this many seconds before the mden: prgr for a new track can arrive just before
# or during its metadata bundle, so a very recent one is kept as the new track's.
PRGR_KEEP_WINDOW_SECONDS = 3.0

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


def prune_covers(cover_dir: Path, keep: int = MAX_COVERS) -> None:
    """Keep only the `keep` most recently modified covers; delete the rest.

    Covers are content-addressed, so this is a slow-growth guard on an unattended
    appliance, not a hot path. Best-effort: any filesystem error is ignored so
    pruning never breaks the metadata stream. Stray atomic-write temp files
    (`.cover.*`) are swept too, so a crash mid-write cannot leave a dotfile
    behind.
    """
    cover_dir = Path(cover_dir)
    try:
        for leftover in cover_dir.glob(".cover.*"):
            try:
                leftover.unlink()
            except OSError:
                pass
        covers = sorted(
            cover_dir.glob("*.jpg"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
    except OSError:
        return
    for stale in covers[keep:]:
        try:
            stale.unlink()
        except OSError:
            pass


def store_cover(data: bytes, cover_dir: Path, host: str, *, keep: int = MAX_COVERS) -> Optional[str]:
    """Downscale and store a cover, returning its URL, or None on any failure.

    The file name is the content hash of the downscaled JPEG, so identical art
    reuses one file and the URL is immutable. The write is atomic (temp file then
    os.replace) and world-readable so nginx can serve it. A decode or write
    failure returns None, so bad art never breaks the metadata stream. After a
    successful write the directory is pruned to the `keep` most recent covers.
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
    prune_covers(cover_dir, keep=keep)
    return build_art_url(host, digest)


# --------------------------------------------------------------------------- #
# The player state and its Snapcast properties
# --------------------------------------------------------------------------- #


def current_position(player: "Player") -> Optional[float]:
    """The track position in seconds at this moment, or None when unknown.

    From the anchor `(position, monotonic time)` the player last recorded, the
    position is extrapolated forward while playing (`anchor_pos + elapsed`) and
    held at the anchor while paused or stopped, so a properties resend for an
    unrelated change never carries a stale position. When the duration is known
    the result is clamped to `[0, duration]`, so a track that plays past its end
    with no fresh progress frame never reports beyond its length.
    """
    if player._anchor_pos is None or player._anchor_at is None:
        return None
    if player.playback_status == "playing":
        position = player._anchor_pos + (player._clock() - player._anchor_at)
    else:
        position = player._anchor_pos
    if player.duration is not None:
        position = max(0.0, min(position, player.duration))
    return position


def build_properties(player: "Player") -> dict:
    """Assemble a Snapcast stream properties object from the AirPlay state.

    The metadata block is left out entirely when there is nothing to show, so the
    notification says "playing, no title" rather than carrying an empty block.
    The cover travels only as `artUrl`, never as inline `artData`. Duration and
    genre ride in the metadata block when known; the top-level `position` is the
    extrapolated position at emit time. The capability flags report that the
    house does not control this stream.
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
    position = current_position(player)
    if position is not None:
        properties["position"] = position
    metadata: dict[str, Any] = {}
    if player.title:
        metadata["title"] = player.title
    if player.artist:
        metadata["artist"] = [player.artist]
    if player.album:
        metadata["album"] = player.album
    if player.duration is not None:
        metadata["duration"] = player.duration
    if player.genre:
        metadata["genre"] = [player.genre]
    if player.art_url:
        metadata["artUrl"] = player.art_url
    if metadata:
        properties["metadata"] = metadata
    return properties


class Player:
    """The last AirPlay state seen on the pipe, rendered into stream properties."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self.title: Optional[str] = None
        self.artist: Optional[str] = None
        self.album: Optional[str] = None
        self.art_url: Optional[str] = None
        self.playback_status: str = "stopped"
        self.duration: Optional[float] = None
        self.genre: Optional[str] = None
        # The committed track's identity (shairport's persistent id when present,
        # else title/artist/album), used to tell a new track from a metadata
        # resend so stale cover art is dropped only on a real track change.
        self._track_id: object = None
        # The identity accumulating in the current metadata bundle.
        self._bundle_id: object = None
        # Position anchor: the position (seconds) last known and the monotonic
        # time it was known at, from which current_position extrapolates.
        self._clock = clock
        self._anchor_pos: Optional[float] = None
        self._anchor_at: Optional[float] = None
        # The monotonic time ssnc/prgr last set the anchor, so a very recent
        # progress frame is recognised as belonging to a just-changed track.
        self._prgr_at: Optional[float] = None
        # True once ssnc/prgr has set the duration for the current track, so the
        # core/astm fallback does not override a real progress duration.
        self._duration_from_prgr: bool = False
        # True once the current metadata bundle (since the last mdst) has set a
        # duration (via core/astm), so a real track change keeps a duration the
        # new track's own bundle supplied.
        self._duration_in_bundle: bool = False

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

    A metadata bundle starts at ssnc/mdst and ends at ssnc/mden. At the start the
    track fields are cleared so a bundle that omits a field (a new track with no
    album, say) does not keep the previous track's value; the core items
    (minm/asar/asal, and the persistent id mper) then fill the bundle, and mden
    commits it. On a real track change (the identity differs from the committed
    one) the cover art URL is cleared, so a new track with no art does not keep
    the previous track's cover; a metadata resend of the same track keeps it. A
    picture (ssnc/PICT) is downscaled, stored, and its URL set. Play-state codes
    set the playback status (ssnc/pend is a stop). The caller emits a properties
    notification when this returns True.
    """
    if type_str == "core":
        if code_str == "minm":
            player.title = data.decode("utf-8", "replace") or None
        elif code_str == "asar":
            player.artist = data.decode("utf-8", "replace") or None
        elif code_str == "asal":
            player.album = data.decode("utf-8", "replace") or None
        elif code_str == "asgn":
            player.genre = data.decode("utf-8", "replace") or None
        elif code_str == "astm":
            # Song time in ms (4-byte big-endian): a duration fallback used only
            # when no ssnc/prgr progress frame has already set the duration. It
            # rides inside the metadata bundle, so note that this bundle carried a
            # duration, which lets a real track change keep it.
            if not player._duration_from_prgr and len(data) == 4:
                player.duration = int.from_bytes(data, "big") / 1000.0
                player._duration_in_bundle = True
        elif code_str == "mper":
            # shairport's persistent track id; the stable identity for a track.
            player._bundle_id = data or None
        # Wait for the bundle end to emit, so title/artist/album land together.
        return False

    if type_str == "ssnc":
        if code_str == "mdst":
            # A (possibly new) track's metadata bundle is starting: clear the
            # text fields so an omitted one does not carry over from the last
            # bundle. The duration and position anchor are NOT cleared here: they
            # are refilled only by a separate progress frame, so clearing them at
            # the bundle start would blank the bar on a same-track resend. They
            # are instead dropped at mden when the track identity actually
            # changes. The duration-source flags reset so this bundle's own
            # core/astm can set the duration.
            player.title = None
            player.artist = None
            player.album = None
            player.genre = None
            player._duration_from_prgr = False
            player._duration_in_bundle = False
            player._bundle_id = None
            return False
        if code_str == "mden":
            track_id = player._bundle_id or (player.title, player.artist, player.album)
            has_track = bool(player._bundle_id) or any(
                (player.title, player.artist, player.album)
            )
            if has_track and track_id != player._track_id:
                # Real track change: drop the previous track's cover, and drop its
                # duration and position anchor too, unless a progress frame for
                # the new track arrived within the keep window (prgr can precede
                # or overlap the bundle) or the new track's own bundle carried a
                # duration (core/astm).
                player.art_url = None
                recent_prgr = (
                    player._prgr_at is not None
                    and (player._clock() - player._prgr_at) <= PRGR_KEEP_WINDOW_SECONDS
                )
                if not recent_prgr:
                    player._anchor_pos = None
                    player._anchor_at = None
                    player._prgr_at = None
                    if not player._duration_in_bundle:
                        player.duration = None
                        player._duration_from_prgr = False
                player._track_id = track_id
            return True
        if code_str == "prgr":
            # ASCII start/current/end RTP frame numbers at 44100 Hz: duration is
            # (end - start) / 44100, the anchor position is (current - start) /
            # 44100 at the moment this frame arrived. prgr arrives at track start
            # and on resume or seek.
            try:
                start, current, end = (
                    int(part) for part in data.decode("ascii").split("/")
                )
            except (ValueError, UnicodeDecodeError):
                return False
            now = player._clock()
            player.duration = (end - start) / RTP_FRAMES_PER_SECOND
            player._duration_from_prgr = True
            player._anchor_pos = (current - start) / RTP_FRAMES_PER_SECOND
            player._anchor_at = now
            player._prgr_at = now
            return True
        if code_str == "PICT":
            player.art_url = store_cover(data, cover_dir, host) if data else None
            return True
        if code_str in _PLAY_STATE:
            # Freeze or thaw the position: capture the current extrapolated value
            # as the new anchor so a pause holds where it stopped and a resume
            # continues from there, independent of any fresh progress frame.
            position = current_position(player)
            if position is not None:
                player._anchor_pos = position
                player._anchor_at = player._clock()
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


def pipe_items(
    open_session: Callable[[], Iterable[str]],
    *,
    log: Callable[[str], None],
    sleep: Callable[[float], None],
    parser_factory: Callable[[], PipeParser] = PipeParser,
    backoff: float = PIPE_RETRY_SECONDS,
    max_sessions: Optional[int] = None,
) -> Iterable[tuple]:
    """Yield ("item", type, code, data) resiliently across pipe sessions.

    `open_session()` opens the metadata pipe and returns an iterator of text
    chunks for one session, ending when shairport (the writer) closes it. A fresh
    parser is built per session, so a partial `<item>` left behind when the pipe
    closed never merges into the next session's first item. Any failure opening
    or reading the pipe is logged once per failure streak (not on every retry),
    backed off with `sleep(backoff)`, and retried, so the reader never dies; a
    clean close just reopens. `max_sessions` bounds the loop for tests (None means
    forever).
    """
    sessions = 0
    logged = False
    while max_sessions is None or sessions < max_sessions:
        sessions += 1
        parser = parser_factory()
        try:
            for chunk in open_session():
                if not chunk:
                    break
                for item in parser.feed(chunk):
                    yield ("item", item[0], item[1], item[2])
            logged = False  # a session opened and closed without error
        except Exception as exc:  # noqa: BLE001 - keep the reader alive
            if not logged:
                log(f"metadata pipe read failed, backing off and retrying: {exc}")
                logged = True
            sleep(backoff)


def _pipe_events(pipe_path: str, *, log: Callable[[str], None]) -> Iterable[tuple]:  # pragma: no cover
    """Resiliently yield items from shairport's metadata pipe (blocking IO).

    shairport creates the pipe; a missing pipe, a read error, or shairport
    restarting is handled by pipe_items (reopen, fresh parser, backoff). Confirmed
    on the real hub, not in CI, so only the blocking IO lives here.
    """
    import time

    def open_session() -> Iterable[str]:
        fd = os.open(pipe_path, os.O_RDONLY)
        pipe = os.fdopen(fd, "r", encoding="utf-8", errors="replace")
        try:
            while True:
                chunk = pipe.read(65536)
                if chunk == "":
                    return  # writer closed; reopen and keep following
                yield chunk
        finally:
            pipe.close()

    yield from pipe_items(open_session, log=log, sleep=time.sleep)


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

    def pipe_log(message: str) -> None:
        write_message(
            out,
            {
                "jsonrpc": "2.0",
                "method": "Plugin.Stream.Log",
                "params": {"severity": "warning", "message": message},
            },
        )

    def read_pipe() -> None:
        # pipe_events is self-healing (reopen, fresh parser, backoff), so it does
        # not return; the guard is only a last resort for an unexpected bug.
        try:
            for event in _pipe_events(args.pipe, log=pipe_log):
                events.put(event)
        except Exception as exc:  # noqa: BLE001 - last resort, keep the stream alive
            pipe_log(f"pipe loop stopped unexpectedly: {exc}")

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
