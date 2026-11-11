"""Tests for the hub's frame control core (lib/snaphouse/framecontrol.py).

Frame control is the settings and pin the art admin service owns alongside the
art library and publishes as /frame-control.json: which kinds of audio take the
now-playing screen, the quiet hours and morning-weather windows, and a pin that
holds the frame on the current art piece, the weather, or now playing for a
while. This module is HTTP-free (like artlib): every method takes plain values,
raises ArtError(status, message) on bad input, and persists its state to a JSON
file so a service restart reloads it. The clock is injected so the "until"
computations (and the morning-window resolution) are deterministic under test.
"""

from __future__ import annotations

import json
import urllib.parse
from datetime import datetime, time, timedelta
from pathlib import Path

import pytest

from snaphouse.artlib import ArtError
from snaphouse.framecontrol import FrameControl


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _clock_at(year, month, day, hour, minute):
    """A clock callable fixed at a local-tz wall-clock time.

    The datetime is built in the system's local zone (the same zone the module
    uses), so the expected "until" values the tests compute with the same helper
    match the module's output whatever time zone CI runs in.
    """
    moment = datetime(year, month, day, hour, minute).astimezone()
    return lambda: moment.timestamp(), moment


def _make(tmp_path: Path, *, clock=None, art_history=None, geocode=None) -> FrameControl:
    state = tmp_path / "state" / "frame-control.json"
    kwargs = {"art_history": art_history, "geocode": geocode}
    if clock is not None:
        kwargs["clock"] = clock
    return FrameControl(state, **kwargs)


# A sentinel a fake geocoder returns to stand for a fetch that failed outright
# (timeout, transport error): the real fetch returns None in that case.
_UNREACHABLE = object()


class FakeGeocoder:
    """A stand-in for the injected geocoder fetch (url -> body bytes or None).

    Built with a map from the searched city name (case-folded) to the body to
    serve: a dict is encoded as JSON, raw ``bytes`` are served verbatim (a
    malformed body), and ``_UNREACHABLE`` serves ``None`` (a failed fetch). A
    city not in the map serves an empty-results document (a clean no-match). Every
    URL it is called with is recorded, so a test can assert only the typed city
    name left the LAN.
    """

    def __init__(self, bodies: dict) -> None:
        self._bodies = bodies
        self.calls: list[str] = []

    def __call__(self, url: str):
        self.calls.append(url)
        params = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        name = (params.get("name", [""])[0]).casefold()
        body = self._bodies.get(name, {"generationtime_ms": 0.1})
        if body is _UNREACHABLE:
            return None
        if isinstance(body, bytes):
            return body
        return json.dumps(body).encode("utf-8")


def _place(name, lat, lon, tz, population, admin1, country, country_code):
    return {
        "name": name,
        "latitude": lat,
        "longitude": lon,
        "timezone": tz,
        "population": population,
        "admin1": admin1,
        "country": country,
        "country_code": country_code,
    }


_SAN_DIEGO = {
    "san diego": {
        "results": [
            _place("San Diego", 32.71533, -117.15726, "America/Los_Angeles",
                   1394928, "California", "United States", "US"),
        ]
    }
}

_PORTLAND = {
    "portland": {
        "results": [
            _place("Portland", 45.52345, -122.67621, "America/Los_Angeles",
                   652503, "Oregon", "United States", "US"),
            _place("Portland", 43.65737, -70.25893, "America/New_York",
                   66215, "Maine", "United States", "US"),
        ]
    }
}

_PARIS = {
    "paris": {
        "results": [
            _place("Paris", 48.85341, 2.3488, "Europe/Paris",
                   2138551, "Île-de-France", "France", "FR"),
            _place("Paris", 33.66094, -95.55551, "America/Chicago",
                   25171, "Texas", "United States", "US"),
        ]
    }
}


def _local_iso(moment: datetime) -> str:
    return moment.astimezone().isoformat()


# --------------------------------------------------------------------------- #
# Defaults
# --------------------------------------------------------------------------- #


