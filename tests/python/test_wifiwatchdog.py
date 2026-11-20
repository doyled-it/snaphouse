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
    """Fake command runner plus boot clock. Commands are recorded in `calls`.

    `t` is seconds since boot (CLOCK_BOOTTIME), so it is also the uptime.
    """

    def __init__(self) -> None:
        self.t = 10_000.0
        self.route = ROUTE_OK
        self.ping_ok = True
        self.nm_state = "100 (connected)"
        self.neigh = ""
        self.nm_out: dict[str, tuple[int, str]] = {}
        self.calls: list[list[str]] = []
        self.logs: list[str] = []

    def clock(self) -> float:
        return self.t

    def run(self, cmd: list[str]) -> tuple[int, str]:
        self.calls.append(cmd)
        if cmd[:2] == ["ip", "-4"] and cmd[2] == "route":
            return 0, self.route
        if cmd[:2] == ["ip", "-4"] and cmd[2] == "neigh":
            return 0, self.neigh
        if cmd[0] == "ping":
            return (0 if self.ping_ok else 1), ""
        if cmd[:3] == ["nmcli", "-g", "GENERAL.STATE"]:
            return 0, self.nm_state
        if cmd[0] == "nmcli":
            return self.nm_out.get(" ".join(cmd[1:]), (0, ""))
        return 0, ""

    def advance(self, seconds: float) -> None:
        self.t += seconds

    def commands(self, name: str) -> list[list[str]]:
        return [c for c in self.calls if c[0] == name]

    def restarts(self) -> int:
        return len([c for c in self.calls if c[:4] == ["nmcli", "radio", "wifi", "off"]])

    def reboots(self) -> int:
        return len([c for c in self.calls if c[:2] == ["systemctl", "reboot"]])


@pytest.fixture
def fake() -> Fake:
    return Fake()


def make(fake: Fake, tmp_path, name: str = "run") -> ww.Watchdog:
    return ww.Watchdog(
        run=fake.run,
        clock=fake.clock,
        state_dir=tmp_path / name,
        stamp_dir=tmp_path / "var",
        wall=lambda: 1.0,
        log=fake.logs.append,
        sleep=lambda s: None,
    )


@pytest.fixture
def dog(fake: Fake, tmp_path):
    return make(fake, tmp_path)


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
    tick(fake, dog, 6)  # restart at the 5 minute check
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


def test_no_reboot_when_uptime_is_under_an_hour(fake, dog):
    # A recent boot (uptime under the interval) means the last reboot was less
    # than an hour ago, so the watchdog restarts Wi-Fi but does not reboot again.
    fake.t = 100.0
    fake.ping_ok = False
    tick(fake, dog, 20)
    assert fake.restarts() == 1
    assert fake.reboots() == 0
    assert len([line for line in fake.logs if "not rebooting" in line]) == 1
    # Once uptime reaches an hour the reboot goes ahead.
    fake.t = 3600.0
    tick(fake, dog, 1)
    assert fake.reboots() == 1


def test_a_boot_after_a_reboot_starts_clean_and_waits_for_an_hour_of_uptime(fake, tmp_path):
    fake.ping_ok = False
    first = make(fake, tmp_path)
    tick(fake, first, 17)
    assert fake.reboots() == 1
    # The reboot: /run is wiped (a fresh state dir) and the boot clock restarts.
    fake.t = 30.0
    second = make(fake, tmp_path, name="run-after-reboot")
    tick(fake, second, 40)
    assert fake.reboots() == 1  # still the first one: uptime under an hour


def test_reboot_leaves_no_running_state_and_records_the_stamp(fake, dog, tmp_path):
    fake.ping_ok = False
    tick(fake, dog, 16)
    assert fake.reboots() == 1
    assert not (tmp_path / "run" / "state.json").exists()
    assert (tmp_path / "var" / "last-reboot").read_text().strip() == "1.0"


def test_running_state_lives_under_the_state_dir_not_the_stamp_dir(fake, dog, tmp_path):
    fake.ping_ok = False
    tick(fake, dog, 2)
    assert (tmp_path / "run" / "state.json").exists()
    assert not (tmp_path / "var" / "state.json").exists()


def test_default_dirs_are_run_for_state_and_var_lib_for_the_stamp():
    assert str(ww.DEFAULT_STATE_DIR).startswith("/run/")
    assert str(ww.DEFAULT_STAMP_DIR).startswith("/var/lib/snaphouse/")


def test_timing_ignores_the_wall_clock(fake, tmp_path):
    jumpy = iter(range(10**6, 0, -997))
    dog = ww.Watchdog(
        run=fake.run,
        clock=fake.clock,
        state_dir=tmp_path / "run",
        stamp_dir=tmp_path / "var",
        wall=lambda: float(next(jumpy)),
        log=fake.logs.append,
        sleep=lambda s: None,
    )
    fake.ping_ok = False
    tick(fake, dog, 9)
    assert fake.restarts() == 1


