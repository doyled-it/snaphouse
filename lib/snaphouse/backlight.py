"""Panel backlight schedule: dim during quiet hours, wake to full on touch.

The tap that wakes a dimmed screen only wakes it: while dim the daemon holds an
exclusive evdev grab on the touchscreen (see _ActivityClock). With --no-grab the
page's screensaver overlay swallows that first tap instead, so the daemon never
grabs and one tap both wakes the screen and dismisses the overlay.

The official 7 inch DSI display exposes its backlight as a sysfs device under
/sys/class/backlight/<name>/, with a writable `brightness` file (0 .. max) and a
read-only `max_brightness`. The exact device name depends on the panel driver
(for example `10-0045` or `rpi_backlight`), so the service discovers it by
globbing rather than hard-coding a name.

This module owns the policy the panel role's snaphouse-backlight service runs. The
levels, the night window and the wait come from the panel's own settings on the hub
(panel-settings.json, read with the now-playing poll, the last good copy cached in
/var/lib/snaphouse/backlight/), with the startup flags as the fallback. The decision
is a pure function of the wall clock, how long since the last touch (a monotonic
count), whether music shows, and an optional preview level (Settings.target_pct, the
combined rule in docs/superpowers/specs/2026-11-23-panel-settings-design.md). The only
side effect is writing `brightness`, so it is testable with an injected clock and a
fake sysfs tree, no hardware involved.

While music shows on the panel (a track playing, or paused less than five minutes ago,
read from the hub's now-playing.json) the night dim is skipped: the screen sits at the
screensaver level when the screensaver is on, and at Brightness when it is off.
MusicWatch keeps that answer; with the hub unreachable for two minutes it counts as
nothing playing.

A loopback-only API (127.0.0.1, answering the hub's origin only, with the CORS and
Private Network Access preflight) lets the page preview a brightness for a few seconds
and ask for an immediate settings read.

Stdlib only: it runs from the on-device venv python but pulls in nothing beyond
the standard library, so it stays light on the Pi 3.
"""

from __future__ import annotations

import argparse
import fcntl
import glob
import os
import select
import json
import struct
import sys
import threading
import time as time_module
import tempfile
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from dataclasses import dataclass, replace
from datetime import datetime, time
from pathlib import Path

DEFAULT_SYS_CLASS = Path("/sys/class/backlight")

# How often the daemon re-evaluates the schedule. A touch is noticed immediately
# (the input wait wakes the loop); this only bounds how soon the quiet-hours edge
# and the post-touch dim take effect.
DEFAULT_INTERVAL_S = 2.0

# evdev ioctls and event codes for the wake-only tap. EVIOCGRAB is
# _IOW('E', 0x90, int): while one reader holds it, no other reader (the
# compositor) receives that device's events. EVIOCGBIT(EV_ABS, 8) reads the
# absolute axes a device reports, which picks the touchscreen out of the event
# nodes (the HDMI keyboard and jack have none).
EVIOCGRAB = 0x40044590
EVIOCGBIT_ABS = (2 << 30) | (8 << 16) | (0x45 << 8) | (0x20 + 3)
EV_SYN, EV_KEY, EV_ABS = 0, 1, 3
SYN_DROPPED = 3
BTN_TOUCH = 0x14A
ABS_X, ABS_MT_SLOT, ABS_MT_POSITION_X, ABS_MT_TRACKING_ID = 0x00, 0x2F, 0x35, 0x39
_EVENT_FMT = "@llHHi"
_EVENT_SIZE = struct.calcsize(_EVENT_FMT)

# After a waking tap, how long the touchscreen may stay silent before the tap is
# treated as over even though no lift was seen. Bounds how long the grab can hold.
LIFT_FALLBACK_S = 0.3

# Where the touchscreen reports events. Any readable event counts as a touch, so
# the daemon never parses the evdev payload; it only needs to know "something
# happened".
DEFAULT_INPUT_GLOB = "/dev/input/event*"


class NoBacklightError(RuntimeError):
    """No backlight device was found under the sysfs class directory."""


