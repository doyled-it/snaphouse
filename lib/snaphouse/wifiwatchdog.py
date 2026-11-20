"""Wi-Fi watchdog for the Pis (issue #178).

A Pi once lost its Wi-Fi link in a brief network blip, never rejoined, and sat
offline for about 12 hours until a power cycle. This runs once a minute from a
systemd timer (wifi-watchdog tasks in the `common` role) on machines that have a
wlan0 and a NetworkManager Wi-Fi profile.

The signal is the default gateway from `ip route`. No default route is down. A
ping that answers is up. A failed ping alone is not enough to call it down: the
link is also up when NetworkManager reports wlan0 connected (state 100) and the
gateway's neighbour entry on wlan0 is REACHABLE or STALE with a link address.
Down is no route, or a failed ping and a failed fallback. mDNS and the hub are
never used (the hub reboots nightly).

Escalation:

1. Down for restart_after_s (5 minutes) in a row: restart the Wi-Fi link, once.
2. Still down reboot_after_restart_s (10 minutes) after that restart: reboot, but
   only when the machine has been up at least reboot_min_interval_s (an hour).

Timing never uses the wall clock: the Pis have no RTC and the clock is stale
until NTP syncs, which cannot happen while Wi-Fi is dead. Durations and uptime
come from CLOCK_BOOTTIME. The running state lives under /run, so any reboot
starts clean, and "uptime at least an hour" is what makes it at most one
watchdog reboot an hour. The only thing kept across reboots is a last-reboot
stamp (wall time, for people reading the log).

Every step logs with the `wifi-watchdog:` tag. Stdlib only; commands, clock and
sleep are injected so the tests need no network.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

# Running state is under /run (RuntimeDirectory in the unit) so a reboot for any
# reason starts clean. Only the last-reboot stamp persists.
DEFAULT_STATE_DIR = Path("/run/snaphouse-wifi-watchdog")
DEFAULT_STAMP_DIR = Path("/var/lib/snaphouse/wifi-watchdog")
INTERFACE = "wlan0"

RESTART_AFTER_S = 300
REBOOT_AFTER_RESTART_S = 600
REBOOT_MIN_INTERVAL_S = 3600

Runner = Callable[[list[str]], tuple[int, str]]


def run_command(cmd: list[str]) -> tuple[int, str]:
    try:
        done = subprocess.run(cmd, capture_output=True, text=True, timeout=60, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, str(exc)
    return done.returncode, done.stdout


def boot_seconds() -> float:
    """Seconds since boot, including suspend. Not affected by clock changes."""
    clock_id = getattr(time, "CLOCK_BOOTTIME", None)
    if clock_id is None:  # not Linux (development machines)
        return time.monotonic()
    return time.clock_gettime(clock_id)


def parse_gateway(route_output: str) -> str | None:
    """The gateway address on the first `default via <addr>` line, or None."""
    for line in route_output.splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[0] == "default" and parts[1] == "via":
            return parts[2]
    return None


def neighbour_is_live(neigh_output: str) -> bool:
    """True for an `ip neigh` line with a link address that is REACHABLE or STALE."""
    for line in neigh_output.splitlines():
        parts = line.split()
        if "lladdr" in parts and parts and parts[-1] in ("REACHABLE", "STALE"):
            return True
    return False


class Watchdog:
    def __init__(
        self,
        run: Runner = run_command,
        clock: Callable[[], float] = boot_seconds,
        state_dir: Path = DEFAULT_STATE_DIR,
        stamp_dir: Path = DEFAULT_STAMP_DIR,
        wall: Callable[[], float] = time.time,
        log: Callable[[str], None] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        restart_after_s: int = RESTART_AFTER_S,
        reboot_after_restart_s: int = REBOOT_AFTER_RESTART_S,
        reboot_min_interval_s: int = REBOOT_MIN_INTERVAL_S,
    ) -> None:
        self.run = run
        self.clock = clock
        self.wall = wall
        self.state_path = Path(state_dir) / "state.json"
        self.stamp_path = Path(stamp_dir) / "last-reboot"
        self.log_out = log or (lambda line: print(line, flush=True))
        self.sleep = sleep
        self.restart_after_s = restart_after_s
        self.reboot_after_restart_s = reboot_after_restart_s
        self.reboot_min_interval_s = reboot_min_interval_s

    def log(self, message: str) -> None:
        self.log_out(f"wifi-watchdog: {message}")

    # State ---------------------------------------------------------------

    def _load(self) -> dict:
        blank = {"down_since": None, "restarted_at": None, "reboot_skip_logged": False}
        try:
            data = json.loads(self.state_path.read_text())
        except (OSError, ValueError):
            return blank
        if not isinstance(data, dict):
            return blank
        return {**blank, **data}

    def _save(self, state: dict) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state))
        tmp.replace(self.state_path)

    # Signal --------------------------------------------------------------

    def gateway_up(self) -> bool:
        _rc, out = self.run(["ip", "-4", "route", "show", "default"])
        gateway = parse_gateway(out)
        if gateway is None:
            return False
        rc, _ = self.run(["ping", "-c", "2", "-W", "2", gateway])
        if rc == 0:
            return True
        # A lost ping alone is not proof the link is down (a gateway can drop
        # ICMP). Trust NetworkManager plus a live neighbour entry instead.
        _rc, state = self.run(["nmcli", "-g", "GENERAL.STATE", "device", "show", INTERFACE])
        if not state.strip().startswith("100"):
            return False
        _rc, neigh = self.run(["ip", "-4", "neigh", "show", gateway, "dev", INTERFACE])
        return neighbour_is_live(neigh)

    # Actions -------------------------------------------------------------

    def _nmcli(self, *args: str) -> None:
        rc, out = self.run(["nmcli", *args])
        lines = out.strip().splitlines()
        short = " ".join(lines[0].split())[:80] if lines else ""
        self.log(f"nmcli {' '.join(args)}: rc={rc} {short}".rstrip())

    def restart_wifi(self) -> None:
        # Cycle the radio, then rescan, then ask NetworkManager to connect the
        # device. The rescan comes first so the scan list is fresh after the radio
        # comes back (a connect against an empty list fails with "no suitable
        # network"). The explicit `device connect` matters: NetworkManager stops
        # autoconnecting once its retries run out, and only a manual activation
        # overrides that.
        self._nmcli("radio", "wifi", "off")
        self.sleep(3)
        self._nmcli("radio", "wifi", "on")
        self.sleep(5)
        self._nmcli("device", "wifi", "rescan", "ifname", INTERFACE)
        self.sleep(5)
        self._nmcli("device", "connect", INTERFACE)

    def reboot_allowed(self, uptime: float) -> bool:
        # Up at least an hour means the last reboot (ours or anyone's) was more
        # than an hour ago, with no wall clock needed.
        return uptime >= self.reboot_min_interval_s

    # One pass ------------------------------------------------------------

    def check_once(self) -> str:
        """One check. Returns what it did: ok, down, restart, reboot or skip."""
        state = self._load()
        now = self.clock()

        if self.gateway_up():
            if state["down_since"] is not None:
                self.log(f"gateway answers again after {int(now - state['down_since'])}s")
            self._save({"down_since": None, "restarted_at": None, "reboot_skip_logged": False})
            return "ok"

        down_since = state["down_since"]
        if down_since is None or down_since > now:
            # First failure, or state from another boot: start counting again.
            down_since = now
            state = {"down_since": now, "restarted_at": None, "reboot_skip_logged": False}
            self.log("gateway unreachable, counting")
        if state["restarted_at"] is not None and state["restarted_at"] > now:
            state["restarted_at"] = None

        action = "down"
        if state["restarted_at"] is None:
            if now - down_since >= self.restart_after_s:
                self.log(f"gateway down for {int(now - down_since)}s, restarting Wi-Fi")
                self.restart_wifi()
                state["restarted_at"] = self.clock()
                action = "restart"
        elif now - state["restarted_at"] >= self.reboot_after_restart_s:
            if self.reboot_allowed(now):
                self.log(
                    f"still down {int(now - state['restarted_at'])}s after the Wi-Fi restart, rebooting"
                )
                self.stamp_path.parent.mkdir(parents=True, exist_ok=True)
                self.stamp_path.write_text(f"{self.wall()}\n")
                try:
                    self.state_path.unlink()
                except OSError:
                    pass
                self.run(["systemctl", "reboot"])
                return "reboot"
            if not state["reboot_skip_logged"]:
                self.log(
                    f"still down but up only {int(now)}s (under {self.reboot_min_interval_s}s), "
                    "so a reboot already happened within the hour, not rebooting"
                )
                state["reboot_skip_logged"] = True
            action = "skip"

        self._save(state)
        return action


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="snaphouse Wi-Fi watchdog (one check)")
    parser.add_argument("--restart-after", type=int, default=RESTART_AFTER_S)
    parser.add_argument("--reboot-after-restart", type=int, default=REBOOT_AFTER_RESTART_S)
    parser.add_argument("--reboot-min-interval", type=int, default=REBOOT_MIN_INTERVAL_S)
    args = parser.parse_args(argv)
    Watchdog(
        restart_after_s=args.restart_after,
        reboot_after_restart_s=args.reboot_after_restart,
        reboot_min_interval_s=args.reboot_min_interval,
    ).check_once()
    return 0


if __name__ == "__main__":
    sys.exit(main())
