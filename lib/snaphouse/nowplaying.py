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
history threshold without a snapserver event. The module does no I/O: the
runtime writes the files and clears the ``now_playing_changed`` and
``history_changed`` flags after each write.

Position carries Task 1 semantics. Every properties update that carries
``position`` already holds the value extrapolated to the moment of emit (frozen
while paused), so the tracker anchors ``(position, arrival time)`` on each one
with no stale-value filtering. Consumers extrapolate from that anchor.
"""

from __future__ import annotations

from collections import namedtuple
from datetime import datetime
from typing import Callable, Optional
from urllib.parse import urlsplit

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
    URL under ``/cover/`` becomes its path, a path that is already same-origin
    passes through, and anything else (a foreign host, a non-cover path) becomes
    ``None`` so no off-origin or unexpected reference ever ships.
    """
    if url is None:
        return None
    url = url.strip()
    if not url:
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
    ) -> None:
        self._house_stream = house_stream
        self._hold_s = hold_s
        self._count_after_s = count_after_s
        self._max_plays = max_plays
        self._keep_art = keep_art if keep_art is not None else (lambda url: url)
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

        # Track keys a cover lookup has already been requested for.
        self._art_wanted: set[TrackKey] = set()

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
        for play in self._history:
            if _key_of(play) == key and not play.get("art_url"):
                play["art_url"] = self._keep_art(art_url)
                self.history_changed = True
        if self._core() != before:
            self.now_playing_changed = True

    # -- public outputs ---------------------------------------------------

    def wants_art(self) -> tuple[TrackKey, str, list[str] | None] | None:
        """A current track with no cover and no lookup yet, else ``None``.

        Returns ``(key, kind, genre)`` for the runtime to start a background
        lookup, and marks the key so it is handed out only once.
        """
        if not self._track_present or self._current_key is None:
            return None
        if self._art_url:
            return None
        if self._current_key in self._art_wanted:
            return None
        self._art_wanted.add(self._current_key)
        return (self._current_key, self._kind or "music", self._genre)

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
        self._ingest_props(properties, now)

    def _ingest_props(self, properties: dict, now: datetime) -> None:
        playback = properties.get("playbackStatus")
        if playback is not None:
            self._playback_status = playback

        info = _parse_track(properties)
        if info is not None:
            if info.key != self._current_key:
                self._on_new_key(info.key, now)
            self._current_key = info.key
            self._track_present = True
            self._kind = classify(info.duration, info.genre)
            self._duration = info.duration
            self._genre = info.genre
            self._art_url = info.art

        if "position" in properties:
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