def parse_hhmm(value: str) -> time:
    """Parse an "HH:MM" wall time, rejecting anything else."""
    parts = value.split(":")
    if len(parts) != 2 or not all(p.isdigit() for p in parts):
        raise ValueError(f"not an HH:MM time: {value!r}")
    hour, minute = int(parts[0]), int(parts[1])
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError(f"time out of range: {value!r}")
    return time(hour, minute)


# The values a panel's document may carry, checked the same way the hub checks a
# write (lib/snaphouse/panelsettings.py). A bad value never reaches the screen:
# parse_settings_entry falls back per key.
WAIT_CHOICES = (30, 60, 120, 300, 600)


def _valid_pct(value, low: int) -> bool:
    return type(value) is int and low <= value <= 100 and value % 5 == 0


def _valid_time(value) -> bool:
    if not isinstance(value, str) or len(value) != 5:
        return False
    try:
        parsed = parse_hhmm(value)
    except ValueError:
        return False
    return parsed.minute in (0, 30)


@dataclass(frozen=True)
class Settings:
    """The panel's screen policy, independent of any clock or device.

    quiet_start/quiet_end bound the night window (it may wrap past midnight, like
    the house default 22:00 to 06:00). full_pct is Brightness, dim_pct the night
    dim, screensaver_pct the level while the screensaver (or the ambient Now
    Playing view) shows; all are percentages of the panel's max brightness.
    wait_s is the one wait after the last touch, for day and night.
    """

    quiet_start: time
    quiet_end: time
    full_pct: int
    dim_pct: int
    wait_s: float
    screensaver_on: bool = False
    screensaver_pct: int = 40

    def in_quiet_hours(self, now: time) -> bool:
        start, end = self.quiet_start, self.quiet_end
        if start <= end:
            # A same-day window, for example 01:00 to 02:00.
            return start <= now < end
        # A window that wraps past midnight, for example 22:00 to 06:00.
        return now >= start or now < end

    def target_pct(
        self, now: time, idle_s: float, music: bool = False, preview_pct: int | None = None
    ) -> int:
        """The brightness percentage for this moment (the combined rule).

        A preview wins. Before the wait it is Brightness. After it: music (playing,
        or paused under five minutes) shows the screensaver level when the
        screensaver is on and Brightness when it is off (no night dim either way);
        otherwise night is the night dim, and day is the screensaver level when the
        screensaver is on and Brightness when it is off.
        """
        if preview_pct is not None:
            return max(0, min(100, preview_pct))
        if idle_s < self.wait_s:
            return self.full_pct
        saver = self.screensaver_pct if self.screensaver_on else self.full_pct
        if music:
            return saver
        if self.in_quiet_hours(now):
            return self.dim_pct
        return saver

    def night_dimmed(self, now: time, idle_s: float, music: bool) -> bool:
        """True when the level is the night dim (a real dim: a dim level equal to
        Brightness leaves nothing to wake from)."""
        return (
            self.dim_pct != self.full_pct
            and not music
            and idle_s >= self.wait_s
            and self.in_quiet_hours(now)
        )


def parse_settings_entry(entry, fallback: Settings) -> Settings:
    """One panel's entry from panel-settings.json over a fallback.

    Each key is validated alone; an invalid or missing one keeps the fallback's
    value, so a half-bad document still applies what it can. Equal night start and
    end are refused as a pair.
    """
    if not isinstance(entry, dict):
        return fallback
    changes: dict = {}
    if _valid_pct(entry.get("brightness_pct"), 20):
        changes["full_pct"] = entry["brightness_pct"]
    if _valid_pct(entry.get("night_dim_pct"), 0):
        changes["dim_pct"] = entry["night_dim_pct"]
    if _valid_pct(entry.get("screensaver_pct"), 5):
        changes["screensaver_pct"] = entry["screensaver_pct"]
    if type(entry.get("screensaver_on")) is bool:
        changes["screensaver_on"] = entry["screensaver_on"]
    wait = entry.get("screensaver_wait_s")
    if type(wait) is int and wait in WAIT_CHOICES:
        changes["wait_s"] = float(wait)
    start = entry.get("night_start", None)
    end = entry.get("night_end", None)
    new_start = parse_hhmm(start) if _valid_time(start) else fallback.quiet_start
    new_end = parse_hhmm(end) if _valid_time(end) else fallback.quiet_end
    if new_start != new_end:
        changes["quiet_start"], changes["quiet_end"] = new_start, new_end
    return replace(fallback, **changes)


