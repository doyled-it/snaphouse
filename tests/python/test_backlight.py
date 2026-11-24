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


def _schedule(**kw) -> backlight.Settings:
    base = dict(
        quiet_start=time(22, 0),
        quiet_end=time(6, 0),
        full_pct=100,
        dim_pct=15,
        wait_s=60.0,
        screensaver_on=False,
        screensaver_pct=40,
    )
    if "dim_timeout_s" in kw:
        kw["wait_s"] = kw.pop("dim_timeout_s")
    base.update(kw)
    return backlight.Settings(**base)


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


# --------------------------------------------------------------------------- #
# Wake-only tap: an exclusive grab (EVIOCGRAB) while dimmed
# --------------------------------------------------------------------------- #

import struct  # noqa: E402

EV_SYN, EV_KEY, EV_ABS = 0, 1, 3
BTN_TOUCH, ABS_MT_SLOT, ABS_MT_TRACKING_ID = 0x14A, 0x2F, 0x39
_EVENT = "@llHHi"


def _ev(etype: int, code: int, value: int) -> bytes:
    return struct.pack(_EVENT, 0, 0, etype, code, value)


class _FakeIoctl:
    """Records EVIOCGRAB calls and answers the EV_ABS capability probe.

    `touch_fds` are the descriptors that report ABS_MT_POSITION_X; any other fd
    (a keyboard, a pipe) answers with no absolute axes, or fails like a pipe does.
    """

    def __init__(self, touch_fds, fail_grab=False):
        self.touch_fds = set(touch_fds)
        self.fail_grab = fail_grab
        self.grabs: list[tuple[int, int]] = []

    def __call__(self, fd, request, arg=0):
        if request == backlight.EVIOCGRAB:
            if self.fail_grab:
                raise OSError(16, "busy")
            self.grabs.append((fd, int(arg)))
            return 0
        if fd in self.touch_fds:
            bits = bytearray(8)
            bits[0x35 // 8] |= 1 << (0x35 % 8)  # ABS_MT_POSITION_X
            return bytes(bits)
        return bytes(8)


def _gate(tmp_path, names=("event0",), touch=("event0",), **kw):
    """An _ActivityClock over pipes, one per node; returns it and the pipes."""
    pipes, fds = {}, {}
    for name in names:
        r, w = os.pipe()
        os.set_blocking(r, False)
        (tmp_path / name).write_text("")
        pipes[name] = (r, w)
        fds[str(tmp_path / name)] = r
    clock = _FakeClock(1000.0)
    ioctl = _FakeIoctl([pipes[n][0] for n in touch], **kw)
    activity = backlight._ActivityClock(
        str(tmp_path / "event*"),
        monotonic=clock,
        opener=lambda path, flags: fds[path],
        ioctl=ioctl,
    )
    return activity, clock, ioctl, pipes


def _send(pipes, name, *events):
    os.write(pipes[name][1], b"".join(events))


def _touch_down():
    return _ev(EV_ABS, ABS_MT_TRACKING_ID, 5) + _ev(EV_KEY, BTN_TOUCH, 1) + _ev(EV_SYN, 0, 0)


def _touch_up():
    return _ev(EV_ABS, ABS_MT_TRACKING_ID, -1) + _ev(EV_KEY, BTN_TOUCH, 0) + _ev(EV_SYN, 0, 0)


def test_night_dimmed_only_when_the_level_is_the_night_dim_level():
    sched = _schedule(full_pct=100, dim_pct=20, dim_timeout_s=60.0)
    assert sched.night_dimmed(time(23, 0), 120.0, False) is True
    assert sched.night_dimmed(time(23, 0), 5.0, False) is False  # just touched
    assert sched.night_dimmed(time(12, 0), 9999.0, False) is False  # daytime
    assert sched.night_dimmed(time(23, 0), 9999.0, True) is False  # music
    same = _schedule(full_pct=100, dim_pct=100, dim_timeout_s=60.0)
    assert same.night_dimmed(time(23, 0), 120.0, False) is False  # nothing to wake from


def test_no_grab_while_the_level_is_full(tmp_path):
    activity, _clock, ioctl, _p = _gate(tmp_path)
    activity.maintain(dim=False)
    assert ioctl.grabs == []


def test_grabs_only_the_touchscreen_node_when_dim(tmp_path):
    activity, _clock, ioctl, pipes = _gate(
        tmp_path, names=("event0", "event1"), touch=("event0",)
    )
    activity.maintain(dim=True)
    assert ioctl.grabs == [(pipes["event0"][0], 1)]


def test_wake_tap_is_swallowed_until_the_finger_lifts_then_released(tmp_path):
    activity, clock, ioctl, pipes = _gate(tmp_path)
    clock.t = 1100.0
    activity.maintain(dim=True)
    fd = pipes["event0"][0]
    assert ioctl.grabs == [(fd, 1)]

    _send(pipes, "event0", _touch_down())
    assert activity.wait(0.01) is True
    assert activity.idle_s == 0.0  # the screen goes to full at once

    # The level is now full (dim=False) but the finger is still down: keep the grab.
    activity.maintain(dim=False)
    assert ioctl.grabs == [(fd, 1)]

    _send(pipes, "event0", _touch_up())
    activity.wait(0.01)
    activity.maintain(dim=False)
    assert ioctl.grabs == [(fd, 1), (fd, 0)]  # released: the next tap reaches the page

    # A later tap while the level is full is never grabbed.
    activity.maintain(dim=False)
    assert ioctl.grabs == [(fd, 1), (fd, 0)]


def test_a_lost_lift_releases_after_a_short_quiet_fallback(tmp_path):
    activity, clock, ioctl, pipes = _gate(tmp_path)
    activity.maintain(dim=True)
    _send(pipes, "event0", _touch_down())
    activity.wait(0.01)
    fd = pipes["event0"][0]

    clock.t += 0.2
    activity.maintain(dim=False)
    assert ioctl.grabs == [(fd, 1)]  # inside the 300 ms window: still swallowing

    clock.t += 0.2
    activity.maintain(dim=False)
    assert ioctl.grabs == [(fd, 1), (fd, 0)]


def test_never_grabs_while_a_finger_is_down(tmp_path):
    activity, _clock, ioctl, pipes = _gate(tmp_path)
    _send(pipes, "event0", _touch_down())
    activity.wait(0.01)
    activity.maintain(dim=True)
    assert ioctl.grabs == []  # the level went dim mid-touch: wait for the lift

    _send(pipes, "event0", _touch_up())
    activity.wait(0.01)
    activity.maintain(dim=True)
    assert [g[1] for g in ioctl.grabs] == [1]


def test_grab_is_dropped_when_the_level_returns_to_full_untouched(tmp_path):
    activity, _clock, ioctl, pipes = _gate(tmp_path)
    activity.maintain(dim=True)
    activity.maintain(dim=False)  # quiet hours ended
    fd = pipes["event0"][0]
    assert ioctl.grabs == [(fd, 1), (fd, 0)]


def test_a_failed_grab_is_tolerated_and_retried(tmp_path):
    activity, _clock, ioctl, _p = _gate(tmp_path, fail_grab=True)
    activity.maintain(dim=True)  # must not raise
    ioctl.fail_grab = False
    activity.maintain(dim=True)
    assert len(ioctl.grabs) == 1


def test_a_second_finger_keeps_the_swallow_going(tmp_path):
    activity, _clock, ioctl, pipes = _gate(tmp_path)
    activity.maintain(dim=True)
    two = (
        _ev(EV_ABS, ABS_MT_SLOT, 0) + _ev(EV_ABS, ABS_MT_TRACKING_ID, 1)
        + _ev(EV_ABS, ABS_MT_SLOT, 1) + _ev(EV_ABS, ABS_MT_TRACKING_ID, 2)
        + _ev(EV_KEY, BTN_TOUCH, 1) + _ev(EV_SYN, 0, 0)
    )
    _send(pipes, "event0", two)
    activity.wait(0.01)
    # One finger lifts, the other is still down.
    _send(pipes, "event0", _ev(EV_ABS, ABS_MT_SLOT, 0) + _ev(EV_ABS, ABS_MT_TRACKING_ID, -1) + _ev(EV_SYN, 0, 0))
    activity.wait(0.01)
    activity.maintain(dim=False)
    assert [g[1] for g in ioctl.grabs] == [1]


def test_a_held_finger_is_not_grabbed_after_the_fallback_release(tmp_path):
    activity, clock, ioctl, pipes = _gate(tmp_path)
    activity.maintain(dim=True)
    _send(pipes, "event0", _touch_down())
    activity.wait(0.01)
    clock.t += 0.5  # the screen stays silent: a held, stationary finger
    activity.maintain(dim=False)
    assert [g[1] for g in ioctl.grabs] == [1, 0]

    # The level goes dim again with the finger still held: no grab mid-touch.
    activity.maintain(dim=True)
    assert [g[1] for g in ioctl.grabs] == [1, 0]

    # A real lift is seen, then the grab can return.
    _send(pipes, "event0", _touch_up())
    activity.wait(0.01)
    activity.maintain(dim=True)
    assert [g[1] for g in ioctl.grabs] == [1, 0, 1]


def test_one_failing_node_does_not_make_the_others_ebusy(tmp_path):
    class _Picky(_FakeIoctl):
        def __call__(self, fd, request, arg=0):
            if request == backlight.EVIOCGRAB and fd == self.bad:
                raise OSError(16, "busy")
            return super().__call__(fd, request, arg)

    names = ("event0", "event1")
    pipes, fds = {}, {}
    for name in names:
        r, w = os.pipe()
        os.set_blocking(r, False)
        (tmp_path / name).write_text("")
        pipes[name] = (r, w)
        fds[str(tmp_path / name)] = r
    ioctl = _Picky([pipes[n][0] for n in names])
    ioctl.bad = pipes["event0"][0]
    activity = backlight._ActivityClock(
        str(tmp_path / "event*"),
        monotonic=_FakeClock(0.0),
        opener=lambda path, flags: fds[path],
        ioctl=ioctl,
    )
    activity.maintain(dim=True)
    activity.maintain(dim=True)  # the retry must skip the node already grabbed
    good = pipes["event1"][0]
    assert ioctl.grabs == [(good, 1)]


def test_an_unplugged_node_is_closed_and_dropped(tmp_path):
    closed: list[int] = []
    read_fd, write_fd = os.pipe()
    os.set_blocking(read_fd, False)
    node = tmp_path / "event0"
    node.write_text("")
    activity = backlight._ActivityClock(
        str(tmp_path / "event*"),
        monotonic=_FakeClock(0.0),
        opener=lambda path, flags: read_fd,
        ioctl=_FakeIoctl([read_fd]),
        closer=lambda fd: closed.append(fd),
    )
    os.close(write_fd)  # EOF: the device went away
    node.unlink()
    activity.wait(0.01)
    assert closed == [read_fd]
    # No fds left, so the next wait sleeps instead of spinning on the dead fd.
    assert activity._fds == {} and activity._touchscreens == set()
    os.close(read_fd)


# --------------------------------------------------------------------------- #
# --no-grab: the page screensaver swallows the waking tap instead
# --------------------------------------------------------------------------- #


def test_grab_flags_default_off_and_parse():
    args = backlight._parse_args([])
    assert args.no_grab is False and args.force_grab is False
    assert backlight._parse_args(["--no-grab"]).no_grab is True
    assert backlight._parse_args(["--force-grab"]).force_grab is True


def test_grab_only_when_night_dimmed_with_screensaver_off():
    g = backlight.grab_wanted
    assert g(dimmed=True, screensaver_on=False, force_grab=False, no_grab=False) is True
    assert g(dimmed=False, screensaver_on=False, force_grab=False, no_grab=False) is False


def test_grab_never_with_screensaver_on_unless_forced():
    g = backlight.grab_wanted
    assert g(dimmed=True, screensaver_on=True, force_grab=False, no_grab=False) is False
    # grab_wake_tap forces the grab, as before.
    assert g(dimmed=True, screensaver_on=True, force_grab=True, no_grab=False) is True
    assert g(dimmed=False, screensaver_on=True, force_grab=True, no_grab=False) is False


def test_no_grab_flag_never_grabs():
    g = backlight.grab_wanted
    assert g(dimmed=True, screensaver_on=False, force_grab=True, no_grab=True) is False


# --------------------------------------------------------------------------- #
# MusicWatch: no night dim while the ambient Now Playing shows
# --------------------------------------------------------------------------- #


class _Feed:
    """A fake hub: returns queued documents, or raises for a failed read."""

    def __init__(self) -> None:
        self.doc = None
        self.fail = False

    def __call__(self):
        if self.fail:
            raise OSError("hub unreachable")
        return self.doc


def _doc(status="playing", *, key="resume", title="Song"):
    entry = {"title": title, "artist": "Band", "album": "LP", "status": status}
    return {"track": entry if key == "track" else None, "resume": entry if key == "resume" else None, "rooms": []}


def _watch(feed, clock):
    return backlight.MusicWatch(feed, clock=clock)


def test_music_watch_playing_is_active():
    clock, feed = _FakeClock(), _Feed()
    watch = _watch(feed, clock)
    feed.doc = _doc("playing", key="track")
    watch.poll()
    assert watch.music_active() is True


def test_music_watch_nothing_playing_is_not_active():
    clock, feed = _FakeClock(), _Feed()
    watch = _watch(feed, clock)
    assert watch.music_active() is False  # before any read
    feed.doc = {"track": None, "resume": None, "rooms": []}
    watch.poll()
    assert watch.music_active() is False


def test_music_watch_paused_counts_for_five_minutes_on_its_own_clock():
    clock, feed = _FakeClock(), _Feed()
    watch = _watch(feed, clock)
    feed.doc = _doc("paused", key="track")
    watch.poll()
    clock.t += 299
    watch.poll()
    assert watch.music_active() is True
    clock.t += 2
    watch.poll()
    assert watch.music_active() is False


def test_music_watch_reads_the_resume_block_for_the_five_minute_rule():
    clock, feed = _FakeClock(), _Feed()
    watch = _watch(feed, clock)
    feed.doc = _doc("paused", key="track")
    watch.poll()
    clock.t += 200
    # The hub's hold passes and the same track moves to `resume`: same clock.
    feed.doc = _doc("paused", key="resume")
    watch.poll()
    clock.t += 99
    watch.poll()
    assert watch.music_active() is True
    clock.t += 2
    watch.poll()
    assert watch.music_active() is False


def test_music_watch_a_new_pause_restarts_the_count_and_play_clears_it():
    clock, feed = _FakeClock(), _Feed()
    watch = _watch(feed, clock)
    feed.doc = _doc("paused", key="track")
    watch.poll()
    clock.t += 400
    feed.doc = _doc("playing", key="track")
    watch.poll()
    assert watch.music_active() is True
    feed.doc = _doc("paused", key="track")
    watch.poll()
    clock.t += 100
    watch.poll()
    assert watch.music_active() is True


def test_music_watch_keeps_its_answer_for_two_minutes_after_a_failed_read():
    clock, feed = _FakeClock(), _Feed()
    watch = _watch(feed, clock)
    feed.doc = _doc("playing", key="track")
    watch.poll()
    feed.fail = True
    clock.t += 119
    watch.poll()
    assert watch.music_active() is True
    clock.t += 2
    watch.poll()
    assert watch.music_active() is False


def test_music_watch_garbage_counts_as_a_failed_read():
    clock, feed = _FakeClock(), _Feed()
    watch = _watch(feed, clock)
    feed.doc = _doc("playing", key="track")
    watch.poll()
    feed.doc = "not a document"
    clock.t += 130
    watch.poll()
    assert watch.music_active() is False


def test_music_watch_fetch_parses_json_and_rejects_junk(tmp_path):
    good = backlight.parse_now_playing(b'{"track": {"title": "T", "status": "playing"}}')
    assert good["track"]["title"] == "T"
    with pytest.raises(ValueError):
        backlight.parse_now_playing(b"{nope")
    with pytest.raises(ValueError):
        backlight.parse_now_playing(b"[1]")


def test_music_keeps_the_screen_at_screensaver_brightness_in_quiet_hours():
    sched = _schedule(screensaver_on=True, screensaver_pct=40)
    assert sched.target_pct(time(23, 0), 600.0) == 15
    assert sched.target_pct(time(23, 0), 600.0, music=True) == 40
    assert sched.night_dimmed(time(23, 0), 600.0, music=True) is False


def test_apply_once_does_not_night_dim_while_music_shows(tmp_path):
    device = _fake_device(tmp_path, max_brightness=10)
    light = backlight.Backlight(device)
    sched = _schedule(full_pct=100, dim_pct=20, wait_s=60.0)
    # Screensaver off: music means Brightness (no overlay, no night dim).
    assert backlight.apply_once(light, sched, now=time(23, 0), idle_s=900.0, music=True) == 10
    assert backlight.apply_once(light, sched, now=time(23, 0), idle_s=900.0, music=False) == 2


# --------------------------------------------------------------------------- #
# The combined rule (docs/superpowers/specs/2026-11-23-panel-settings-design.md)
# --------------------------------------------------------------------------- #

DAY, NIGHT = time(12, 0), time(23, 0)


def test_before_the_wait_the_level_is_brightness_day_and_night():
    for on in (True, False):
        s = _schedule(screensaver_on=on, wait_s=60.0, full_pct=80)
        for at in (DAY, NIGHT):
            for music in (True, False):
                assert s.target_pct(at, 59.9, music) == 80


def test_day_with_screensaver_on_dims_to_screensaver_pct_after_the_wait():
    s = _schedule(screensaver_on=True, screensaver_pct=40)
    assert s.target_pct(DAY, 60.0) == 40


def test_day_with_screensaver_off_never_dims():
    s = _schedule(screensaver_on=False)
    assert s.target_pct(DAY, 99999.0) == 100


def test_night_no_music_dims_to_night_dim_either_way():
    for on in (True, False):
        s = _schedule(screensaver_on=on, dim_pct=0)
        assert s.target_pct(NIGHT, 60.0) == 0


def test_music_with_screensaver_on_uses_screensaver_pct_by_day_and_night():
    s = _schedule(screensaver_on=True, screensaver_pct=35)
    assert s.target_pct(DAY, 600.0, music=True) == 35
    assert s.target_pct(NIGHT, 600.0, music=True) == 35


def test_music_with_screensaver_off_uses_brightness_and_skips_night_dim():
    s = _schedule(screensaver_on=False, full_pct=90)
    assert s.target_pct(DAY, 600.0, music=True) == 90
    assert s.target_pct(NIGHT, 600.0, music=True) == 90


def test_preview_pct_wins_over_everything():
    s = _schedule(screensaver_on=True)
    assert s.target_pct(NIGHT, 0.0, music=True, preview_pct=55) == 55
    assert s.target_pct(DAY, 9999.0, preview_pct=0) == 0


def test_night_dim_zero_writes_zero_and_a_touch_wakes(tmp_path):
    device = _fake_device(tmp_path, max_brightness=10, brightness=7)
    light = backlight.Backlight(device)
    s = _schedule(dim_pct=0, wait_s=60.0)
    assert backlight.apply_once(light, s, now=NIGHT, idle_s=120.0) == 0
    assert backlight.apply_once(light, s, now=NIGHT, idle_s=0.5) == 10


# --------------------------------------------------------------------------- #
# parse_settings_entry: one panel's document entry over the flag fallback
# --------------------------------------------------------------------------- #

ENTRY = {
    "brightness_pct": 70, "night_dim_pct": 5, "night_start": "21:30", "night_end": "07:00",
    "screensaver_on": True, "screensaver_wait_s": 120, "screensaver_style": "aurora",
    "screensaver_pct": 25,
}


def test_parse_settings_entry_applies_every_key():
    got = backlight.parse_settings_entry(ENTRY, _schedule())
    assert got == backlight.Settings(
        quiet_start=time(21, 30), quiet_end=time(7, 0), full_pct=70, dim_pct=5,
        wait_s=120.0, screensaver_on=True, screensaver_pct=25,
    )


def test_parse_settings_entry_keeps_valid_keys_and_falls_back_per_key():
    base = _schedule()
    got = backlight.parse_settings_entry({**ENTRY, "brightness_pct": 7, "screensaver_wait_s": "x"}, base)
    assert got.full_pct == base.full_pct and got.wait_s == base.wait_s
    assert got.dim_pct == 5 and got.screensaver_pct == 25


def test_parse_settings_entry_rejects_garbage_types_and_equal_times():
    base = _schedule()
    junk = {"brightness_pct": True, "night_dim_pct": 7.5, "night_start": "22:15",
            "screensaver_on": "yes", "screensaver_pct": None}
    assert backlight.parse_settings_entry(junk, base) == base
    same = backlight.parse_settings_entry({"night_start": "06:00", "night_end": "06:00"}, base)
    assert (same.quiet_start, same.quiet_end) == (base.quiet_start, base.quiet_end)
    assert backlight.parse_settings_entry("nope", base) == base
    assert backlight.parse_settings_entry(None, base) == base


def test_now_playing_url_comes_from_the_hub_origin():
    assert backlight._parse_args([]).now_playing_url is None
    args = backlight._parse_args(["--now-playing-url", "http://hub.example/now-playing.json"])
    assert args.now_playing_url == "http://hub.example/now-playing.json"
    assert backlight.now_playing_url("http://hub.example") == "http://hub.example/now-playing.json"
    assert backlight.now_playing_url("http://hub.example/") == "http://hub.example/now-playing.json"
    assert backlight.now_playing_url("") is None
    assert backlight.now_playing_url(None) is None
