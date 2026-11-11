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

import os
from datetime import time
from pathlib import Path

import pytest

from snaphouse import backlight


class _FakeClock:
    """A monotonic clock we can step by hand, for the idle/wake timing."""

    def __init__(self, start: float = 1000.0) -> None:
        self.t = start

    def __call__(self) -> float:
        return self.t


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


# --------------------------------------------------------------------------- #
# _ActivityClock: the wake-on-touch path (injected clock, fake input)
# --------------------------------------------------------------------------- #


def test_activity_clock_wakes_on_a_touch(tmp_path):
    # A pipe stands in for a touchscreen evdev node: readable only once "touched".
    read_fd, write_fd = os.pipe()
    os.set_blocking(read_fd, False)
    (tmp_path / "event0").write_text("")  # a path for the glob to find
    clock_time = _FakeClock(1000.0)
    activity = backlight._ActivityClock(
        str(tmp_path / "event*"),
        monotonic=clock_time,
        opener=lambda path, flags: read_fd,
    )

    # Time passes with no touch: the screen is idle, and waiting reports nothing.
    clock_time.t = 1100.0
    assert activity.idle_s == 100.0
    assert activity.wait(0.01) is False
    assert activity.idle_s == 100.0

    # A touch arrives: the wait wakes, and the idle timer resets to now.
    os.write(write_fd, b"\x01")
    assert activity.wait(0.01) is True
    assert activity.idle_s == 0.0

    os.close(write_fd)
    os.close(read_fd)


def test_activity_clock_opens_a_touchscreen_that_appears_late(tmp_path):
    # The daemon can start before the touchscreen node is enumerated (a power cut,
    # a driver binding late). It must rescan and open the node when it shows up,
    # then wake on a touch, rather than sleep forever against an empty fd set.
    read_fd, write_fd = os.pipe()
    os.set_blocking(read_fd, False)
    opened: list[str] = []

    def opener(path, flags):
        opened.append(path)
        return read_fd

    activity = backlight._ActivityClock(
        str(tmp_path / "event*"),
        monotonic=_FakeClock(0.0),
        opener=opener,
    )
    assert opened == []  # nothing to open yet

    (tmp_path / "event0").write_text("")  # the touchscreen appears after start
    assert activity.wait(0.01) is False  # rescans, opens it, no touch yet
    assert opened == [str(tmp_path / "event0")]

    os.write(write_fd, b"\x01")
    assert activity.wait(0.01) is True  # the late node now wakes the screen

    os.close(write_fd)
    os.close(read_fd)