class Backlight:
    """A sysfs backlight device: read max_brightness, write brightness."""

    def __init__(self, device_dir: Path) -> None:
        self.device_dir = Path(device_dir)

    @classmethod
    def discover(cls, sys_class: Path = DEFAULT_SYS_CLASS) -> "Backlight":
        """The first backlight device under sys_class.

        There is exactly one on the panel; globbing avoids depending on the
        driver-specific device name, which differs between kernels.
        """
        candidates = sorted(p for p in Path(sys_class).glob("*") if p.is_dir())
        if not candidates:
            raise NoBacklightError(f"no backlight device under {sys_class}")
        return cls(candidates[0])

    @property
    def max_brightness(self) -> int:
        return int((self.device_dir / "max_brightness").read_text().strip())

    def set_pct(self, pct: int) -> int:
        """Set brightness to pct (0..100) of max; return the raw value written."""
        pct = max(0, min(100, pct))
        value = round(self.max_brightness * pct / 100)
        (self.device_dir / "brightness").write_text(f"{value}\n")
        return value


def apply_once(
    backlight: Backlight,
    settings: Settings,
    *,
    now: time,
    idle_s: float,
    music: bool = False,
    preview_pct: int | None = None,
) -> int:
    """Compute the level for this moment and write it. Returns the raw
    brightness value written, so callers (and tests) can assert the effect."""
    return backlight.set_pct(settings.target_pct(now, idle_s, music, preview_pct))


# How often the hub's now-playing.json is read, how long the last answer is kept
# after reads start failing, and how long a paused track still counts as music.
MUSIC_POLL_S = 10.0
MUSIC_GRACE_S = 120.0
MUSIC_PAUSE_S = 300.0
MUSIC_FETCH_TIMEOUT_S = 3.0


def now_playing_url(origin: str | None) -> str | None:
    """The hub's now-playing.json URL from its origin, or None with no origin."""
    if not origin:
        return None
    return origin.rstrip("/") + "/now-playing.json"


def parse_now_playing(body: bytes) -> dict:
    """Parse a now-playing.json body. Anything that is not a JSON object raises
    ValueError, which the watch treats as a failed read."""
    document = json.loads(body.decode("utf-8", errors="replace"))
    if not isinstance(document, dict):
        raise ValueError("now-playing.json is not an object")
    return document


def fetch_now_playing(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=MUSIC_FETCH_TIMEOUT_S) as response:
        return parse_now_playing(response.read(1 << 20))


class MusicWatch:
    """Whether the panel is showing the ambient Now Playing view: a track is
    playing, or was paused less than five minutes ago.

    It reads the hub's now-playing.json (the `track`, or the paused `resume`
    block the hub keeps while the phone's player is present). The pause is
    measured on this machine's own monotonic clock from the moment it first sees
    the track paused, never from a hub timestamp. A failed read keeps the last
    answer for MUSIC_GRACE_S, then counts as nothing playing.
    """

    def __init__(
        self,
        fetch,
        *,
        clock=time_module.monotonic,
        grace_s: float = MUSIC_GRACE_S,
        pause_s: float = MUSIC_PAUSE_S,
    ) -> None:
        self._fetch = fetch
        self._clock = clock
        self._grace_s = grace_s
        self._pause_s = pause_s
        self._last_ok: float | None = None
        self._status: str | None = None
        self._key: tuple | None = None
        self._paused_since: float | None = None

    def poll(self) -> None:
        try:
            document = self._fetch()
            if not isinstance(document, dict):
                raise ValueError("not a document")
        except Exception:
            return
        now = self._clock()
        entry = document.get("track")
        if not isinstance(entry, dict) or not entry.get("title"):
            entry = document.get("resume")
        if not isinstance(entry, dict) or not entry.get("title"):
            self._status, self._key, self._paused_since = None, None, None
            self._last_ok = now
            return
        key = (entry.get("title"), entry.get("artist"), entry.get("album"))
        if entry.get("status") == "paused":
            if self._status != "paused" or key != self._key or self._paused_since is None:
                self._paused_since = now
            self._status = "paused"
        else:
            self._status = "playing"
            self._paused_since = None
        self._key = key
        self._last_ok = now

    def music_active(self) -> bool:
        now = self._clock()
        if self._last_ok is None or now - self._last_ok > self._grace_s:
            return False
        if self._status == "playing":
            return True
        if self._status == "paused" and self._paused_since is not None:
            return now - self._paused_since < self._pause_s
        return False

    def start(self, interval_s: float = MUSIC_POLL_S) -> threading.Thread:
        """Poll on a daemon thread so a slow hub never delays a touch."""

        def loop() -> None:
            while True:
                self.poll()
                time_module.sleep(interval_s)

        thread = threading.Thread(target=loop, name="music-watch", daemon=True)
        thread.start()
        return thread


