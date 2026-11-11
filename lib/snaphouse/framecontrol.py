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
                            "weekend": ["07:00", "09:00"]},
        "weather_location": null
      },
      "pin": null
    }

``weather_location`` is ``null`` or ``{"name", "latitude", "longitude",
"timezone"}``: the display name, city-centre coordinates rounded to two decimals,
and the IANA timezone the hub resolved from a typed city via Open-Meteo's keyless
geocoding API. The frame uses it for the forecast and falls back to its own local
config when it is ``null``.

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
import urllib.error
import urllib.parse
import urllib.request
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


# --------------------------------------------------------------------------- #
# Geocoding (Open-Meteo, keyless)
# --------------------------------------------------------------------------- #

#: Open-Meteo's keyless geocoding search. Only the typed city name is sent, with
#: a fixed count, language, and format; the qualifier (state or country) stays on
#: the hub and is used to pick among the results. Nothing identifying goes out.
_GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"

#: A geocoding response is small JSON; refuse a larger body so a broken or hostile
#: source cannot stream an unbounded response into memory on the appliance.
_GEOCODE_MAX_BYTES = 1 * 1024 * 1024
_GEOCODE_TIMEOUT = 10.0

#: The longest typed city query accepted. A city name is short; a longer string
#: is a mistake or abuse, refused with a 400 before anything is geocoded rather
#: than sent on to the geocoder as the search term.
_MAX_QUERY_LENGTH = 100

#: Shown when the geocoder could not be reached or returned something unusable
#: (a transient condition the owner can retry), distinct from a clean no-match.
_GEOCODER_UNREACHABLE = "Could not look up places right now. Try again in a minute."

#: US state and territory postal codes, so a "City, CA" style qualifier matches a
#: result whose ``admin1`` is the full state name. Open-Meteo returns the full
#: name ("California"), not the code, so the hub maps the two together.
_US_STATE_CODES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas",
    "CA": "California", "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware",
    "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho",
    "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas",
    "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland",
    "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi",
    "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
    "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York",
    "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma",
    "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina",
    "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas", "UT": "Utah",
    "VT": "Vermont", "VA": "Virginia", "WA": "Washington", "WV": "West Virginia",
    "WI": "Wisconsin", "WY": "Wyoming", "DC": "District of Columbia",
}
#: The reverse map (full name -> postal code), case-folded for comparison.
_US_STATE_NAME_TO_CODE = {
    name.casefold(): code for code, name in _US_STATE_CODES.items()
}

#: A fetch gets a URL and returns the response body, or None on any failure (a bad
#: status, a timeout, a transport error, or an oversized body). The caller tells a
#: failed fetch apart from a parsed no-match, so the two map to different errors.
Fetch = Callable[[str], Optional[bytes]]

_user_agent_cache: Optional[str] = None


def _user_agent() -> str:
    """A User-Agent naming the public repository, nothing identifying."""
    global _user_agent_cache
    if _user_agent_cache is None:
        _user_agent_cache = "snaphouse (+https://github.com/doyled-it/snaphouse)"
    return _user_agent_cache


