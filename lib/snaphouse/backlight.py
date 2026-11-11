"""Panel backlight schedule: dim during quiet hours, wake to full on touch.

The official 7 inch DSI display exposes its backlight as a sysfs device under
/sys/class/backlight/<name>/, with a writable `brightness` file (0 .. max) and a
read-only `max_brightness`. The exact device name depends on the panel driver
(for example `10-0045` or `rpi_backlight`), so the service discovers it by
globbing rather than hard-coding a name.

This module owns the schedule the panel role's snaphouse-backlight service runs:
during the shared quiet hours the screen sits at a low level and wakes to full on
a touch, returning to the low level after a short idle; outside quiet hours it
stays full. The decision is a pure function of the wall clock (for the
quiet-hours window) and how long since the last touch (an idle count from a
monotonic clock), and the only side effect is writing `brightness`. That keeps it
testable with an injected clock and a fake sysfs tree, no hardware involved.

Stdlib only: it runs from the on-device venv python but pulls in nothing beyond
the standard library, so it stays light on the Pi 3.
"""

from __future__ import annotations

import argparse
import glob
import os
import select
import sys
import time as time_module
from dataclasses import dataclass
from datetime import datetime, time
from pathlib import Path

DEFAULT_SYS_CLASS = Path("/sys/class/backlight")

# How often the daemon re-evaluates the schedule. A touch is noticed immediately
# (the input wait wakes the loop); this only bounds how soon the quiet-hours edge
# and the post-touch dim take effect.
DEFAULT_INTERVAL_S = 2.0

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


@dataclass(frozen=True)
class Schedule:
    """The dim/wake policy, independent of any clock or device.

    quiet_start/quiet_end bound the low-light window (it may wrap past midnight,
    like the house default 22:00 to 06:00). full_pct/dim_pct are percentages of
    the panel's max brightness. dim_timeout_s is how long after the last touch
    the screen returns to dim while inside quiet hours.
    """

    quiet_start: time
    quiet_end: time
    full_pct: int
    dim_pct: int
    dim_timeout_s: float

    def in_quiet_hours(self, now: time) -> bool:
        start, end = self.quiet_start, self.quiet_end
        if start <= end:
            # A same-day window, for example 01:00 to 02:00.
            return start <= now < end
        # A window that wraps past midnight, for example 22:00 to 06:00.
        return now >= start or now < end

    def target_pct(self, now: time, idle_s: float) -> int:
        """The brightness percentage for this moment.

        Outside quiet hours the screen is always full. Inside quiet hours it is
        full for dim_timeout_s after the last touch, then dim.
        """
        if not self.in_quiet_hours(now):
            return self.full_pct
        if idle_s < self.dim_timeout_s:
            return self.full_pct
        return self.dim_pct


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


def apply_once(backlight: Backlight, schedule: Schedule, *, now: time, idle_s: float) -> int:
    """Compute the scheduled level for this moment and write it. Returns the raw
    brightness value written, so callers (and tests) can assert the effect."""
    return backlight.set_pct(schedule.target_pct(now, idle_s))


class _ActivityClock:
    """Tracks the last touch as a monotonic timestamp, and waits for the next one.

    Any readable event on any of the touchscreen's evdev nodes counts as a touch.
    wait() blocks up to `timeout` for an event, draining whatever arrives, and
    reports whether something happened so the caller can refresh immediately.

    The touchscreen node can appear after the daemon starts: a power cut brings the
    daemon up before the driver binds, or the node is enumerated late. So the glob
    is rescanned on every wait(), opening any node that has shown up since, rather
    than globbed once at start and then sleeping forever against an empty fd set.
    """

    def __init__(
        self,
        input_glob: str,
        *,
        monotonic=time_module.monotonic,
        opener=os.open,
    ) -> None:
        self._monotonic = monotonic
        self._opener = opener
        self._input_glob = input_glob
        self._last = monotonic()
        # Keyed by path so a rescan does not reopen a node already watched.
        self._fds: dict[str, int] = {}
        self._rescan()

    def _rescan(self) -> None:
        for path in sorted(glob.glob(self._input_glob)):
            if path in self._fds:
                continue
            try:
                self._fds[path] = self._opener(path, os.O_RDONLY | os.O_NONBLOCK)
            except OSError:
                # A node we cannot open (permissions, hot-unplug) is skipped
                # rather than fatal; the others still report touches.
                continue

    @property
    def idle_s(self) -> float:
        return self._monotonic() - self._last

    def wait(self, timeout: float) -> bool:
        # Pick up a touchscreen that was not there at start before deciding how to
        # wait, so a late node starts waking the screen without a daemon restart.
        self._rescan()
        if not self._fds:
            time_module.sleep(timeout)
            return False
        readable, _, _ = select.select(list(self._fds.values()), [], [], timeout)
        if not readable:
            return False
        for fd in readable:
            try:
                while os.read(fd, 4096):
                    pass
            except (BlockingIOError, OSError):
                pass
        self._last = self._monotonic()
        return True


def _build_schedule(args: argparse.Namespace) -> Schedule:
    return Schedule(
        quiet_start=parse_hhmm(args.quiet_start),
        quiet_end=parse_hhmm(args.quiet_end),
        full_pct=args.full_pct,
        dim_pct=args.dim_pct,
        dim_timeout_s=args.dim_timeout,
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
    return parser.parse_args(argv)


def run(argv: list[str], *, now_time=None) -> int:
    """The daemon loop: set the level, then wait for a touch or the interval and
    set it again. now_time returns the current wall clock time()."""
    args = _parse_args(argv)
    now_time = now_time or (lambda: datetime.now().time())
    schedule = _build_schedule(args)
    backlight = Backlight.discover(Path(args.sys_class))
    activity = _ActivityClock(args.input_glob)
    while True:
        apply_once(backlight, schedule, now=now_time(), idle_s=activity.idle_s)
        # Wake early on a touch so the screen lights up at once; otherwise re-check
        # on the interval so the quiet-hours edge and the post-touch dim land.
        activity.wait(args.interval)


def main(argv: list[str] | None = None) -> int:
    try:
        return run(sys.argv[1:] if argv is None else argv)
    except NoBacklightError as exc:
        print(f"snaphouse-backlight: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