class _Fingers:
    """Which contacts are down on one touchscreen, from its evdev stream."""

    def __init__(self) -> None:
        self._touch = False
        self._slot = 0
        self._live: set[int] = set()

    @property
    def down(self) -> bool:
        return self._touch or bool(self._live)

    def feed(self, etype: int, code: int, value: int) -> None:
        if etype == EV_KEY and code == BTN_TOUCH:
            self._touch = bool(value)
            if not value:
                self._live.clear()
        elif etype == EV_ABS and code == ABS_MT_SLOT:
            self._slot = value
        elif etype == EV_ABS and code == ABS_MT_TRACKING_ID:
            if value < 0:
                self._live.discard(self._slot)
            else:
                self._live.add(self._slot)
        elif etype == EV_SYN and code == SYN_DROPPED:
            # Events were lost, so the picture is stale: start again from "up".
            self._touch = False
            self._live.clear()


class _ActivityClock:
    """Tracks the last touch as a monotonic timestamp, and waits for the next one.

    Any readable event on any of the touchscreen's evdev nodes counts as a touch.
    wait() blocks up to `timeout` for an event, draining whatever arrives, and
    reports whether something happened so the caller can refresh immediately.

    The touchscreen node can appear after the daemon starts: a power cut brings the
    daemon up before the driver binds, or the node is enumerated late. So the glob
    is rescanned on every wait(), opening any node that has shown up since, rather
    than globbed once at start and then sleeping forever against an empty fd set.

    Wake-only tap. While the screen is dim, maintain(dim=True) holds an exclusive
    grab (EVIOCGRAB) on the touchscreen nodes, so the compositor receives nothing
    and the waking tap cannot toggle a room. That tap is swallowed until every
    finger lifts (or the screen stays silent for LIFT_FALLBACK_S), then the grab is
    released so the next tap reaches the page. It never grabs while the level is
    full, and never while a finger is already down (grabbing mid-touch would hand
    the compositor a contact with no start). Only nodes that report absolute axes
    are grabbed, so the HDMI keyboard and jack are left alone.
    """

    def __init__(
        self,
        input_glob: str,
        *,
        monotonic=time_module.monotonic,
        opener=os.open,
        ioctl=fcntl.ioctl,
        closer=os.close,
    ) -> None:
        self._monotonic = monotonic
        self._opener = opener
        self._ioctl = ioctl
        self._closer = closer
        self._input_glob = input_glob
        self._last = monotonic()
        # Keyed by path so a rescan does not reopen a node already watched.
        self._fds: dict[str, int] = {}
        self._touchscreens: set[str] = set()
        self._fingers: dict[str, _Fingers] = {}
        self._pending: dict[str, bytes] = {}
        # Paths currently grabbed, tracked per node so one that fails to grab does
        # not make a retry hit EBUSY on the nodes that already succeeded.
        self._grabbed_paths: set[str] = set()
        self._want_grab = False
        # Set when a swallowed touch was force-released with the finger possibly
        # still down; no re-grab until a real lift has been seen.
        self._await_lift = False
        self._swallowing = False
        self._last_data = monotonic()
        # A self-pipe so another thread (the local API, the poll thread) can wake
        # wait() at once without it counting as a touch.
        self._wake_r, self._wake_w = os.pipe()
        os.set_blocking(self._wake_r, False)
        os.set_blocking(self._wake_w, False)
        self._rescan()

    def _is_touchscreen(self, fd: int) -> bool:
        try:
            bits = self._ioctl(fd, EVIOCGBIT_ABS, bytes(8))
        except OSError:
            return False
        def has(code: int) -> bool:
            return bool(bits[code // 8] & (1 << (code % 8)))
        return has(ABS_MT_POSITION_X) or has(ABS_X)

    def _rescan(self) -> None:
        for path in sorted(glob.glob(self._input_glob)):
            if path in self._fds:
                continue
            try:
                fd = self._opener(path, os.O_RDONLY | os.O_NONBLOCK)
            except OSError:
                # A node we cannot open (permissions, hot-unplug) is skipped
                # rather than fatal; the others still report touches.
                continue
            self._fds[path] = fd
            if self._is_touchscreen(fd):
                self._touchscreens.add(path)
                self._fingers[path] = _Fingers()
                if self._want_grab and self._grab_one(fd, True):
                    self._grabbed_paths.add(path)

    def _grab_one(self, fd: int, on: bool) -> bool:
        try:
            self._ioctl(fd, EVIOCGRAB, 1 if on else 0)
        except OSError as exc:
            print(f"snaphouse-backlight: EVIOCGRAB {int(on)} failed: {exc}", file=sys.stderr)
            return False
        return True

    def _finger_down(self) -> bool:
        return any(f.down for f in self._fingers.values())

    @property
    def idle_s(self) -> float:
        return self._monotonic() - self._last

    def maintain(self, *, dim: bool) -> None:
        """Hold or drop the touchscreen grab to match the scheduled level.

        Call once per loop after wait(). `dim` is whether the scheduled level is
        the dim level (Schedule.is_dim).
        """
        if self._swallowing:
            quiet = self._monotonic() - self._last_data >= LIFT_FALLBACK_S
            if self._finger_down() and not quiet:
                return
            self._set_grab(False)
            self._swallowing = False
            # Forced out by silence with a contact still tracked: wait for the real
            # lift before grabbing again, so a held finger is never grabbed mid-touch.
            self._await_lift = self._finger_down()
            return
        if dim and not self._finger_down() and not self._await_lift:
            self._set_grab(True)
        elif not dim and self._grabbed_paths:
            self._set_grab(False)

    def _set_grab(self, on: bool) -> None:
        self._want_grab = on
        for path in sorted(self._touchscreens):
            if on == (path in self._grabbed_paths):
                continue
            if self._grab_one(self._fds[path], on):
                (self._grabbed_paths.add if on else self._grabbed_paths.discard)(path)
            elif not on:
                # A release that fails (node gone) leaves nothing held.
                self._grabbed_paths.discard(path)

    def _drop(self, path: str) -> None:
        """Close a node that went away and forget it everywhere."""
        fd = self._fds.pop(path, None)
        if fd is not None:
            try:
                self._closer(fd)
            except OSError:
                pass
        self._touchscreens.discard(path)
        self._fingers.pop(path, None)
        self._pending.pop(path, None)
        self._grabbed_paths.discard(path)

    def _feed(self, path: str, data: bytes) -> None:
        fingers = self._fingers.get(path)
        if fingers is None:
            return
        buf = self._pending.pop(path, b"") + data
        usable = len(buf) - len(buf) % _EVENT_SIZE
        for off in range(0, usable, _EVENT_SIZE):
            _s, _u, etype, code, value = struct.unpack_from(_EVENT_FMT, buf, off)
            fingers.feed(etype, code, value)
        if usable < len(buf):
            self._pending[path] = buf[usable:]

    def nudge(self) -> None:
        """Wake wait() now, from any thread. It is not a touch."""
        try:
            os.write(self._wake_w, b"\0")
        except OSError:
            pass  # the pipe is full: a wake is already pending

    def _drain_wake(self) -> None:
        try:
            while os.read(self._wake_r, 4096):
                pass
        except OSError:
            pass

    def wait(self, timeout: float) -> bool:
        # Pick up a touchscreen that was not there at start before deciding how to
        # wait, so a late node starts waking the screen without a daemon restart.
        self._rescan()
        if not self._fds:
            # Nothing to watch yet: wait on the wake pipe alone (a sleep that a
            # nudge can cut short).
            select.select([self._wake_r], [], [], timeout)
            self._drain_wake()
            return False
        if self._swallowing:
            # Wake in time to notice a lift that never arrives.
            left = LIFT_FALLBACK_S - (self._monotonic() - self._last_data)
            timeout = min(timeout, max(left, 0.01))
        by_fd = {fd: path for path, fd in self._fds.items()}
        readable, _, _ = select.select([self._wake_r, *self._fds.values()], [], [], timeout)
        if self._wake_r in readable:
            self._drain_wake()
            readable = [fd for fd in readable if fd != self._wake_r]
        if not readable:
            return False
        activity = False
        for fd in readable:
            path = by_fd[fd]
            chunks = []
            gone = False
            try:
                while True:
                    chunk = os.read(fd, 4096)
                    if not chunk:
                        gone = True  # EOF: the device was unplugged
                        break
                    chunks.append(chunk)
            except BlockingIOError:
                pass
            except OSError:
                gone = True  # ENODEV and friends
            self._feed(path, b"".join(chunks))
            if gone:
                self._drop(path)
                if not chunks:
                    continue
            activity = True
        if not activity:
            return False
        if self._await_lift and not self._finger_down():
            self._await_lift = False
        now = self._monotonic()
        self._last = now
        self._last_data = now
        if self._grabbed_paths:
            self._swallowing = True
        return True


def grab_wanted(*, dimmed: bool, screensaver_on: bool, force_grab: bool, no_grab: bool) -> bool:
    """Whether to hold the touchscreen grab for this pass.

    The grab (the #184 behaviour) keeps the waking tap from reaching the page. It
    holds only while the screen is dimmed to the night level with the screensaver
    off: with the screensaver on, its overlay swallows the first tap itself, so one
    tap both brightens the screen and dismisses it. force_grab
    (panel_backlight.grab_wake_tap) grabs whenever the night dim is showing; no_grab
    never grabs.
    """
    if no_grab or not dimmed:
        return False
    return force_grab or not screensaver_on


# --------------------------------------------------------------------------- #
# Settings from the hub, the preview, and the loopback API
# --------------------------------------------------------------------------- #

PREVIEW_MAX_S = 15.0
LOCAL_BODY_MAX = 1024
DEFAULT_SETTINGS_CACHE = "/var/lib/snaphouse/backlight/settings.json"


def fetch_json(url: str) -> dict:
    """GET a JSON document from the hub (the same bounded read as now-playing)."""
    return fetch_now_playing(url)


class SettingsWatch:
    """This panel's settings, read from the hub's panel-settings.json.

    It starts from the last good copy on disk (so a hub that is down or rebooting
    never changes the screen), falling back to the startup flags when there is no
    cache. A good read replaces the current settings and rewrites the cache
    atomically; a failed or malformed read keeps what it has. Only an entry for
    this panel counts: a document with none leaves the fallback in force.
    """

    def __init__(self, fetch, *, panel_id, fallback: Settings, cache_path: Path | None) -> None:
        self._fetch = fetch
        self._panel_id = panel_id
        self._fallback = fallback
        self._cache_path = Path(cache_path) if cache_path is not None else None
        self.current = fallback
        self._load_cache()

    def _load_cache(self) -> None:
        if self._cache_path is None:
            return
        try:
            entry = json.loads(self._cache_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if isinstance(entry, dict):
            self.current = parse_settings_entry(entry, self._fallback)

    def _store_cache(self, entry: dict) -> None:
        if self._cache_path is None:
            return
        try:
            self._cache_path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=self._cache_path.parent, prefix=".settings-")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump(entry, handle, sort_keys=True)
                os.replace(tmp, self._cache_path)
            except BaseException:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise
        except OSError as exc:
            print(f"snaphouse-backlight: settings cache not written: {exc}", file=sys.stderr)

    def poll(self) -> None:
        try:
            document = self._fetch()
            if not isinstance(document, dict) or document.get("version") != 1:
                return
            panels = document.get("panels")
            entry = panels.get(self._panel_id) if isinstance(panels, dict) else None
            if not isinstance(entry, dict):
                return
        except Exception:
            return
        self.current = parse_settings_entry(entry, self._fallback)
        self._store_cache(entry)


class PreviewState:
    """A brightness shown for a few seconds (a tap on a brightness bar, or the
    screensaver preview), then released. Seconds are capped at PREVIEW_MAX_S and
    zero cancels. Set from the local API thread, read by the main loop."""

    def __init__(self, clock=time_module.monotonic) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._pct: int | None = None
        self._until = 0.0

    def set(self, pct: int, seconds: float) -> None:
        with self._lock:
            if seconds <= 0:
                self._pct = None
                return
            self._pct = pct
            self._until = self._clock() + min(float(seconds), PREVIEW_MAX_S)

    def active_pct(self) -> int | None:
        with self._lock:
            if self._pct is not None and self._clock() < self._until:
                return self._pct
            return None

    def remaining(self) -> float | None:
        with self._lock:
            if self._pct is None:
                return None
            left = self._until - self._clock()
            return left if left > 0 else None


def make_local_server(
    preview: PreviewState, *, on_refresh, on_change, allow_origin: str, port: int
) -> ThreadingHTTPServer:
    """The loopback-only API the panel page calls: POST /preview {pct, seconds}
    and POST /refresh. The page is served from the hub, so the browser sends a
    CORS and Private Network Access preflight; only allow_origin (the hub) is
    answered. A request with no Origin header (a local tool) is allowed, any other
    origin is refused with 403."""

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):  # noqa: A002 - signature
            pass

        def _origin_ok(self) -> bool:
            origin = self.headers.get("Origin")
            return origin is None or origin == allow_origin

        def _reply(self, status: int) -> None:
            self.send_response(status)
            if self.headers.get("Origin") == allow_origin:
                self.send_header("Access-Control-Allow-Origin", allow_origin)
                self.send_header("Vary", "Origin")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_OPTIONS(self):  # noqa: N802
            if self.headers.get("Origin") != allow_origin:
                self._reply(403)
                return
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", allow_origin)
            self.send_header("Access-Control-Allow-Private-Network", "true")
            self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Access-Control-Max-Age", "600")
            self.send_header("Vary", "Origin")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_POST(self):  # noqa: N802
            if not self._origin_ok():
                self._reply(403)
                return
            path = self.path.split("?", 1)[0]
            if path not in ("/preview", "/refresh"):
                self._reply(404)
                return
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = -1
            if length < 0 or length > LOCAL_BODY_MAX:
                self._reply(400)
                return
            raw = self.rfile.read(length) if length else b""
            if path == "/refresh":
                on_refresh()
                self._reply(204)
                return
            try:
                body = json.loads(raw.decode("utf-8"))
            except ValueError:
                self._reply(400)
                return
            pct = body.get("pct") if isinstance(body, dict) else None
            seconds = body.get("seconds") if isinstance(body, dict) else None
            if (
                type(pct) is not int
                or not 0 <= pct <= 100
                or type(seconds) not in (int, float)
                or seconds < 0
            ):
                self._reply(400)
                return
            preview.set(pct, seconds)
            on_change()
            self._reply(204)

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    return server


def poll_loop(pollers, interval_s: float, wake: threading.Event, *, stop=None, after=None) -> None:
    """Poll every watch (now-playing, settings) on one thread, every interval or as
    soon as `wake` is set (a refresh from the local API). `after` runs after each
    round so the main loop can pick the change up at once."""
    while stop is None or not stop.is_set():
        for poller in pollers:
            try:
                poller.poll()
            except Exception as exc:  # noqa: BLE001 - one bad watch never stops the rest
                print(f"snaphouse-backlight: poll failed: {exc}", file=sys.stderr)
        if after is not None:
            after()
        wake.wait(interval_s)
        wake.clear()


def _build_settings(args: argparse.Namespace) -> Settings:
    """The flag values: what the screen uses with no cache and no hub."""
    return Settings(
        quiet_start=parse_hhmm(args.quiet_start),
        quiet_end=parse_hhmm(args.quiet_end),
        full_pct=args.full_pct,
        dim_pct=args.dim_pct,
        wait_s=args.dim_timeout,
        screensaver_on=args.screensaver == "on",
        screensaver_pct=args.screensaver_pct,
    )


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="snaphouse panel backlight daemon")
    parser.add_argument("--sys-class", default=str(DEFAULT_SYS_CLASS))
    parser.add_argument("--input-glob", default=DEFAULT_INPUT_GLOB)
    parser.add_argument("--full-pct", type=int, default=100)
    parser.add_argument("--dim-pct", type=int, default=15)
    parser.add_argument("--dim-timeout", type=float, default=60.0)
    parser.add_argument("--quiet-start", default="22:00")
    parser.add_argument("--quiet-end", default="06:00")
    parser.add_argument("--interval", type=float, default=DEFAULT_INTERVAL_S)
    parser.add_argument(
        "--now-playing-url",
        default=None,
        help="the hub's now-playing.json; with it, no night dim while music shows",
    )
    parser.add_argument(
        "--screensaver",
        choices=("on", "off"),
        default="off",
        help="fallback for the screensaver switch until the hub's settings are read",
    )
    parser.add_argument("--screensaver-pct", type=int, default=40)
    parser.add_argument("--panel-id", default=None)
    parser.add_argument("--settings-url", default=None, help="the hub's panel-settings.json")
    parser.add_argument("--settings-cache", default=DEFAULT_SETTINGS_CACHE)
    parser.add_argument(
        "--local-port", type=int, default=None, help="loopback port for the preview API"
    )
    parser.add_argument(
        "--allow-origin", default=None, help="the one browser origin the local API answers"
    )
    parser.add_argument(
        "--no-grab",
        action="store_true",
        help="never grab the touchscreen; the page screensaver swallows the waking tap",
    )
    parser.add_argument(
        "--force-grab",
        action="store_true",
        help="grab the touchscreen whenever the night dim shows, even with the screensaver on",
    )
    return parser.parse_args(argv)


