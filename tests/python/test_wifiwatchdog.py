"""Issue #178: the Wi-Fi watchdog (lib/snaphouse/wifiwatchdog.py).

A Pi once lost its Wi-Fi link in a brief network blip and never rejoined. The
watchdog runs every minute from a timer. It pings the default gateway (not mDNS,
not the hub, which reboots nightly). After about 5 minutes down it restarts the
Wi-Fi link once; if still down 10 minutes after that it reboots, at most once an
hour. Everything is exercised with a fake command runner and a hand-stepped
clock, so no network or systemd is involved.
"""

from __future__ import annotations

import json

import pytest

from snaphouse import wifiwatchdog as ww

GATEWAY = "192.0.2.1"
ROUTE_OK = f"default via {GATEWAY} dev wlan0 proto dhcp metric 600\n"


class Fake:
    """Fake command runner plus clock. Commands are recorded in `calls`."""

    def __init__(self) -> None:
        self.t = 1_000_000.0
        self.route = ROUTE_OK
        self.ping_ok = True
        self.calls: list[list[str]] = []
        self.logs: list[str] = []

    def now(self) -> float:
        return self.t

    def run(self, cmd: list[str]) -> tuple[int, str]:
        self.calls.append(cmd)
        if cmd[:2] == ["ip", "-4"]:
            return 0, self.route
        if cmd[0] == "ping":
            return (0 if self.ping_ok else 1), ""
        return 0, ""

    def advance(self, seconds: float) -> None:
        self.t += seconds

    def commands(self, name: str) -> list[list[str]]:
        return [c for c in self.calls if c[0] == name]

    def restarts(self) -> int:
        return len([c for c in self.calls if c[:3] == ["nmcli", "radio", "wifi"] and c[3] == "off"])

    def reboots(self) -> int:
        return len([c for c in self.calls if c[:2] == ["systemctl", "reboot"]])


@pytest.fixture
def fake() -> Fake:
    return Fake()


@pytest.fixture
def dog(fake: Fake, tmp_path):
    return ww.Watchdog(
        run=fake.run, now=fake.now, state_dir=tmp_path, log=fake.logs.append, sleep=lambda s: None
    )


def tick(fake: Fake, dog, minutes: int) -> None:
    for _ in range(minutes):
        dog.check_once()
        fake.advance(60)


def test_gateway_up_does_nothing(fake, dog):
    tick(fake, dog, 30)
    assert fake.restarts() == 0 and fake.reboots() == 0


def test_down_under_five_minutes_does_nothing(fake, dog):
    fake.ping_ok = False
    tick(fake, dog, 5)  # checks at 0..4 minutes down
    assert fake.restarts() == 0 and fake.reboots() == 0


def test_five_minutes_down_restarts_wifi_once_not_every_minute(fake, dog):
    fake.ping_ok = False
    tick(fake, dog, 9)
    assert fake.restarts() == 1
    assert fake.reboots() == 0


def test_still_down_ten_minutes_after_restart_reboots(fake, dog):
    fake.ping_ok = False
    tick(fake, dog, 5)
    assert fake.restarts() == 0
    tick(fake, dog, 1)  # 5 min down: restart
    assert fake.restarts() == 1
    tick(fake, dog, 9)
    assert fake.reboots() == 0
    tick(fake, dog, 2)
    assert fake.reboots() == 1
    assert fake.restarts() == 1


def test_gateway_answering_resets_state(fake, dog):
    fake.ping_ok = False
    tick(fake, dog, 6)
    assert fake.restarts() == 1
    fake.ping_ok = True
    tick(fake, dog, 1)
    fake.ping_ok = False
    tick(fake, dog, 5)  # a fresh outage under 5 minutes
    assert fake.restarts() == 1
    tick(fake, dog, 1)
    assert fake.restarts() == 2
    assert fake.reboots() == 0


def test_reboot_not_repeated_within_an_hour(fake, dog, tmp_path):
    fake.ping_ok = False
    tick(fake, dog, 17)
    assert fake.reboots() == 1
    # After the reboot the machine is still down: a new outage inside the hour.
    tick(fake, dog, 20)
    assert fake.reboots() == 1
    # Once an hour has passed since the reboot, the next escalation may reboot.
    fake.advance(3600)
    tick(fake, dog, 20)
    assert fake.reboots() == 2


def test_reboot_stamp_survives_a_new_watchdog_instance(fake, dog, tmp_path):
    fake.ping_ok = False
    tick(fake, dog, 17)
    assert fake.reboots() == 1
    again = ww.Watchdog(
        run=fake.run, now=fake.now, state_dir=tmp_path, log=fake.logs.append, sleep=lambda s: None
    )
    tick(fake, again, 20)
    assert fake.reboots() == 1


def test_no_default_route_counts_as_down(fake, dog):
    fake.route = ""
    tick(fake, dog, 6)
    assert fake.restarts() == 1
    assert fake.commands("ping") == []


def test_ping_targets_the_gateway_from_the_route(fake, dog):
    dog.check_once()
    ping = fake.commands("ping")[0]
    assert GATEWAY in ping


def test_every_step_is_logged_with_the_tag(fake, dog):
    fake.ping_ok = False
    tick(fake, dog, 17)
    text = "\n".join(fake.logs)
    assert "restarting Wi-Fi" in text
    assert "rebooting" in text
    assert all(line.startswith("wifi-watchdog:") for line in fake.logs)


def test_recovery_is_logged(fake, dog):
    fake.ping_ok = False
    tick(fake, dog, 2)
    fake.ping_ok = True
    dog.check_once()
    assert any("answers again" in line for line in fake.logs)


def test_clock_moving_backwards_does_not_trigger_anything(fake, dog):
    fake.ping_ok = False
    tick(fake, dog, 3)
    fake.advance(-100_000)
    tick(fake, dog, 3)
    assert fake.restarts() == 0 and fake.reboots() == 0


def test_corrupt_state_file_is_ignored(fake, dog, tmp_path):
    (tmp_path / "state.json").write_text("not json")
    fake.ping_ok = False
    tick(fake, dog, 2)
    assert json.loads((tmp_path / "state.json").read_text())["down_since"] is not None


def test_gateway_parse_ignores_non_default_lines():
    assert ww.parse_gateway("10.0.0.0/24 dev wlan0\n" + ROUTE_OK) == GATEWAY
    assert ww.parse_gateway("") is None
