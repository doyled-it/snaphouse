"""The hub's frame control core: now-playing settings, windows, and the pin.

The art admin service (``snaphouse.artadmin``) owns frame control alongside the
art library and publishes it as ``/frame-control.json``. This module is that
state and the operations on it, with no HTTP in sight: which kinds of audio take
the now-playing screen, the quiet-hours and morning-weather windows, and a pin
that holds the frame on the current art piece, the weather, or now playing for a
while or until it is cleared.

Like ``snaphouse.artlib`` it is deliberately HTTP-free. Every method takes plain
values and raises ``snaphouse.artlib.ArtError(status, message)`` whose ``message``
is safe to show on the page as-is, so the admin service turns one into a JSON
error exactly as it does for a library error. State persists to a small JSON file
(beside the art state), so a service restart reloads the settings, the pin, and
the sequence counter. The clock is injected so the ``until`` computations and the
morning-window resolution are deterministic under test.

The published document is::

    {
      "settings": {
        "now_playing_kinds": ["music"],
        "quiet_hours": {"start": "22:00", "end": "06:00"},
        "morning_weather": {"enabled": true,
                            "weekday": ["06:00", "08:30"],
                            "weekend": ["07:00", "09:00"]}
      },
      "pin": null
    }

``pin`` is ``null`` or ``{"what", "piece", "until", "seq", "set_at"}``; the design
and the pinned contract are in
``docs/superpowers/specs/2026-11-10-frame-control-design.md`` and
``docs/cross-repo-features.md``.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import time
from datetime import datetime, time as dtime, timedelta
from pathlib import Path
from typing import Callable, Optional

from snaphouse.artlib import ArtError


# --------------------------------------------------------------------------- #
# Constants (the binding values from the design and global constraints)
# --------------------------------------------------------------------------- #

#: The three kinds of audio the now-playing screen can be enabled for. The frame
#: derives one of these from the hub's ``kind`` and ``genre``; the setting lists
#: the kinds that may take the frame. The owner's default is music only.
KINDS = ("music", "podcast", "audiobook")

#: What a pin can hold the frame on.
WHATS = ("art", "weather", "now_playing")

#: How long a pin can last. ``morning`` resolves to the next morning-window start;
#: ``forever`` has no expiry (until it is cleared).
DURATIONS = ("1h", "4h", "morning", "forever")

#: Fixed durations in seconds.
_DURATION_SECONDS = {"1h": 3600, "4h": 4 * 3600}

#: Defaults when no state exists (global constraints: quiet hours 22:00 to 06:00;
#: morning windows 06:00 to 08:30 weekdays, 07:00 to 09:00 weekends).
DEFAULT_KINDS = ["music"]
DEFAULT_QUIET_HOURS = {"start": "22:00", "end": "06:00"}
DEFAULT_MORNING_WEATHER = {
    "enabled": True,
    "weekday": ["06:00", "08:30"],
    "weekend": ["07:00", "09:00"],
}

#: A 24-hour ``HH:MM`` time, 00:00 to 23:59.
_TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


class FrameControl:
    """Frame settings and the pin, persisted to ``state`` and published as a
    ``{settings, pin}`` document.

    ``state`` is the private JSON file the settings, the pin, and the monotonic
    sequence counter persist in. ``art_history`` is the republished
    ``art-history.json`` (the "Art history file" contract); pinning ``art`` reads
    the current piece from it. ``clock`` is injected for deterministic tests.
    """

    def __init__(
        self,
        state: Path,
        *,
        art_history: Optional[Path] = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.state = Path(state)
        self.art_history = Path(art_history) if art_history is not None else None
        self._clock = clock

    # -- published document ------------------------------------------------- #

    def document(self) -> dict:
        """The ``/frame-control.json`` document: exactly ``settings`` and ``pin``.

        The private sequence counter never appears here; a frame reads only the
        settings and the pin (the pin carries its own ``seq`` when set).
        """
        state = self._load_state()
        return {"settings": state["settings"], "pin": state["pin"]}

    # -- settings ----------------------------------------------------------- #

    def update_settings(self, subset: dict) -> dict:
        """Validate a subset of ``settings`` and merge it into the stored settings.

        ``subset`` may carry any of ``now_playing_kinds``, ``quiet_hours``, and
        ``morning_weather``; a window sub-dict may itself be partial (just
        ``enabled``, or just one window). The whole subset is validated before
        anything is written, so a rejected update leaves the stored settings
        untouched. Returns the full merged settings.
        """
        if not isinstance(subset, dict):
            raise ArtError(400, "That is not a valid settings object.")
        state = self._load_state()
        settings = state["settings"]

        if "now_playing_kinds" in subset:
            settings["now_playing_kinds"] = self._clean_kinds(subset["now_playing_kinds"])
        if "quiet_hours" in subset:
            settings["quiet_hours"] = self._clean_quiet_hours(
                settings["quiet_hours"], subset["quiet_hours"]
            )
        if "morning_weather" in subset:
            settings["morning_weather"] = self._clean_morning_weather(
                settings["morning_weather"], subset["morning_weather"]
            )

        state["settings"] = settings
        self._save_state(state)
        return settings

    def _clean_kinds(self, value) -> list:
        if not isinstance(value, list):
            raise ArtError(400, "The now-playing kinds must be a list.")
        cleaned: list[str] = []
        for kind in value:
            if kind not in KINDS:
                raise ArtError(400, "That is not one of the now-playing kinds.")
            if kind not in cleaned:
                cleaned.append(kind)
        return cleaned

    def _clean_quiet_hours(self, current: dict, value) -> dict:
        if not isinstance(value, dict):
            raise ArtError(400, "The quiet hours must be a start and an end time.")
        merged = dict(current)
        for key in ("start", "end"):
            if key in value:
                merged[key] = self._valid_time(value[key])
        # Quiet hours may wrap past midnight (22:00 to 06:00), so start and end
        # have no ordering constraint; both only need to be valid times.
        return {"start": merged["start"], "end": merged["end"]}

    def _clean_morning_weather(self, current: dict, value) -> dict:
        if not isinstance(value, dict):
            raise ArtError(400, "The morning weather must be an object.")
        merged = dict(current)
        if "enabled" in value:
            if not isinstance(value["enabled"], bool):
                raise ArtError(400, "The morning-weather toggle must be true or false.")
            merged["enabled"] = value["enabled"]
        for key in ("weekday", "weekend"):
            if key in value:
                merged[key] = self._valid_window(value[key])
        return {
            "enabled": merged["enabled"],
            "weekday": merged["weekday"],
            "weekend": merged["weekend"],
        }

    def _valid_window(self, value) -> list:
        if not isinstance(value, (list, tuple)) or len(value) != 2:
            raise ArtError(400, "A morning window is a start and an end time.")
        start = self._valid_time(value[0])
        end = self._valid_time(value[1])
        if self._minutes(start) >= self._minutes(end):
            raise ArtError(400, "The morning start must be before the end.")
        return [start, end]

    @staticmethod
    def _valid_time(value) -> str:
        if not isinstance(value, str) or not _TIME_RE.match(value):
            raise ArtError(400, "That is not a valid time (use HH:MM).")
        return value

    @staticmethod
    def _minutes(hhmm: str) -> int:
        hour, minute = hhmm.split(":")
        return int(hour) * 60 + int(minute)

    # -- pin ---------------------------------------------------------------- #

    def pin(self, what, duration) -> dict:
        """Set the pin to ``what`` for ``duration``; return the stored pin.

        ``what`` is one of ``art``, ``weather``, ``now_playing`` and ``duration``
        one of ``1h``, ``4h``, ``morning``, ``forever``. Pinning ``art`` records
        the current art piece from the republished art history (``null`` when it
        is missing, malformed, or shows nothing). ``seq`` increases on every pin
        and unpin, so a frame can tell a change and redraw once.
        """
        if what not in WHATS:
            raise ArtError(400, "That is not something the frame can be pinned to.")
        if duration not in DURATIONS:
            raise ArtError(400, "That is not one of the pin durations.")

        state = self._load_state()
        piece = self._current_piece() if what == "art" else None
        seq = state["seq"] + 1
        pin = {
            "what": what,
            "piece": piece,
            "until": self._until(duration, state["settings"]),
            "seq": seq,
            "set_at": self._now_iso(),
        }
        state["pin"] = pin
        state["seq"] = seq
        self._save_state(state)
        return pin

    def unpin(self) -> None:
        """Clear the pin and advance the sequence counter; return ``None``.

        Clearing is idempotent: unpinning when nothing is pinned still advances
        the counter, so the next pin always carries a sequence a frame has not
        applied. A frame reads the cleared pin (``null``) as "no pin" and returns
        to its normal schedule.
        """
        state = self._load_state()
        state["seq"] += 1
        state["pin"] = None
        self._save_state(state)
        return None

    def _until(self, duration: str, settings: dict) -> Optional[str]:
        if duration == "forever":
            return None
        if duration == "morning":
            return self._next_morning_start(settings)
        seconds = _DURATION_SECONDS[duration]
        return self._iso(self._now() + timedelta(seconds=seconds))

    def _next_morning_start(self, settings: dict) -> str:
        """The next morning-window start, as a local ISO stamp.

        Walks forward from now: for each day the window start is the weekday or
        weekend start for that day of the week, and the first start strictly after
        now wins. Before today's start that is today; otherwise it is a later day
        (usually tomorrow, the "until tomorrow morning" choice).
        """
        now = self._now()
        morning = settings["morning_weather"]
        for offset in range(0, 8):
            day = (now + timedelta(days=offset)).date()
            window = morning["weekday"] if day.weekday() < 5 else morning["weekend"]
            hour, minute = (int(part) for part in window[0].split(":"))
            candidate = datetime.combine(day, dtime(hour, minute)).astimezone()
            if candidate > now:
                return candidate.isoformat()
        # Unreachable: some day within a week always has a start after now.
        return self._iso(now)

    def _current_piece(self) -> Optional[str]:
        """The id (or file) of the piece currently on the frame, or ``None``.

        Reads the republished art history and returns ``current.id`` when present,
        falling back to ``current.file``. A missing, unreadable, or malformed file,
        or nothing current, is ``None``, so a pin set with no art showing (or with
        the history briefly absent) still stores cleanly and the frame falls back.
        """
        if self.art_history is None:
            return None
        try:
            data = json.loads(self.art_history.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(data, dict):
            return None
        current = data.get("current")
        if not isinstance(current, dict):
            return None
        piece_id = current.get("id")
        if isinstance(piece_id, str) and piece_id:
            return piece_id
        file = current.get("file")
        if isinstance(file, str) and file:
            return file
        return None

    # -- state persistence -------------------------------------------------- #

    def _default_state(self) -> dict:
        return {
            "settings": {
                "now_playing_kinds": list(DEFAULT_KINDS),
                "quiet_hours": dict(DEFAULT_QUIET_HOURS),
                "morning_weather": {
                    "enabled": DEFAULT_MORNING_WEATHER["enabled"],
                    "weekday": list(DEFAULT_MORNING_WEATHER["weekday"]),
                    "weekend": list(DEFAULT_MORNING_WEATHER["weekend"]),
                },
            },
            "pin": None,
            "seq": 0,
        }

    def _load_state(self) -> dict:
        try:
            raw = self.state.read_text(encoding="utf-8")
            data = json.loads(raw)
        except (OSError, ValueError):
            return self._default_state()
        if not isinstance(data, dict):
            return self._default_state()
        return self._normalize_state(data)

    def _normalize_state(self, data: dict) -> dict:
        """Coerce a stored document into a full, trusted state.

        Values written here were validated on the way in, but the file could have
        been hand-edited or written by an older version, so each piece is checked
        and anything unusable falls back to the default. This keeps a bad file
        from ever crashing a request; it reads as defaults instead.
        """
        state = self._default_state()
        settings = data.get("settings")
        if isinstance(settings, dict):
            kinds = settings.get("now_playing_kinds")
            if isinstance(kinds, list):
                state["settings"]["now_playing_kinds"] = [
                    k for k in kinds if k in KINDS
                ]
            state["settings"]["quiet_hours"] = self._normalize_quiet_hours(
                settings.get("quiet_hours")
            )
            state["settings"]["morning_weather"] = self._normalize_morning_weather(
                settings.get("morning_weather")
            )
        pin = data.get("pin")
        if isinstance(pin, dict) and pin.get("what") in WHATS:
            state["pin"] = pin
        seq = data.get("seq")
        if isinstance(seq, int) and not isinstance(seq, bool) and seq >= 0:
            state["seq"] = seq
        # The counter must never fall behind a stored pin's seq, or a reissued
        # pin could repeat a number a frame already applied.
        if isinstance(state["pin"], dict):
            pin_seq = state["pin"].get("seq")
            if isinstance(pin_seq, int) and not isinstance(pin_seq, bool):
                state["seq"] = max(state["seq"], pin_seq)
        return state

    def _normalize_quiet_hours(self, value) -> dict:
        result = dict(DEFAULT_QUIET_HOURS)
        if isinstance(value, dict):
            for key in ("start", "end"):
                candidate = value.get(key)
                if isinstance(candidate, str) and _TIME_RE.match(candidate):
                    result[key] = candidate
        return result

    def _normalize_morning_weather(self, value) -> dict:
        result = {
            "enabled": DEFAULT_MORNING_WEATHER["enabled"],
            "weekday": list(DEFAULT_MORNING_WEATHER["weekday"]),
            "weekend": list(DEFAULT_MORNING_WEATHER["weekend"]),
        }
        if isinstance(value, dict):
            if isinstance(value.get("enabled"), bool):
                result["enabled"] = value["enabled"]
            for key in ("weekday", "weekend"):
                window = value.get(key)
                if (
                    isinstance(window, (list, tuple))
                    and len(window) == 2
                    and all(isinstance(t, str) and _TIME_RE.match(t) for t in window)
                    and self._minutes(window[0]) < self._minutes(window[1])
                ):
                    result[key] = [window[0], window[1]]
        return result

    def _save_state(self, state: dict) -> None:
        self.state.parent.mkdir(parents=True, exist_ok=True)
        data = json.dumps(state, indent=2).encode("utf-8")
        directory = self.state.parent
        fd, tmp_name = tempfile.mkstemp(dir=directory, prefix=".frame.", suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
                os.fchmod(handle.fileno(), 0o644)
            os.replace(tmp_name, self.state)
        except BaseException:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise

    # -- clock -------------------------------------------------------------- #

    def _now(self) -> datetime:
        return datetime.fromtimestamp(self._clock()).astimezone()

    def _now_iso(self) -> str:
        return self._now().isoformat()

    @staticmethod
    def _iso(moment: datetime) -> str:
        return moment.astimezone().isoformat()
