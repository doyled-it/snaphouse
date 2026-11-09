"""Now-playing tracker for the hub (pure logic).

The hub, not any one frame, is the single source of now-playing data. This
module folds the snapserver control state into the two shapes the frame and the
room-picker page consume:

- ``now-playing.json``: the track on the House stream right now, whichever
  source it came from, with playback status, duration, a position anchor, the
  rooms it is audible in, and a same-origin cover URL.
- ``play-history.json``: the last 50 plays, newest first.

``Tracker`` is fed by the runtime (Task 4) from two snapserver inputs:

- ``on_server``: a ``Server.GetStatus`` server object (streams, groups,
  clients), issued on connect and every reconnect. It carries the House stream
  status (idle or not), which input is playing (the source), the rooms, and the
  House stream properties.
- ``on_properties``: a ``Stream.OnProperties`` notification for one stream,
  carrying fresh playback status, metadata, and position.

``tick`` advances time so a paused track can time out and a play can cross the
history threshold without a snapserver event. ``Tracker`` does no I/O: the
runtime writes the files and clears the ``now_playing_changed`` and
``history_changed`` flags after each write.

Position carries Task 1 semantics. Every properties update that carries
``position`` already holds the value extrapolated to the moment of emit (frozen
while paused), so the tracker anchors ``(position, arrival time)`` on each one
with no stale-value filtering. Consumers extrapolate from that anchor.

``Service`` is the runtime wrapped around the tracker (run as
``python -m snaphouse.nowplaying`` by the ``snaphouse-nowplaying`` systemd unit
on the hub). It follows the House stream over snapserver's newline-delimited
JSON-RPC control port, resolves missing covers on one background worker thread,
and writes the two files atomically whenever the tracker is dirty. It reconnects
with backoff on any socket error and reloads history from disk at start.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import queue
import socket
import sys
import tempfile
import threading
import time
from collections import namedtuple
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import urlsplit

from .artlookup import ArtLookup, keep_for_history, prune_history_covers, urllib_fetch

# A track's identity: title, artist, album. Artist and album may be absent
# (internet radio, some apps); the title is always present when there is a track.
TrackKey = tuple[str, Optional[str], Optional[str]]

# Long-form audio is 20 minutes or more, or a genre that names talk content.
# These two levers decide "Kind" in the design.
_LONG_FORM_SECONDS = 1200
_LONG_FORM_GENRE = ("podcast", "audiobook", "spoken")

# The House meta stream's inputs, in meta priority order, paired with the
# ``source`` value each one publishes. AirPlay wins a tie (it is listed first in
# snapserver.conf.j2, matching house_stream_name in vars/all.yml).
_HOUSE_INPUTS = (("AirPlay", "airplay"), ("Bluetooth", "bluetooth"))

_TrackInfo = namedtuple("_TrackInfo", ["key", "duration", "genre", "art"])

# A lookup that comes back empty is retried for the same current track after a
# back-off: 2 minutes after the first empty attempt, then 10 minutes after the
# second. Three attempts per play at most, so a transient failure recovers
# without a service restart while a genuine miss (short-circuited by ArtLookup's
# own miss cache) costs no repeated network calls.
_ART_RETRY_BACKOFF_S = (120.0, 600.0)
_MAX_ART_ATTEMPTS = 1 + len(_ART_RETRY_BACKOFF_S)


def _route_signature(
    key: TrackKey, kind: str, genre: list[str] | None
) -> tuple:
    """The identity of an art lookup: the track key plus what picks its route.

    Two lookups share an identity when their key, kind, and genre match, so a
    position or status resend does not change it but later metadata (a filled-in
    kind or genre) does.
    """
    return (key, kind, tuple(genre) if genre else ())


def classify(duration: float | None, genre: list[str] | None) -> str:
    """Return ``"long_form"`` or ``"music"`` for a track's duration and genre.

    Long-form when the genre names a podcast, audiobook, or spoken word
    (case-insensitive), or the duration is 20 minutes or more. A track with no
    duration and no telling genre is music.
    """
    if genre:
        joined = " ".join(str(item) for item in genre).lower()
        if any(word in joined for word in _LONG_FORM_GENRE):
            return "long_form"
    if duration is not None and duration >= _LONG_FORM_SECONDS:
        return "long_form"
    return "music"


def rewrite_art_url(url: str | None) -> str | None:
    """Reduce a cover URL to a same-origin ``/cover/`` path, or ``None``.

    The AirPlay bridge publishes an absolute ``http://<host>/cover/...`` URL; the
    now-playing data must carry only a same-origin path on the hub. An absolute
    URL under ``/cover/`` becomes its path, a rooted same-origin path passes
    through, and anything else becomes ``None`` so no off-origin or unexpected
    reference ever ships. A protocol-relative ``//host/...`` URL is rejected too:
    it begins with ``/`` but a browser resolves it to another origin.
    """
    if url is None:
        return None
    url = url.strip()
    if not url:
        return None
    if url.startswith("//"):
        return None
    if url.startswith("/"):
        return url
    parts = urlsplit(url)
    if parts.scheme in ("http", "https") and parts.path.startswith("/cover/"):
        return parts.path
    return None


def _coerce_text(value: object) -> str | None:
    """Coerce a metadata text field to a display string or ``None``.

    Snapcast sends artist as a list normally, but a sender may put a plain string
    or ``null`` there, and album is usually a bare string. A list joins with
    ``", "``, a non-empty string passes through, and anything empty or absent
    becomes ``None`` so missing fields render as null rather than blank text.
    """
    if isinstance(value, list):
        joined = ", ".join(str(item) for item in value if item)
        return joined or None
    if isinstance(value, str):
        return value or None
    return None


def _parse_dt(value: object) -> datetime | None:
    """Parse an ISO-8601 timestamp from a history entry, or ``None``."""
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _parse_track(properties: dict) -> _TrackInfo | None:
    """Pull track identity, duration, genre, and cover out of properties.

    Returns ``None`` when the update carries no title, which means no track in
    this update; the caller keeps the current identity so a transient idle or
    paused update does not drop a held track.
    """
    metadata = properties.get("metadata") or {}
    title = metadata.get("title")
    if not title:
        return None
    key: TrackKey = (
        title,
        _coerce_text(metadata.get("artist")),
        _coerce_text(metadata.get("album")),
    )
    return _TrackInfo(
        key=key,
        duration=metadata.get("duration"),
        genre=metadata.get("genre"),
        art=rewrite_art_url(metadata.get("artUrl")),
    )


def _rooms_for(groups: list[dict], house_stream: str) -> list[str]:
    """The names of the unmuted, connected clients on the House stream.

    The frame's existing room rule, scoped to the House stream: a room counts
    when its group is assigned to the House stream, the group is not muted, the
    client is connected, and the client is not muted.
    """
    names: list[str] = []
    for group in groups:
        if group.get("stream_id") != house_stream:
            continue
        if group.get("muted", False):
            continue
        for client in group.get("clients", []):
            if not client.get("connected", False):
                continue
            config = client.get("config", {})
            if config.get("volume", {}).get("muted", False):
                continue
            name = config.get("name") or client.get("host", {}).get("name", "")
            if name:
                names.append(name)
    return names


class Tracker:
    """Folds snapserver state into the now-playing and play-history shapes."""

    def __init__(
        self,
        house_stream: str,
        history: list[dict],
        *,
        hold_s: float = 600,
        count_after_s: float = 30,
        max_plays: int = 50,
        keep_art: Callable[[str | None], str | None] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._house_stream = house_stream
        self._hold_s = hold_s
        self._count_after_s = count_after_s
        self._max_plays = max_plays
        self._keep_art = keep_art if keep_art is not None else (lambda url: url)
        self._clock = clock
        self._history: list[dict] = list(history)

        # Last known House playback status and whether the stream is idle. The
        # published status is playing only when playback is playing and the
        # stream is not idle. Idle is known only from Server.GetStatus, so it
        # stays last-known between notifications.
        self._playback_status: str | None = None
        self._stream_idle = True
        self._status = "paused"

        # Last known source and rooms, both from Server.GetStatus.
        self._source: str | None = None
        self._rooms: list[str] = []

        # Current track identity and its metadata. ``_track_present`` is False
        # before the first title and after a hold clears the track.
        self._current_key: TrackKey | None = None
        self._track_present = False
        self._kind: str | None = None
        self._duration: float | None = None
        self._genre: list[str] | None = None
        self._art_url: str | None = None
        self._started_at: datetime | None = None

        # Position anchor: the value last received and when it arrived.
        self._anchor_pos: float | None = None
        self._anchor_at: datetime | None = None

        # Play counting. ``_play_s`` accumulates only playing seconds for the
        # current identity; a play is recorded once it crosses the threshold.
        self._play_s = 0.0
        self._play_mark: datetime | None = None
        self._counted = False

        # When the current track went continuously not-playing, for the hold.
        self._not_playing_since: datetime | None = None

        # Art-lookup bookkeeping for the current play. A request is identified by
        # its route signature (key, kind, genre): the track key plus what decides
        # the lookup route. When the route changes because later metadata fills in
        # a kind or genre, a fresh request goes out. A lookup that comes back
        # empty is retried after a back-off, up to _MAX_ART_ATTEMPTS per play.
        self._art_signature: tuple | None = None
        self._art_pending = False
        self._art_attempts = 0
        self._art_retry_at: float | None = None

        self.now_playing_changed = False
        self.history_changed = False

    # -- public inputs ----------------------------------------------------

    def on_server(self, server: dict, now: datetime) -> None:
        """Fold a ``Server.GetStatus`` server object into the state."""
        before = self._core()
        self._accrue_and_count(now)
        self._apply_server(server, now)
        self._hold_check(now)
        if self._core() != before:
            self.now_playing_changed = True

    def on_properties(self, stream_id: str, properties: dict, now: datetime) -> None:
        """Fold a ``Stream.OnProperties`` notification into the state."""
        if stream_id != self._house_stream:
            return
        before = self._core()
        self._accrue_and_count(now)
        self._ingest_props(properties, now)
        self._hold_check(now)
        if self._core() != before:
            self.now_playing_changed = True

    def tick(self, now: datetime) -> None:
        """Advance time so a paused track can clear and a play can be recorded."""
        before = self._core()
        self._accrue_and_count(now)
        self._hold_check(now)
        if self._core() != before:
            self.now_playing_changed = True

    def set_art(self, key: TrackKey, art_url: str) -> None:
        """Record a resolved cover for a track key.

        Updates the now-playing cover when the key is current, and fills the
        cover into any history entry for that key that still lacks one. The
        history copy goes through ``keep_art`` (a durable history path); the
        now-playing doc keeps the raw cover path.
        """
        before = self._core()
        if self._track_present and self._current_key == key:
            self._art_url = art_url
            # The lookup for this track resolved; clear the in-flight flag so the
            # retry bookkeeping does not treat a later empty result as pending.
            self._art_pending = False
        for play in self._history:
            if _key_of(play) == key and not play.get("art_url"):
                play["art_url"] = self._keep_art(art_url)
                self.history_changed = True
        if self._core() != before:
            self.now_playing_changed = True

    # -- public outputs ---------------------------------------------------

    def wants_art(self) -> tuple[TrackKey, str, list[str] | None] | None:
        """A current track that needs a cover lookup started, else ``None``.

        Returns ``(key, kind, genre)`` for the runtime to start a background
        lookup. A request is handed out at first sight of a route signature
        ``(key, kind, genre)`` and again whenever that signature changes (later
        metadata fills in the kind or genre and the track still has no cover). A
        lookup that came back empty, reported through ``art_missing``, is handed
        out again after a back-off, up to ``_MAX_ART_ATTEMPTS`` per play.
        """
        if not self._track_present or self._current_key is None:
            return None
        if self._art_url:
            return None

        signature = _route_signature(self._current_key, self._kind or "music", self._genre)
        if signature != self._art_signature:
            # A new track, or the route changed because later metadata arrived:
            # start a fresh attempt sequence for this signature.
            self._art_signature = signature
            self._art_pending = False
            self._art_attempts = 0
            self._art_retry_at = None

        if self._art_pending:
            return None  # a request is out; wait for its result
        if self._art_attempts == 0:
            return self._hand_out_art()
        if self._art_attempts >= _MAX_ART_ATTEMPTS:
            return None
        if self._art_retry_at is None or self._clock() < self._art_retry_at:
            return None
        return self._hand_out_art()

    def _hand_out_art(self) -> tuple[TrackKey, str, list[str] | None]:
        self._art_attempts += 1
        self._art_pending = True
        assert self._current_key is not None
        return (self._current_key, self._kind or "music", self._genre)

    def art_missing(self, key: TrackKey, signature: tuple | None = None) -> None:
        """Record that the lookup for ``key`` returned no cover.

        Clears the in-flight flag and, while attempts remain for the current
        signature, schedules the next retry after the back-off. When the caller
        passes the ``signature`` the result belongs to (the runtime does) and the
        current track has since moved to a different signature, the stale result
        is ignored: it neither clears the live request's in-flight flag nor
        starts a back-off against the wrong signature. The newer signature then
        starts fresh on the next ``wants_art``.
        """
        if not self._art_pending:
            return
        if signature is not None and signature != self._art_signature:
            return
        self._art_pending = False
        if self._art_signature is None or self._art_signature[0] != key:
            return
        index = self._art_attempts - 1
        if 0 <= index < len(_ART_RETRY_BACKOFF_S):
            self._art_retry_at = self._clock() + _ART_RETRY_BACKOFF_S[index]
        else:
            self._art_retry_at = None

    def now_playing_doc(self, now: datetime) -> dict:
        """The ``now-playing.json`` shape at ``now``."""
        if not self._track_present or self._current_key is None:
            track = None
        else:
            title, artist, album = self._current_key
            track = {
                "title": title,
                "artist": artist,
                "album": album,
                "kind": self._kind,
                "genre": self._genre,
                "source": self._source,
                "status": self._status,
                "duration": self._duration,
                "position": self._anchor_pos,
                "position_at": _iso(self._anchor_at),
                "started_at": _iso(self._started_at),
                "art_url": self._art_url,
            }
        return {
            "updated_at": now.isoformat(),
            "track": track,
            "rooms": list(self._rooms),
        }

    def history_doc(self) -> dict:
        """The ``play-history.json`` shape, newest first."""
        return {"plays": [dict(play) for play in self._history]}

    # -- internals --------------------------------------------------------

    def _apply_server(self, server: dict, now: datetime) -> None:
        streams = server.get("streams", [])
        groups = server.get("groups", [])
        by_id = {stream.get("id"): stream for stream in streams}

        house = by_id.get(self._house_stream)
        self._stream_idle = house is None or house.get("status") == "idle"

        for stream_id, source_name in _HOUSE_INPUTS:
            stream = by_id.get(stream_id)
            if stream is not None and stream.get("status") == "playing":
                self._source = source_name
                break

        self._rooms = _rooms_for(groups, self._house_stream)

        properties = (house or {}).get("properties", {}) or {}
        self._ingest_props(properties, now, from_snapshot=True)

    def _ingest_props(
        self, properties: dict, now: datetime, *, from_snapshot: bool = False
    ) -> None:
        playback = properties.get("playbackStatus")
        if playback is not None:
            self._playback_status = playback

        info = _parse_track(properties)
        if info is not None:
            new_track = info.key != self._current_key
            if new_track:
                self._on_new_key(info.key, now)
            self._current_key = info.key
            self._track_present = True
            self._kind = classify(info.duration, info.genre)
            self._duration = info.duration
            self._genre = info.genre
            # The cover from the source (AirPlay carries one; Bluetooth does
            # not) replaces the current cover only on a new track or when the
            # update actually carries an artUrl. A looked-up cover from set_art
            # then survives the source's position, status, and metadata resends
            # for the same track, which otherwise carry art None and would wipe
            # it within seconds.
            if new_track or info.art is not None:
                self._art_url = info.art

        if "position" in properties:
            # A Stream.OnProperties notification carries the position
            # extrapolated to its emit time (Task 1), so it anchors on every
            # update. A Server.GetStatus snapshot returns the properties exactly
            # as last emitted, so its position is stale (possibly minutes old);
            # it only seeds an anchor when the current key has none yet (service
            # start, reconnect, or a key change), never re-anchoring an already
            # anchored track, which would move the bar backward on an unrelated
            # refresh (Review Focus 1).
            if not from_snapshot or self._anchor_pos is None:
                self._anchor_pos = properties["position"]
                self._anchor_at = now

        self._update_status(now)

    def _on_new_key(self, new_key: TrackKey, now: datetime) -> None:
        # A real track change: reset the play clock and the position anchor, and
        # decide this track's started_at. If the newest history entry is the same
        # identity and started less than its duration plus the hold window ago,
        # this is the same play resuming (or a mid-track restart): keep its
        # started_at and do not record it a second time.
        self._play_s = 0.0
        self._anchor_pos = None
        self._anchor_at = None

        # A new identity resets the art-lookup bookkeeping (signature, in-flight
        # flag, attempt budget, and retry clock), so a track that replays (its
        # key becomes current again) asks for art once more and gets a fresh set
        # of attempts. ArtLookup answers a repeat request from its own disk
        # cache, so this costs no network call for a track whose cover was
        # already resolved, and recovers one for a no-art track that had nothing
        # to show before.
        self._art_signature = None
        self._art_pending = False
        self._art_attempts = 0
        self._art_retry_at = None

        newest = self._history[0] if self._history else None
        if newest is not None and _key_of(newest) == new_key:
            started = _parse_dt(newest.get("started_at"))
            window = (newest.get("duration") or 0) + self._hold_s
            if started is not None and (now - started).total_seconds() < window:
                self._started_at = started
                self._counted = True
                return

        self._started_at = now
        self._counted = False

    def _update_status(self, now: datetime) -> None:
        playing = self._playback_status == "playing" and not self._stream_idle
        status = "playing" if playing else "paused"
        if status == "playing":
            self._not_playing_since = None
        elif self._not_playing_since is None:
            self._not_playing_since = now
        self._status = status

    def _accrue_and_count(self, now: datetime) -> None:
        if (
            self._play_mark is not None
            and self._status == "playing"
            and self._current_key is not None
        ):
            self._play_s += (now - self._play_mark).total_seconds()
        self._maybe_count()
        self._play_mark = now

    def _maybe_count(self) -> None:
        if not self._track_present or self._current_key is None:
            return
        if self._counted or self._play_s < self._count_after_s:
            return
        title, artist, album = self._current_key
        play = {
            "title": title,
            "artist": artist,
            "album": album,
            "kind": self._kind,
            "genre": self._genre,
            "source": self._source,
            "duration": self._duration,
            "started_at": _iso(self._started_at),
            "art_url": self._keep_art(self._art_url),
        }
        self._history.insert(0, play)
        del self._history[self._max_plays :]
        self._counted = True
        self.history_changed = True

    def _hold_check(self, now: datetime) -> None:
        if not self._track_present or self._not_playing_since is None:
            return
        if (now - self._not_playing_since).total_seconds() >= self._hold_s:
            self._track_present = False
            self._current_key = None
            self._kind = None
            self._duration = None
            self._genre = None
            self._art_url = None
            self._started_at = None
            self._anchor_pos = None
            self._anchor_at = None
            self._play_s = 0.0
            self._counted = False

    def _core(self):
        # The published now-playing state, minus ``updated_at``, used to decide
        # whether a write is needed. Position shows the anchor value, which only
        # moves on a position-carrying update, so a plain tick stays clean.
        if not self._track_present or self._current_key is None:
            track = None
        else:
            title, artist, album = self._current_key
            track = (
                title,
                artist,
                album,
                self._kind,
                tuple(self._genre) if self._genre else None,
                self._source,
                self._status,
                self._duration,
                self._anchor_pos,
                _iso(self._anchor_at),
                _iso(self._started_at),
                self._art_url,
            )
        return (track, tuple(self._rooms))


def _key_of(play: dict) -> TrackKey:
    return (play.get("title"), play.get("artist"), play.get("album"))


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


# -- runtime --------------------------------------------------------------- #

# Notifications that mean the server state the tracker reads (streams, groups,
# clients, House properties) may have changed, so the service re-requests
# Server.GetStatus. Client.* and Group.* are matched by prefix as well.
_RESTATUS_METHODS = frozenset({"Stream.OnUpdate", "Server.OnUpdate"})


def load_history(path: Path, log: logging.Logger) -> list[dict]:
    """Load the plays list from ``play-history.json`` at start, or an empty list.

    A missing file is a fresh start and is silent. A present but corrupt file
    (bad JSON, or no plays list) logs a warning and starts empty, so a half
    written file from a crash never stops the service from coming up.
    """
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        return []
    try:
        data = json.loads(text)
    except ValueError:
        log.warning("%s is corrupt; starting with an empty history", path)
        return []
    plays = data.get("plays") if isinstance(data, dict) else None
    if not isinstance(plays, list):
        log.warning("%s has no plays list; starting with an empty history", path)
        return []
    return [play for play in plays if isinstance(play, dict)]


def _write_json_atomic(path: Path, document: dict) -> None:
    """Write a document to a public web-root path atomically and world-readable.

    The same pattern as housestatus.py: a temp file in the same directory, fsync,
    widen to 0644 so nginx (www-data) can serve it, then ``os.replace`` over the
    destination, so a polling reader never sees a partial file.
    """
    path = Path(path)
    payload = json.dumps(document, indent=2) + "\n"
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
            os.fchmod(handle.fileno(), 0o644)
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


class Service:
    """The now-playing runtime: snapserver control socket plus file writes.

    One socket follows the House stream. ``Server.GetStatus`` is sent on connect
    and re-requested (coalesced to at most one per cycle) whenever a notification
    says the server state may have changed. ``Stream.OnProperties`` for House is
    applied straight to the tracker; for an input stream it also triggers a
    status refresh, so House's forwarded properties are picked up either way.
    Art lookups run on one background worker so the socket loop never blocks on
    the network, and both files are written atomically when the tracker is dirty.
    """

    def __init__(
        self,
        *,
        host: str,
        port: int,
        public_root: Path,
        house_stream: str,
        tracker: Tracker,
        art_lookup,
        now: Callable[[], datetime] | None = None,
        log: logging.Logger | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        tick_interval: float = 1.0,
        socket_timeout: float = 1.0,
        connect_timeout: float = 5.0,
        backoff_start: float = 1.0,
        backoff_cap: float = 30.0,
        worker_poll: float = 0.2,
    ) -> None:
        self._host = host
        self._port = port
        self._public_root = Path(public_root)
        self._house_stream = house_stream
        self._tracker = tracker
        self._art_lookup = art_lookup
        self._now = now if now is not None else (lambda: datetime.now().astimezone())
        self._log = log if log is not None else logging.getLogger("snaphouse.nowplaying")
        self._monotonic = monotonic
        self._sleep = sleep
        self._tick_interval = tick_interval
        self._socket_timeout = socket_timeout
        self._connect_timeout = connect_timeout
        self._backoff_start = backoff_start
        self._backoff_cap = backoff_cap
        self._worker_poll = worker_poll

        # The two JSON files live in a dedicated subdirectory of the public root
        # (owned by snapserver on the hub), so the service never needs to own the
        # shared public web root. nginx serves them same-origin from here. Covers
        # stay under public_root/cover, which snapserver already owns.
        self._state_dir = self._public_root / "nowplaying"
        self._now_playing_path = self._state_dir / "now-playing.json"
        self._history_path = self._state_dir / "play-history.json"

        self._sock: socket.socket | None = None
        self._req_id = 0
        self._need_status = False
        self._backoff = backoff_start
        self._last_tick = monotonic()

        self._stop = threading.Event()
        self._art_queue: "queue.Queue[tuple[TrackKey, str, list[str] | None]]" = queue.Queue()
        self._art_results: (
            "queue.Queue[tuple[TrackKey, str, list[str] | None, str | None]]"
        ) = queue.Queue()
        self._worker_thread: threading.Thread | None = None

    # -- lifecycle --------------------------------------------------------

    def run(self) -> None:
        """Write the files once, then follow snapserver until stopped."""
        # On the hub this directory already exists (ansible creates it owned by
        # snapserver), so this is a no-op; off-hub and in tests it is created.
        self._state_dir.mkdir(parents=True, exist_ok=True)
        self._write_all(self._now())
        self._start_worker()
        try:
            while not self._stop.is_set():
                try:
                    self._connect()
                    self._run_connected()
                except OSError as exc:
                    if self._stop.is_set():
                        break
                    self._log.warning(
                        "snapserver connection lost (%s); reconnecting in %.0fs",
                        exc,
                        self._backoff,
                    )
                    self._sleep(self._backoff)
                    self._backoff = min(self._backoff * 2, self._backoff_cap)
                finally:
                    self._close_sock()
        finally:
            self._stop_worker()
            self._close_sock()

    def stop(self) -> None:
        """Ask the service to finish the current cycle and return from ``run``."""
        self._stop.set()

    # -- socket loop ------------------------------------------------------

    def _connect(self) -> None:
        self._sock = socket.create_connection(
            (self._host, self._port), timeout=self._connect_timeout
        )
        self._sock.settimeout(self._socket_timeout)

    def _run_connected(self) -> None:
        self._backoff = self._backoff_start
        self._need_status = False
        self._last_tick = self._monotonic()
        self._send_get_status()
        buffer = b""
        while not self._stop.is_set():
            try:
                chunk = self._sock.recv(65536)
            except (socket.timeout, TimeoutError):
                chunk = None
            if chunk == b"":
                raise ConnectionError("snapserver closed the connection")
            if chunk:
                buffer += chunk
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    line = line.strip()
                    if line:
                        self._handle_message(line)
            self._periodic()

    def _periodic(self) -> None:
        if self._need_status:
            self._need_status = False
            self._send_get_status()
        now_mono = self._monotonic()
        if now_mono - self._last_tick >= self._tick_interval:
            self._tracker.tick(self._now())
            self._last_tick = now_mono
        self._drain_art_results()
        self._pump_art_requests()
        self._write_if_dirty(self._now())

    def _handle_message(self, line: bytes) -> None:
        try:
            message = json.loads(line)
        except ValueError:
            self._log.warning("ignoring a malformed snapserver message")
            return
        if not isinstance(message, dict):
            return
        if "result" in message:
            result = message["result"]
            if isinstance(result, dict) and isinstance(result.get("server"), dict):
                self._tracker.on_server(result["server"], self._now())
            return
        method = message.get("method")
        if not isinstance(method, str):
            return
        params = message.get("params") or {}
        if method == "Stream.OnProperties":
            stream_id = params.get("id")
            properties = params.get("properties") or {}
            if isinstance(stream_id, str) and isinstance(properties, dict):
                self._tracker.on_properties(stream_id, properties, self._now())
                if stream_id != self._house_stream:
                    # An input stream's properties changed; the House meta stream
                    # forwards from it, so re-read the full status to be sure.
                    self._need_status = True
            return
        if method in _RESTATUS_METHODS or method.startswith(("Client.", "Group.")):
            self._need_status = True

    def _send_get_status(self) -> None:
        self._req_id += 1
        self._send({"id": self._req_id, "jsonrpc": "2.0", "method": "Server.GetStatus"})

    def _send(self, obj: dict) -> None:
        assert self._sock is not None
        self._sock.sendall((json.dumps(obj) + "\n").encode("utf-8"))

    def _close_sock(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None

    # -- art worker -------------------------------------------------------

    def _start_worker(self) -> None:
        self._worker_thread = threading.Thread(
            target=self._worker, name="art-lookup", daemon=True
        )
        self._worker_thread.start()

    def _stop_worker(self) -> None:
        if self._worker_thread is not None:
            self._worker_thread.join(timeout=2.0)
            self._worker_thread = None

    def _worker(self) -> None:
        while not self._stop.is_set():
            try:
                key, kind, genre = self._art_queue.get(timeout=self._worker_poll)
            except queue.Empty:
                continue
            title, artist, album = key
            try:
                url = self._art_lookup.find(title, artist, album, kind, genre)
            except Exception:
                self._log.exception("art lookup raised for %r", key)
                url = None
            self._art_results.put((key, kind, genre, url))

    def _pump_art_requests(self) -> None:
        request = self._tracker.wants_art()
        if request is not None:
            key, kind, genre = request
            self._art_queue.put((key, kind, genre))

    def _drain_art_results(self) -> None:
        while True:
            try:
                key, kind, genre, url = self._art_results.get_nowait()
            except queue.Empty:
                return
            if url:
                self._tracker.set_art(key, url)
            else:
                self._tracker.art_missing(key, _route_signature(key, kind, genre))

    # -- writing ----------------------------------------------------------

    def _write_all(self, now: datetime) -> None:
        self._write_now_playing(now)
        self._write_history()
        self._tracker.now_playing_changed = False
        self._tracker.history_changed = False

    def _write_if_dirty(self, now: datetime) -> None:
        if self._tracker.now_playing_changed:
            self._write_now_playing(now)
            self._tracker.now_playing_changed = False
        if self._tracker.history_changed:
            self._write_history()
            self._tracker.history_changed = False

    def _write_now_playing(self, now: datetime) -> None:
        _write_json_atomic(self._now_playing_path, self._tracker.now_playing_doc(now))

    def _write_history(self) -> None:
        document = self._tracker.history_doc()
        _write_json_atomic(self._history_path, document)
        keep = {
            play["art_url"]
            for play in document["plays"]
            if play.get("art_url")
        }
        prune_history_covers(self._public_root, keep)


def main(argv: Optional[list[str]] = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    parser = argparse.ArgumentParser(
        prog="snaphouse.nowplaying",
        description="Follow the House stream and publish now-playing.json (hub).",
    )
    parser.add_argument("--snapserver-host", default="localhost")
    parser.add_argument("--port", type=int, default=1705)
    parser.add_argument("--public-root", default="/var/lib/snaphouse/public")
    parser.add_argument("--house-stream", default="House")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    log = logging.getLogger("snaphouse.nowplaying")

    public_root = Path(args.public_root)
    history = load_history(public_root / "nowplaying" / "play-history.json", log)
    tracker = Tracker(
        args.house_stream,
        history,
        keep_art=lambda url: keep_for_history(url, public_root),
    )
    art_lookup = ArtLookup(public_root / "cover", urllib_fetch)
    service = Service(
        host=args.snapserver_host,
        port=args.port,
        public_root=public_root,
        house_stream=args.house_stream,
        tracker=tracker,
        art_lookup=art_lookup,
        log=log,
    )
    service.run()
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
