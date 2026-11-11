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


def _make(tmp_path: Path, *, clock=None, art_history=None) -> FrameControl:
    state = tmp_path / "state" / "frame-control.json"
    kwargs = {"art_history": art_history}
    if clock is not None:
        kwargs["clock"] = clock
    return FrameControl(state, **kwargs)


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
