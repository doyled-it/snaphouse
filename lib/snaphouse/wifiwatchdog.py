"""Wi-Fi watchdog for the Pis (issue #178).

A Pi once lost its Wi-Fi link in a brief network blip, never rejoined, and sat
offline for about 12 hours until a power cycle. This runs once a minute from a
systemd timer (wifi-watchdog role tasks in `common`) on machines whose wlan0 is
managed by NetworkManager.

The signal is the default gateway from `ip route`: it must exist and answer a
ping. It does not use mDNS or the hub, which reboots nightly. Escalation:

1. Down for restart_after_s (5 minutes) in a row: restart the Wi-Fi link, once.
2. Still down reboot_after_restart_s (10 minutes) after that restart: reboot, but
   never twice within reboot_min_interval_s (an hour). The reboot time is
   stamped on disk so the limit survives the reboot.

The gateway answering clears the counters. Every step is logged to the journal
with the `wifi-watchdog:` tag. Stdlib only; commands, clock and sleep are
injected so the tests need no network.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

DEFAULT_STATE_DIR = Path("/var/lib/snaphouse/wifi-watchdog")
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


def parse_gateway(route_output: str) -> str | None:
    """The gateway address on the first `default via <addr>` line, or None."""
    for line in route_output.splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[0] == "default" and parts[1] == "via":
            return parts[2]
    return None


class Watchdog:
    def __init__(
        self,
        run: Runner = run_command,
        now: Callable[[], float] = time.time,
        state_dir: Path = DEFAULT_STATE_DIR,
        log: Callable[[str], None] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        restart_after_s: int = RESTART_AFTER_S,
        reboot_after_restart_s: int = REBOOT_AFTER_RESTART_S,
        reboot_min_interval_s: int = REBOOT_MIN_INTERVAL_S,
    ) -> None:
        self.run = run
        self.now = now
        self.state_path = Path(state_dir) / "state.json"
        self.stamp_path = Path(state_dir) / "last-reboot"
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

    def _last_reboot(self) -> float | None:
        try:
            return float(self.stamp_path.read_text().strip())
        except (OSError, ValueError):
            return None

    # Signal --------------------------------------------------------------

    def gateway_up(self) -> bool:
        _rc, out = self.run(["ip", "-4", "route", "show", "default"])
        gateway = parse_gateway(out)
        if gateway is None:
            return False
        rc, _ = self.run(["ping", "-c", "2", "-W", "2", gateway])
        return rc == 0

    # Actions -------------------------------------------------------------

    def restart_wifi(self) -> None:
        # Cycle the radio, then ask NetworkManager to connect the device. The
        # explicit `device connect` matters: after the link is lost NetworkManager
        # gives up autoconnecting once its retries run out, and only a manual
        # activation overrides that.
        self.run(["nmcli", "radio", "wifi", "off"])
        self.sleep(3)
        self.run(["nmcli", "radio", "wifi", "on"])
        self.sleep(5)
        self.run(["nmcli", "device", "connect", INTERFACE])

    def reboot_allowed(self, now: float) -> bool:
        last = self._last_reboot()
        if last is None:
            return True
        # A stamp in the future means the clock is stale (no RTC): treat as recent.
        return now - last >= self.reboot_min_interval_s

    # One pass ------------------------------------------------------------

    def check_once(self) -> str:
        """One check. Returns what it did: ok, down, restart, reboot or skip."""
        state = self._load()
        now = self.now()

        if self.gateway_up():
            if state["down_since"] is not None:
                self.log(f"gateway answers again after {int(now - state['down_since'])}s")
            self._save({"down_since": None, "restarted_at": None, "reboot_skip_logged": False})
            return "ok"

        down_since = state["down_since"]
        if down_since is None or down_since > now:
            # First failure, or the clock moved backwards: start counting again.
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
                state["restarted_at"] = now
                action = "restart"
        elif now - state["restarted_at"] >= self.reboot_after_restart_s:
            if self.reboot_allowed(now):
                self.log(
                    f"still down {int(now - state['restarted_at'])}s after the Wi-Fi restart, rebooting"
                )
                self.stamp_path.parent.mkdir(parents=True, exist_ok=True)
                self.stamp_path.write_text(f"{now}\n")
                self._save({"down_since": None, "restarted_at": None, "reboot_skip_logged": False})
                self.run(["systemctl", "reboot"])
                return "reboot"
            if not state["reboot_skip_logged"]:
                self.log("still down but a reboot already happened within the last hour, not rebooting")
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