def test_defaults_when_no_state_exists(tmp_path):
    frame = _make(tmp_path)
    doc = frame.document()
    assert doc["pin"] is None
    settings = doc["settings"]
    assert settings["now_playing_kinds"] == ["music"]
    assert settings["quiet_hours"] == {"start": "22:00", "end": "06:00"}
    assert settings["morning_weather"] == {
        "enabled": True,
        "weekday": ["06:00", "08:30"],
        "weekend": ["07:00", "09:00"],
    }


def test_document_is_only_settings_and_pin(tmp_path):
    # The published shape is exactly {settings, pin}; the private seq counter must
    # not leak into the document a frame reads.
    frame = _make(tmp_path)
    assert set(frame.document().keys()) == {"settings", "pin"}


# --------------------------------------------------------------------------- #
# Settings: subset updates
# --------------------------------------------------------------------------- #


def test_update_kinds_only_leaves_the_rest_at_default(tmp_path):
    frame = _make(tmp_path)
    settings = frame.update_settings({"now_playing_kinds": ["music", "podcast"]})
    assert settings["now_playing_kinds"] == ["music", "podcast"]
    # Untouched keys keep their defaults.
    assert settings["quiet_hours"] == {"start": "22:00", "end": "06:00"}


def test_update_quiet_hours_subset_merges_one_side(tmp_path):
    frame = _make(tmp_path)
    settings = frame.update_settings({"quiet_hours": {"start": "23:00"}})
    assert settings["quiet_hours"] == {"start": "23:00", "end": "06:00"}


def test_update_morning_weather_enabled_only(tmp_path):
    frame = _make(tmp_path)
    settings = frame.update_settings({"morning_weather": {"enabled": False}})
    assert settings["morning_weather"]["enabled"] is False
    # The windows are untouched.
    assert settings["morning_weather"]["weekday"] == ["06:00", "08:30"]


def test_update_morning_weather_window(tmp_path):
    frame = _make(tmp_path)
    settings = frame.update_settings(
        {"morning_weather": {"weekday": ["05:30", "07:30"]}}
    )
    assert settings["morning_weather"]["weekday"] == ["05:30", "07:30"]


def test_empty_subset_is_a_no_op(tmp_path):
    frame = _make(tmp_path)
    settings = frame.update_settings({})
    assert settings["now_playing_kinds"] == ["music"]


def test_kinds_are_deduplicated_in_order(tmp_path):
    frame = _make(tmp_path)
    settings = frame.update_settings(
        {"now_playing_kinds": ["podcast", "music", "podcast"]}
    )
    assert settings["now_playing_kinds"] == ["podcast", "music"]


# --------------------------------------------------------------------------- #
# Settings: validation errors
# --------------------------------------------------------------------------- #


def test_unknown_kind_is_rejected(tmp_path):
    frame = _make(tmp_path)
    with pytest.raises(ArtError) as info:
        frame.update_settings({"now_playing_kinds": ["music", "radio"]})
    assert info.value.status == 400


def test_kinds_must_be_a_list(tmp_path):
    frame = _make(tmp_path)
    with pytest.raises(ArtError) as info:
        frame.update_settings({"now_playing_kinds": "music"})
    assert info.value.status == 400


def test_bad_time_is_rejected(tmp_path):
    frame = _make(tmp_path)
    with pytest.raises(ArtError) as info:
        frame.update_settings({"quiet_hours": {"start": "24:00"}})
    assert info.value.status == 400


def test_non_hhmm_time_is_rejected(tmp_path):
    frame = _make(tmp_path)
    with pytest.raises(ArtError):
        frame.update_settings({"quiet_hours": {"end": "6am"}})


def test_morning_start_not_before_end_is_rejected(tmp_path):
    frame = _make(tmp_path)
    with pytest.raises(ArtError) as info:
        frame.update_settings({"morning_weather": {"weekday": ["08:30", "06:00"]}})
    assert info.value.status == 400


def test_morning_window_equal_start_and_end_is_rejected(tmp_path):
    frame = _make(tmp_path)
    with pytest.raises(ArtError):
        frame.update_settings({"morning_weather": {"weekend": ["07:00", "07:00"]}})