def run(argv: list[str], *, now_time=None) -> int:
    """The daemon loop: set the level, then wait for a touch, a nudge or the
    interval and set it again. now_time returns the current wall clock time()."""
    args = _parse_args(argv)
    now_time = now_time or (lambda: datetime.now().time())
    fallback = _build_settings(args)
    backlight = Backlight.discover(Path(args.sys_class))
    activity = _ActivityClock(args.input_glob)
    preview = PreviewState()
    music = None
    watch = None
    pollers = []
    if args.now_playing_url:
        url = args.now_playing_url
        music = MusicWatch(lambda: fetch_now_playing(url))
        pollers.append(music)
    if args.settings_url and args.panel_id:
        settings_url = args.settings_url
        watch = SettingsWatch(
            lambda: fetch_json(settings_url),
            panel_id=args.panel_id,
            fallback=fallback,
            cache_path=Path(args.settings_cache),
        )
    refresh = threading.Event()
    if pollers or watch is not None:
        if watch is not None:
            pollers.append(watch)
        threading.Thread(
            target=poll_loop,
            args=(pollers, MUSIC_POLL_S, refresh),
            kwargs={"after": activity.nudge},
            name="hub-poll",
            daemon=True,
        ).start()
    if args.local_port is not None and args.allow_origin:
        server = make_local_server(
            preview,
            on_refresh=refresh.set,
            on_change=activity.nudge,
            allow_origin=args.allow_origin,
            port=args.local_port,
        )
        threading.Thread(target=server.serve_forever, name="local-api", daemon=True).start()
    while True:
        settings = watch.current if watch is not None else fallback
        now = now_time()
        playing = music is not None and music.music_active()
        idle = activity.idle_s
        held = preview.active_pct()
        apply_once(backlight, settings, now=now, idle_s=idle, music=playing, preview_pct=held)
        # While dimmed to the night level with the screensaver off, hold the
        # touchscreen so the waking tap only wakes the screen. Never during a preview.
        activity.maintain(
            dim=held is None
            and grab_wanted(
                dimmed=settings.night_dimmed(now, idle, playing),
                screensaver_on=settings.screensaver_on,
                force_grab=args.force_grab,
                no_grab=args.no_grab,
            )
        )
        # Wake early on a touch or a nudge so the screen follows at once; otherwise
        # re-check on the interval (and when a preview ends).
        left = preview.remaining()
        activity.wait(args.interval if left is None else min(args.interval, left + 0.05))


def main(argv: list[str] | None = None) -> int:
    try:
        return run(sys.argv[1:] if argv is None else argv)
    except NoBacklightError as exc:
        print(f"snaphouse-backlight: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
