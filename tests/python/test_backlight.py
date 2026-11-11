"""Tests for the panel backlight schedule (lib/snaphouse/backlight.py).

The panel role's snaphouse-backlight service dims the official DSI display during
the shared quiet hours and wakes it to full on a touch, returning to the low
level after a short idle, full otherwise. The schedule is a pure function of the
wall clock (the quiet-hours window) and how long since the last touch, and the
only side effect is writing the sysfs `brightness` file. So the whole thing is
exercised here with an injected clock and a fake /sys/class/backlight tree, with
no hardware: Schedule decides the level, Backlight writes it, apply_once ties the
two together.
"""

from __future__ import annotations

from datetime import time
from pathlib import Path

import pytest

from snaphouse import backlight


def _fake_device(tmp_path: Path, *, max_brightness: int, brightness: int = 0) -> Path:
    device = tmp_path / "10-0045"
    device.mkdir()
    (device / "max_brightness").write_text(f"{max_brightness}\n")
    (device / "brightness").write_text(f"{brightness}\n")
    return device


# --------------------------------------------------------------------------- #
# parse_hhmm
# --------------------------------------------------------------------------- #


def test_parse_hhmm_reads_a_wall_time():
    assert backlight.parse_hhmm("22:00") == time(22, 0)
    assert backlight.parse_hhmm("06:30") == time(6, 30)


def test_parse_hhmm_rejects_junk():
    for bad in ("", "22", "9:99", "nope", "24:00"):
        with pytest.raises(ValueError):
            backlight.parse_hhmm(bad)


# --------------------------------------------------------------------------- #
# Schedule.in_quiet_hours
# --------------------------------------------------------------------------- #


def _schedule(**kw) -> backlight.Schedule:
    base = dict(
        quiet_start=time(22, 0),
        quiet_end=time(6, 0),
        full_pct=100,
        dim_pct=15,
        dim_timeout_s=60.0,
    )
    base.update(kw)
    return backlight.Schedule(**base)


def test_quiet_hours_wrap_past_midnight():
    sched = _schedule()
    for inside in (time(22, 0), time(23, 30), time(0, 0), time(5, 59)):
        assert sched.in_quiet_hours(inside), inside
    for outside in (time(6, 0), time(6, 1), time(12, 0), time(21, 59)):
        assert not sched.in_quiet_hours(outside), outside


def test_quiet_hours_same_day_window_does_not_wrap():
    sched = _schedule(quiet_start=time(1, 0), quiet_end=time(2, 0))
    assert sched.in_quiet_hours(time(1, 30))
    assert not sched.in_quiet_hours(time(0, 30))
    assert not sched.in_quiet_hours(time(3, 0))


# --------------------------------------------------------------------------- #
# Schedule.target_pct
# --------------------------------------------------------------------------- #


def test_outside_quiet_hours_is_always_full():
    sched = _schedule()
    # Idle length is irrelevant outside quiet hours: the screen stays full.
    assert sched.target_pct(time(12, 0), idle_s=0.0) == 100
    assert sched.target_pct(time(12, 0), idle_s=9999.0) == 100


def test_inside_quiet_hours_dims_after_the_idle_timeout():
    sched = _schedule()
    # A recent touch keeps it at full.
    assert sched.target_pct(time(23, 0), idle_s=0.0) == 100
    assert sched.target_pct(time(23, 0), idle_s=59.9) == 100
    # Past the timeout it falls back to the low level.
    assert sched.target_pct(time(23, 0), idle_s=60.0) == 15
    assert sched.target_pct(time(23, 0), idle_s=3600.0) == 15


# --------------------------------------------------------------------------- #
# Backlight (the fake sysfs)
# --------------------------------------------------------------------------- #


def test_set_pct_scales_against_max_brightness(tmp_path):
    device = _fake_device(tmp_path, max_brightness=255)
    light = backlight.Backlight(device)
    assert light.max_brightness == 255
    assert light.set_pct(100) == 255
    assert (device / "brightness").read_text().strip() == "255"
    assert light.set_pct(15) == 38  # round(255 * 0.15)
    assert (device / "brightness").read_text().strip() == "38"


def test_set_pct_clamps_out_of_range(tmp_path):
    device = _fake_device(tmp_path, max_brightness=10)
    light = backlight.Backlight(device)
    assert light.set_pct(0) == 0
    assert light.set_pct(-20) == 0
    assert light.set_pct(250) == 10


def test_discover_picks_the_single_backlight_device(tmp_path):
    _fake_device(tmp_path, max_brightness=100)
    light = backlight.Backlight.discover(tmp_path)
    assert light.max_brightness == 100


def test_discover_raises_when_there_is_no_backlight(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(backlight.NoBacklightError):
        backlight.Backlight.discover(empty)


# --------------------------------------------------------------------------- #
# apply_once: schedule + sysfs together
# --------------------------------------------------------------------------- #


def test_apply_once_writes_the_scheduled_level(tmp_path):
    device = _fake_device(tmp_path, max_brightness=10)
    light = backlight.Backlight(device)
    sched = _schedule(full_pct=100, dim_pct=20, dim_timeout_s=60.0)

    # Quiet hours, idle past the timeout: dim to 20% of 10 = 2.
    assert backlight.apply_once(light, sched, now=time(23, 0), idle_s=120.0) == 2
    assert (device / "brightness").read_text().strip() == "2"

    # Quiet hours, just touched: back to full.
    assert backlight.apply_once(light, sched, now=time(23, 0), idle_s=1.0) == 10
    assert (device / "brightness").read_text().strip() == "10"

    # Daytime: full regardless of idle.
    assert backlight.apply_once(light, sched, now=time(12, 0), idle_s=9999.0) == 10
    assert (device / "brightness").read_text().strip() == "10"