def test_morning_window_must_have_two_times(tmp_path):
    frame = _make(tmp_path)
    with pytest.raises(ArtError):
        frame.update_settings({"morning_weather": {"weekday": ["06:00"]}})


def test_enabled_must_be_a_bool(tmp_path):
    frame = _make(tmp_path)
    with pytest.raises(ArtError):
        frame.update_settings({"morning_weather": {"enabled": "yes"}})


def test_a_bad_value_does_not_persist(tmp_path):
    # A rejected update leaves the stored settings untouched.
    frame = _make(tmp_path)
    frame.update_settings({"now_playing_kinds": ["podcast"]})
    with pytest.raises(ArtError):
        frame.update_settings({"now_playing_kinds": ["radio"]})
    assert frame.document()["settings"]["now_playing_kinds"] == ["podcast"]


# --------------------------------------------------------------------------- #
# Pin: each what and each duration
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("what", ["art", "weather", "now_playing"])
def test_pin_each_what(tmp_path, what):
    clock, _ = _clock_at(2026, 11, 9, 14, 0)
    frame = _make(tmp_path, clock=clock)
    pin = frame.pin(what, "forever")
    assert pin["what"] == what
    assert pin["until"] is None
    assert pin["seq"] == 1
    assert isinstance(pin["set_at"], str) and pin["set_at"]


def test_pin_one_hour(tmp_path):
    clock, moment = _clock_at(2026, 11, 9, 14, 0)
    frame = _make(tmp_path, clock=clock)
    pin = frame.pin("weather", "1h")
    assert pin["until"] == _local_iso(moment + timedelta(hours=1))


def test_pin_four_hours(tmp_path):
    clock, moment = _clock_at(2026, 11, 9, 14, 0)
    frame = _make(tmp_path, clock=clock)
    pin = frame.pin("weather", "4h")
    assert pin["until"] == _local_iso(moment + timedelta(hours=4))


def test_pin_forever_has_no_expiry(tmp_path):
    clock, _ = _clock_at(2026, 11, 9, 14, 0)
    frame = _make(tmp_path, clock=clock)
    assert frame.pin("weather", "forever")["until"] is None


def test_unknown_what_is_rejected(tmp_path):
    frame = _make(tmp_path)
    with pytest.raises(ArtError) as info:
        frame.pin("clock", "1h")
    assert info.value.status == 400


def test_unknown_duration_is_rejected(tmp_path):
    frame = _make(tmp_path)
    with pytest.raises(ArtError) as info:
        frame.pin("weather", "2h")
    assert info.value.status == 400


# --------------------------------------------------------------------------- #
# Pin: morning resolution
# --------------------------------------------------------------------------- #


def _expected_morning(moment: datetime, hour: int, minute: int, day_offset: int) -> str:
    day = (moment + timedelta(days=day_offset)).date()
    return datetime.combine(day, time(hour, minute)).astimezone().isoformat()


def test_morning_resolves_to_next_weekday_start(tmp_path):
    # Monday 14:00: the next morning-window start is Tuesday (a weekday) 06:00.
    clock, moment = _clock_at(2026, 11, 9, 14, 0)  # 2026-11-09 is a Monday
    frame = _make(tmp_path, clock=clock)
    pin = frame.pin("weather", "morning")
    assert pin["until"] == _expected_morning(moment, 6, 0, 1)


def test_morning_picks_today_when_before_the_start(tmp_path):
    # Monday 05:00, before the 06:00 weekday start: the next start is today 06:00.
    clock, moment = _clock_at(2026, 11, 9, 5, 0)
    frame = _make(tmp_path, clock=clock)
    pin = frame.pin("weather", "morning")
    assert pin["until"] == _expected_morning(moment, 6, 0, 0)


def test_morning_uses_the_weekend_window(tmp_path):
    # Saturday 10:00, past the 07:00 weekend start: the next start is Sunday 07:00.
    clock, moment = _clock_at(2026, 11, 14, 10, 0)  # 2026-11-14 is a Saturday
    frame = _make(tmp_path, clock=clock)
    pin = frame.pin("weather", "morning")
    assert pin["until"] == _expected_morning(moment, 7, 0, 1)