def test_the_default_clock_is_a_boot_clock():
    assert ww.boot_seconds() >= 0.0
    assert ww.boot_seconds() <= ww.boot_seconds()


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


def test_corrupt_state_file_is_ignored(fake, dog, tmp_path):
    (tmp_path / "run").mkdir()
    (tmp_path / "run" / "state.json").write_text("not json")
    fake.ping_ok = False
    tick(fake, dog, 2)
    assert json.loads((tmp_path / "run" / "state.json").read_text())["down_since"] is not None


def test_state_from_a_later_boot_time_is_discarded(fake, dog, tmp_path):
    (tmp_path / "run").mkdir()
    (tmp_path / "run" / "state.json").write_text(
        json.dumps({"down_since": 10**9, "restarted_at": 10**9})
    )
    fake.ping_ok = False
    tick(fake, dog, 2)
    assert fake.restarts() == 0 and fake.reboots() == 0


# Item 1: a failed ping alone is not "down" when NetworkManager says wlan0 is
# connected and the gateway neighbour entry is reachable or stale with an lladdr.

NEIGH_REACHABLE = f"{GATEWAY} lladdr 00:00:5e:00:53:01 REACHABLE\n"
NEIGH_STALE = f"{GATEWAY} lladdr 00:00:5e:00:53:01 STALE\n"


@pytest.mark.parametrize("neigh", [NEIGH_REACHABLE, NEIGH_STALE])
def test_failed_ping_is_up_when_nm_is_connected_and_neighbour_is_live(fake, dog, neigh):
    fake.ping_ok = False
    fake.neigh = neigh
    tick(fake, dog, 20)
    assert fake.restarts() == 0 and fake.reboots() == 0


def test_fallback_asks_for_the_gateway_neighbour_on_wlan0(fake, dog):
    fake.ping_ok = False
    fake.neigh = NEIGH_REACHABLE
    dog.check_once()
    neigh = [c for c in fake.calls if c[:3] == ["ip", "-4", "neigh"]]
    assert neigh and GATEWAY in neigh[0] and "wlan0" in neigh[0]
    assert ["nmcli", "-g", "GENERAL.STATE", "device", "show", "wlan0"] in fake.calls


@pytest.mark.parametrize(
    ("nm_state", "neigh"),
    [
        ("30 (disconnected)", NEIGH_REACHABLE),
        ("", NEIGH_REACHABLE),
        ("100 (connected)", f"{GATEWAY} lladdr 00:00:5e:00:53:01 FAILED\n"),
        ("100 (connected)", f"{GATEWAY} lladdr 00:00:5e:00:53:01 DELAY\n"),
        ("100 (connected)", f"{GATEWAY} FAILED\n"),
        ("100 (connected)", f"{GATEWAY} REACHABLE\n"),
        ("100 (connected)", f"{GATEWAY} INCOMPLETE\n"),
        ("100 (connected)", ""),
    ],
)
def test_failed_ping_with_a_failed_fallback_is_down(fake, dog, nm_state, neigh):
    fake.ping_ok = False
    fake.nm_state = nm_state
    fake.neigh = neigh
    tick(fake, dog, 6)
    assert fake.restarts() == 1


def test_fallback_is_not_tried_when_ping_answers(fake, dog):
    dog.check_once()
    assert not [c for c in fake.calls if c[:3] == ["ip", "-4", "neigh"]]


# Item 7: each nmcli command in a restart is logged with its return code and a
# short output, and a Wi-Fi rescan runs before the device connect.


def test_restart_logs_each_nmcli_command_with_rc_and_output(fake, dog):
    fake.nm_out["device connect wlan0"] = (4, "Error: no suitable network found\nextra")
    fake.ping_ok = False
    tick(fake, dog, 6)
    lines = [line for line in fake.logs if "nmcli" in line]
    assert len(lines) == 4
    assert any("radio wifi off" in line and "rc=0" in line for line in lines)
    connect = [line for line in lines if "device connect wlan0" in line][0]
    assert "rc=4" in connect and "no suitable network found" in connect
    assert "extra" not in connect


def test_restart_rescans_before_connecting(fake, dog):
    fake.ping_ok = False
    tick(fake, dog, 6)
    nm = [" ".join(c[1:]) for c in fake.calls if c[0] == "nmcli" and c[1] != "-g"]
    assert nm == [
        "radio wifi off",
        "radio wifi on",
        "device wifi rescan ifname wlan0",
        "device connect wlan0",
    ]


def test_gateway_parse_ignores_non_default_lines():
    assert ww.parse_gateway("198.51.100.0/24 dev wlan0\n" + ROUTE_OK) == GATEWAY
    assert ww.parse_gateway("") is None