def urllib_geocode(url: str) -> Optional[bytes]:
    """GET ``url`` with a short timeout, returning the body or None on failure.

    The body is capped and compression refused so a small compressed response
    cannot amplify past the cap. Any error (bad status, timeout, transport
    failure, oversize body) resolves to None, so the geocode call never raises
    into the request handler; a None body becomes a 502 the owner can retry.
    """
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": _user_agent(),
            "Accept-Encoding": "identity",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=_GEOCODE_TIMEOUT) as response:
            data = response.read(_GEOCODE_MAX_BYTES + 1)
    except (urllib.error.URLError, OSError, ValueError):
        return None
    if len(data) > _GEOCODE_MAX_BYTES:
        return None
    return data


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
        geocode: Optional[Fetch] = None,
    ) -> None:
        self.state = Path(state)
        self.art_history = Path(art_history) if art_history is not None else None
        self._clock = clock
        # The geocoder fetch (url -> body bytes or None) is injected so tests use
        # recorded or synthetic responses and no test touches the network. The
        # module stays HTTP-free; ``urllib_geocode`` is the real one, wired in by
        # the service. A weather_location write with no geocoder set is a 502.
        self._geocode = geocode

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
        if "weather_location" in subset:
            settings["weather_location"] = self._clean_weather_location(
                subset["weather_location"]
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

    # -- weather location --------------------------------------------------- #

    def prepare_settings(self, subset: dict) -> dict:
        """Resolve a settings subset's network-bound parts before it is stored.

        The weather geocode is the only slow, network-bound step in a settings
        write, and the service must run it outside its library write lock (so a
        slow geocoder never stalls the thumbnail pass or another write). This does
        that resolution: it returns a shallow copy of ``subset`` in which a
        ``weather_location`` write of the form ``{"query": "<city>"}`` is replaced
        by the resolved location (the geocode happens here) and ``None`` is left
        as ``None``. Every other key is passed through untouched for
        :meth:`update_settings` to validate and store under the lock. A write with
        no ``weather_location`` key needs no network and is returned as-is.
        """
        if not isinstance(subset, dict):
            raise ArtError(400, "That is not a valid settings object.")
        if "weather_location" not in subset:
            return dict(subset)
        prepared = dict(subset)
        prepared["weather_location"] = self._resolve_weather_location(
            subset["weather_location"]
        )
        return prepared

    def _resolve_weather_location(self, value):
        """Resolve a weather-location write to its stored form (the geocode).

        ``None`` clears the location; ``{"query": "<city>"}`` is geocoded to the
        stored location dict. Any other shape is a 400, matching what
        :meth:`update_settings` would reject, so a client posting a pre-resolved
        location (not via ``query``) is refused rather than trusted.
        """
        if value is None:
            return None
        if not isinstance(value, dict) or "query" not in value:
            raise ArtError(400, "That is not a valid weather location.")
        return self._geocode_query(self._clean_query(value["query"]))

    def _clean_weather_location(self, value):
        """Validate a weather-location value and return its stored form.

        ``None`` clears it. A ``{"query": "<city>"}`` is geocoded (a direct call;
        the service resolves it in :meth:`prepare_settings` outside the lock, so
        this path does no network there). An already-resolved location dict is
        validated to the stored shape and kept. Anything else is a 400.
        """
        if value is None:
            return None
        if not isinstance(value, dict):
            raise ArtError(400, "That is not a valid weather location.")
        if "query" in value:
            return self._geocode_query(self._clean_query(value["query"]))
        resolved = _normalize_weather_location(value)
        if resolved is None:
            raise ArtError(400, "That is not a valid weather location.")
        return resolved

    @staticmethod
    def _clean_query(query) -> str:
        """A typed city query, trimmed, non-blank, and within the length cap."""
        if not isinstance(query, str) or not query.strip():
            raise ArtError(400, 'Enter a city, like "San Diego, CA".')
        trimmed = query.strip()
        if len(trimmed) > _MAX_QUERY_LENGTH:
            raise ArtError(400, "That city name is too long. Use a shorter city name.")
        return trimmed

    def _geocode_query(self, query: str) -> dict:
        """Geocode a typed ``City`` or ``City, Qualifier`` to a stored location.

        The query is split at the first comma: the city part is the only thing
        that leaves the LAN (the search term), and the qualifier (a state or
        country) stays on the hub to pick among the results. A failed or
        unparseable fetch is a 502 (retryable); a search that parsed but matched
        nothing is a 400 naming the query.
        """
        if self._geocode is None:
            raise ArtError(502, _GEOCODER_UNREACHABLE)
        city, qualifier = _split_query(query)
        if not city:
            raise ArtError(400, _no_place_message(query))
        url = _GEOCODE_URL + "?" + urllib.parse.urlencode(
            {"name": city, "count": 10, "language": "en", "format": "json"}
        )
        results = _geocode_results(self._geocode(url))
        if results is None:
            raise ArtError(502, _GEOCODER_UNREACHABLE)
        chosen = _choose_place(results, qualifier)
        if chosen is None:
            raise ArtError(400, _no_place_message(query))
        location = _build_location(chosen)
        if location is None:
            # The geocoder matched a result but it lacked a usable name,
            # coordinates, or timezone: treat it as an unusable response.
            raise ArtError(502, _GEOCODER_UNREACHABLE)
        return location

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
                "weather_location": None,
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
            state["settings"]["weather_location"] = _normalize_weather_location(
                settings.get("weather_location")
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


# --------------------------------------------------------------------------- #
# Geocoding helpers (module-level, pure except for the injected fetch)
# --------------------------------------------------------------------------- #


def _split_query(query: str) -> tuple[str, Optional[str]]:
    """Split ``City, Qualifier`` at the first comma into city and qualifier.

    The city is the only part that is searched for (the term that leaves the
    LAN); the qualifier, when present and non-blank, narrows the results on the
    hub. No comma, or a blank qualifier, yields ``(city, None)``.
    """
    city, sep, rest = query.partition(",")
    city = city.strip()
    if not sep:
        return city, None
    qualifier = rest.strip()
    return city, (qualifier or None)


def _no_place_message(query: str) -> str:
    return f'No place found for "{query}". Try "City, State".'


def _geocode_results(body: Optional[bytes]) -> Optional[list]:
    """The results list from a geocoder body, distinguishing failure from no-match.

    Returns ``None`` when the fetch failed (no body) or the body did not parse as
    a JSON object or carried a non-list ``results`` (a bad response the caller
    turns into a 502). A parsed object with no ``results`` key, or an empty list,
    is a clean search that matched nothing, returned as ``[]`` (a 400 no-match).
    """
    if body is None:
        return None
    try:
        parsed = json.loads(body)
    except (ValueError, TypeError):
        return None
    if not isinstance(parsed, dict):
        return None
    results = parsed.get("results")
    if results is None:
        return []
    if not isinstance(results, list):
        return None
    return results


def _choose_place(results: list, qualifier: Optional[str]):
    """Pick one place from the results, preferring a qualifier match.

    With a qualifier, results whose state or country matches it are preferred and
    the most populous of those wins; with no qualifier, or when none match, the
    most populous result overall wins. Returns ``None`` only when there is no
    usable result at all.
    """
    places = [r for r in results if isinstance(r, dict)]
    if not places:
        return None
    if qualifier:
        matched = [r for r in places if _matches_qualifier(r, qualifier)]
        if matched:
            return _most_populous(matched)
    return _most_populous(places)


def _most_populous(places: list) -> dict:
    """The most populous place, keeping input order as the tie-break."""
    return max(places, key=_population)


def _population(place: dict) -> int:
    value = place.get("population")
    if isinstance(value, bool):
        return 0
    if isinstance(value, (int, float)):
        return int(value)
    return 0


def _matches_qualifier(place: dict, qualifier: str) -> bool:
    """Whether a place's state or country matches a typed qualifier.

    Matches case-insensitively against the admin1 (state or region) name, the
    country name, the ISO country code, and, for a US state, its postal code
    (Open-Meteo returns the full state name, so the hub maps "CA" to
    "California").
    """
    wanted = qualifier.strip().casefold()
    if not wanted:
        return False
    candidates: list[str] = []
    admin1 = place.get("admin1")
    if isinstance(admin1, str) and admin1:
        candidates.append(admin1)
        code = _US_STATE_NAME_TO_CODE.get(admin1.casefold())
        if code:
            candidates.append(code)
    for key in ("country", "country_code"):
        value = place.get(key)
        if isinstance(value, str) and value:
            candidates.append(value)
    return any(candidate.casefold() == wanted for candidate in candidates)


def _build_location(place: dict) -> Optional[dict]:
    """The stored location for a chosen place, or ``None`` if it is unusable.

    The display name is the place name with its state and country appended
    ("San Diego, California, United States"); coordinates are rounded to two
    decimals (city level); the timezone is the result's IANA name. A result that
    lacks a usable name, coordinates, or timezone yields ``None``.
    """
    name = place.get("name")
    latitude = place.get("latitude")
    longitude = place.get("longitude")
    timezone = place.get("timezone")
    if not isinstance(name, str) or not name:
        return None
    if not _is_number(latitude) or not _is_number(longitude):
        return None
    if not isinstance(timezone, str) or not timezone:
        return None
    parts = [name]
    for key in ("admin1", "country"):
        value = place.get(key)
        if isinstance(value, str) and value:
            parts.append(value)
    return {
        "name": ", ".join(parts),
        "latitude": round(float(latitude), 2),
        "longitude": round(float(longitude), 2),
        "timezone": timezone,
    }


def _normalize_weather_location(value) -> Optional[dict]:
    """Coerce a stored weather location into a trusted value, or ``None``.

    The value was validated on the way in, but the file could be hand-edited or
    written by an older version, so a stored location is accepted only when it
    carries a non-empty name, numeric coordinates, and a non-empty timezone;
    anything else reads as no location set.
    """
    if not isinstance(value, dict):
        return None
    name = value.get("name")
    latitude = value.get("latitude")
    longitude = value.get("longitude")
    timezone = value.get("timezone")
    if not isinstance(name, str) or not name:
        return None
    if not _is_number(latitude) or not _is_number(longitude):
        return None
    if not isinstance(timezone, str) or not timezone:
        return None
    return {
        "name": name,
        "latitude": round(float(latitude), 2),
        "longitude": round(float(longitude), 2),
        "timezone": timezone,
    }


def _is_number(value) -> bool:
    """A real number, excluding ``bool`` (an ``int`` subclass)."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)