def test_morning_follows_a_changed_window(tmp_path):
    # A changed weekday start moves the resolution with it.
    clock, moment = _clock_at(2026, 11, 9, 14, 0)
    frame = _make(tmp_path, clock=clock)
    frame.update_settings({"morning_weather": {"weekday": ["05:15", "07:00"]}})
    pin = frame.pin("weather", "morning")
    assert pin["until"] == _expected_morning(moment, 5, 15, 1)


# --------------------------------------------------------------------------- #
# Pin: art captures the current piece from the art history
# --------------------------------------------------------------------------- #


def _write_history(path: Path, current) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"current": current, "history": []}), encoding="utf-8")


def test_pin_art_captures_the_current_piece_id(tmp_path):
    history = tmp_path / "public" / "art-history.json"
    _write_history(history, {"file": "aic-1.jpg", "id": "Photos/aic-1.jpg", "title": "X"})
    frame = _make(tmp_path, art_history=history)
    pin = frame.pin("art", "forever")
    assert pin["what"] == "art"
    assert pin["piece"] == "Photos/aic-1.jpg"


def test_pin_art_falls_back_to_file_when_no_id(tmp_path):
    history = tmp_path / "public" / "art-history.json"
    _write_history(history, {"file": "aic-2.jpg", "id": None, "title": "X"})
    frame = _make(tmp_path, art_history=history)
    assert frame.pin("art", "forever")["piece"] == "aic-2.jpg"


def test_pin_art_piece_is_null_when_history_missing(tmp_path):
    history = tmp_path / "public" / "art-history.json"
    frame = _make(tmp_path, art_history=history)
    assert frame.pin("art", "forever")["piece"] is None


def test_pin_art_piece_is_null_when_history_is_malformed(tmp_path):
    history = tmp_path / "public" / "art-history.json"
    history.parent.mkdir(parents=True, exist_ok=True)
    history.write_text("not json", encoding="utf-8")
    frame = _make(tmp_path, art_history=history)
    assert frame.pin("art", "forever")["piece"] is None


def test_pin_art_piece_is_null_when_nothing_is_current(tmp_path):
    history = tmp_path / "public" / "art-history.json"
    _write_history(history, None)
    frame = _make(tmp_path, art_history=history)
    assert frame.pin("art", "forever")["piece"] is None


def test_non_art_pin_never_captures_a_piece(tmp_path):
    history = tmp_path / "public" / "art-history.json"
    _write_history(history, {"file": "aic-1.jpg", "id": "aic-1.jpg", "title": "X"})
    frame = _make(tmp_path, art_history=history)
    assert frame.pin("weather", "forever")["piece"] is None


# --------------------------------------------------------------------------- #
# Unpin and the sequence number
# --------------------------------------------------------------------------- #


def test_unpin_clears_the_pin(tmp_path):
    frame = _make(tmp_path)
    frame.pin("weather", "forever")
    assert frame.unpin() is None
    assert frame.document()["pin"] is None


def test_unpin_with_no_pin_is_a_no_op(tmp_path):
    frame = _make(tmp_path)
    assert frame.unpin() is None
    assert frame.document()["pin"] is None


def test_seq_increases_on_every_pin(tmp_path):
    frame = _make(tmp_path)
    first = frame.pin("weather", "forever")
    second = frame.pin("art", "forever")
    assert second["seq"] > first["seq"]


def test_seq_keeps_climbing_across_an_unpin(tmp_path):
    # A pin, an unpin, then another pin: the last seq is strictly greater than the
    # first, so a frame always sees a change it has not applied.
    frame = _make(tmp_path)
    first = frame.pin("weather", "forever")
    frame.unpin()
    third = frame.pin("art", "forever")
    assert third["seq"] > first["seq"]


# --------------------------------------------------------------------------- #
# Persistence across a restart
# --------------------------------------------------------------------------- #


def test_restart_reloads_settings_and_pin(tmp_path):
    state = tmp_path / "state" / "frame-control.json"
    first = FrameControl(state)
    first.update_settings({"now_playing_kinds": ["music", "audiobook"]})
    first.pin("weather", "forever")

    # A fresh instance on the same file is what a service restart gives.
    second = FrameControl(state)
    doc = second.document()
    assert doc["settings"]["now_playing_kinds"] == ["music", "audiobook"]
    assert doc["pin"]["what"] == "weather"


def test_restart_keeps_the_seq_climbing(tmp_path):
    state = tmp_path / "state" / "frame-control.json"
    first = FrameControl(state)
    pinned = first.pin("weather", "forever")

    second = FrameControl(state)
    after = second.pin("art", "forever")
    assert after["seq"] > pinned["seq"]


# --------------------------------------------------------------------------- #
# Weather location: geocoding a typed city
# --------------------------------------------------------------------------- #


def test_weather_location_defaults_to_null(tmp_path):
    frame = _make(tmp_path)
    assert frame.document()["settings"]["weather_location"] is None


def test_weather_location_geocodes_a_city_and_stores_it(tmp_path):
    geo = FakeGeocoder(_SAN_DIEGO)
    frame = _make(tmp_path, geocode=geo)
    settings = frame.update_settings({"weather_location": {"query": "San Diego, CA"}})
    assert settings["weather_location"] == {
        "name": "San Diego, California, United States",
        "latitude": 32.72,
        "longitude": -117.16,
        "timezone": "America/Los_Angeles",
    }
    # It is published in the document a frame reads.
    assert frame.document()["settings"]["weather_location"]["name"] == (
        "San Diego, California, United States"
    )


def test_weather_location_rounds_coordinates_to_two_decimals(tmp_path):
    geo = FakeGeocoder(_SAN_DIEGO)
    frame = _make(tmp_path, geocode=geo)
    location = frame.update_settings({"weather_location": {"query": "San Diego, CA"}})[
        "weather_location"
    ]
    assert location["latitude"] == 32.72
    assert location["longitude"] == -117.16


def test_only_the_typed_city_leaves_the_lan(tmp_path):
    # The qualifier stays on the hub; only the city part is sent to the geocoder,
    # and only to the Open-Meteo geocoding host.
    geo = FakeGeocoder(_SAN_DIEGO)
    frame = _make(tmp_path, geocode=geo)
    frame.update_settings({"weather_location": {"query": "San Diego, CA"}})
    assert len(geo.calls) == 1
    url = geo.calls[0]
    assert url.startswith("https://geocoding-api.open-meteo.com/v1/search?")
    params = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
    assert params["name"] == ["San Diego"]
    assert "CA" not in url


def test_state_code_qualifier_picks_the_right_portland(tmp_path):
    geo = FakeGeocoder(_PORTLAND)
    frame = _make(tmp_path, geocode=geo)
    oregon = frame.update_settings({"weather_location": {"query": "Portland, OR"}})[
        "weather_location"
    ]
    assert oregon["name"] == "Portland, Oregon, United States"
    assert oregon["timezone"] == "America/Los_Angeles"

    maine = frame.update_settings({"weather_location": {"query": "Portland, ME"}})[
        "weather_location"
    ]
    assert maine["name"] == "Portland, Maine, United States"
    assert maine["timezone"] == "America/New_York"


def test_country_name_qualifier_picks_the_right_paris(tmp_path):
    geo = FakeGeocoder(_PARIS)
    frame = _make(tmp_path, geocode=geo)
    location = frame.update_settings({"weather_location": {"query": "Paris, France"}})[
        "weather_location"
    ]
    assert location["name"] == "Paris, Île-de-France, France"
    assert location["timezone"] == "Europe/Paris"


def test_no_qualifier_takes_the_most_populous_result(tmp_path):
    geo = FakeGeocoder(_PORTLAND)
    frame = _make(tmp_path, geocode=geo)
    location = frame.update_settings({"weather_location": {"query": "Portland"}})[
        "weather_location"
    ]
    assert location["name"] == "Portland, Oregon, United States"


def test_no_place_found_is_a_400_naming_the_query(tmp_path):
    geo = FakeGeocoder({})  # every city serves empty results
    frame = _make(tmp_path, geocode=geo)
    with pytest.raises(ArtError) as info:
        frame.update_settings({"weather_location": {"query": "Nowhereville, ZZ"}})
    assert info.value.status == 400
    assert "Nowhereville, ZZ" in info.value.message
    assert 'City, State' in info.value.message


def test_unreachable_geocoder_is_a_502(tmp_path):
    geo = FakeGeocoder({"san diego": _UNREACHABLE})
    frame = _make(tmp_path, geocode=geo)
    with pytest.raises(ArtError) as info:
        frame.update_settings({"weather_location": {"query": "San Diego, CA"}})
    assert info.value.status == 502
    assert "Try again" in info.value.message


def test_malformed_geocoder_body_is_a_502(tmp_path):
    geo = FakeGeocoder({"san diego": b"not json at all"})
    frame = _make(tmp_path, geocode=geo)
    with pytest.raises(ArtError) as info:
        frame.update_settings({"weather_location": {"query": "San Diego, CA"}})
    assert info.value.status == 502


def test_weather_location_null_clears_it(tmp_path):
    geo = FakeGeocoder(_SAN_DIEGO)
    frame = _make(tmp_path, geocode=geo)
    frame.update_settings({"weather_location": {"query": "San Diego, CA"}})
    settings = frame.update_settings({"weather_location": None})
    assert settings["weather_location"] is None
    assert frame.document()["settings"]["weather_location"] is None


def test_blank_query_is_rejected_without_a_fetch(tmp_path):
    geo = FakeGeocoder(_SAN_DIEGO)
    frame = _make(tmp_path, geocode=geo)
    with pytest.raises(ArtError) as info:
        frame.update_settings({"weather_location": {"query": "   "}})
    assert info.value.status == 400
    assert geo.calls == []


def test_weather_location_bad_shape_is_rejected(tmp_path):
    frame = _make(tmp_path, geocode=FakeGeocoder(_SAN_DIEGO))
    for bad in [42, "San Diego", {"name": "San Diego"}, []]:
        with pytest.raises(ArtError) as info:
            frame.update_settings({"weather_location": bad})
        assert info.value.status == 400


def test_weather_location_leaves_other_settings_untouched(tmp_path):
    geo = FakeGeocoder(_SAN_DIEGO)
    frame = _make(tmp_path, geocode=geo)
    frame.update_settings({"now_playing_kinds": ["music", "podcast"]})
    settings = frame.update_settings({"weather_location": {"query": "San Diego, CA"}})
    assert settings["now_playing_kinds"] == ["music", "podcast"]
    # And a later unrelated update keeps the weather location.
    after = frame.update_settings({"quiet_hours": {"start": "23:00"}})
    assert after["weather_location"]["name"] == "San Diego, California, United States"


def test_a_failed_geocode_leaves_stored_settings_untouched(tmp_path):
    geo = FakeGeocoder({"san diego": _UNREACHABLE})
    frame = _make(tmp_path, geocode=geo)
    frame.update_settings({"now_playing_kinds": ["podcast"]})
    with pytest.raises(ArtError):
        frame.update_settings({"weather_location": {"query": "San Diego, CA"}})
    doc = frame.document()["settings"]
    assert doc["now_playing_kinds"] == ["podcast"]
    assert doc["weather_location"] is None


def test_stored_location_survives_a_restart(tmp_path):
    state = tmp_path / "state" / "frame-control.json"
    geo = FakeGeocoder(_SAN_DIEGO)
    first = FrameControl(state, geocode=geo)
    first.update_settings({"weather_location": {"query": "San Diego, CA"}})

    second = FrameControl(state)
    location = second.document()["settings"]["weather_location"]
    assert location == {
        "name": "San Diego, California, United States",
        "latitude": 32.72,
        "longitude": -117.16,
        "timezone": "America/Los_Angeles",
    }


def test_a_malformed_stored_location_reads_as_null(tmp_path):
    state = tmp_path / "state" / "frame-control.json"
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text(
        json.dumps(
            {
                "settings": {"weather_location": {"name": "X", "latitude": "nope"}},
                "pin": None,
                "seq": 0,
            }
        ),
        encoding="utf-8",
    )
    frame = FrameControl(state)
    assert frame.document()["settings"]["weather_location"] is None
