"""Tests for keepalive, the Bluetooth speaker reconnect check.

pi-bedroom's JBL is off most of the time. A systemd timer runs keepalive every
30 seconds; it reconnects the speaker whenever it powers on and, crucially, logs
only when the connection state changes. Days with the speaker off must leave one
log line, not a loop of errors (design: "Bluetooth speakers that come and go",
Review Focus 1), and the speaker coming back must be recovered by the next tick
or two (30-60 s) and logged once.

The connection probe and the connect command are injected, and the last-seen
state is persisted to a file (the timer runs a fresh process each tick, so the
"only on transition" rule has to survive across processes). These tests drive
fakes and a temporary state file, so no Bluetooth hardware is involved.
"""

from __future__ import annotations

from pathlib import Path

from snaphouse import clients


class Speaker:
    """A fake speaker whose reachability a test controls.

    `connected` is what the probe reports now. `reachable` is whether a connect
    attempt would succeed (the speaker is powered on and in range). Each connect
    attempt is counted so a test can confirm keepalive tries while the speaker is
    off without logging each try.
    """

    def __init__(self, connected: bool = False, reachable: bool = False):
        self.connected = connected
        self.reachable = reachable
        self.connect_attempts = 0

    def is_connected(self, mac: str) -> bool:
        return self.connected

    def connect(self, mac: str) -> bool:
        self.connect_attempts += 1
        if self.reachable:
            self.connected = True
        return self.reachable


MAC = "AA:BB:CC:DD:EE:FF"


def run(speaker: Speaker, state_path: Path, log: list[str]) -> str:
    return clients.keepalive(
        MAC,
        is_connected=speaker.is_connected,
        connect=speaker.connect,
        state_path=state_path,
        log=log.append,
    )


def test_logs_once_across_many_absent_cycles(tmp_path):
    # The speaker is off: never connected, connect never succeeds.
    speaker = Speaker(connected=False, reachable=False)
    state_path = tmp_path / "keepalive.state"
    log: list[str] = []

    states = [run(speaker, state_path, log) for _ in range(100)]

    # Every cycle reports absent, and a connect was attempted each time (so the
    # speaker is picked up the moment it returns), but only the first transition
    # into "absent" is logged.
    assert states == ["absent"] * 100
    assert speaker.connect_attempts == 100
    assert len(log) == 1


def test_logs_once_on_the_transition_to_connected(tmp_path):
    state_path = tmp_path / "keepalive.state"
    log: list[str] = []

    # A stretch of absence: one log line.
    speaker = Speaker(connected=False, reachable=False)
    for _ in range(5):
        run(speaker, state_path, log)
    assert len(log) == 1

    # The speaker powers on. The next tick connects it ("connecting"), the tick
    # after sees it connected. Each distinct state transition logs exactly once.
    speaker.reachable = True
    assert run(speaker, state_path, log) == "connecting"
    assert run(speaker, state_path, log) == "connected"
    assert len(log) == 3

    # Staying connected logs nothing more.
    assert run(speaker, state_path, log) == "connected"
    assert len(log) == 3


def test_does_not_connect_when_already_connected(tmp_path):
    speaker = Speaker(connected=True, reachable=True)
    state_path = tmp_path / "keepalive.state"
    log: list[str] = []

    assert run(speaker, state_path, log) == "connected"
    # Already connected, so no connect command is issued.
    assert speaker.connect_attempts == 0


def test_does_not_leak_the_full_address_in_the_log(tmp_path):
    speaker = Speaker(connected=False, reachable=False)
    state_path = tmp_path / "keepalive.state"
    log: list[str] = []

    run(speaker, state_path, log)

    assert len(log) == 1
    # The household's address must not reach the journal; only the last two
    # octets may appear, the way snaphouse.adapters redacts an address.
    assert MAC not in log[0]
    assert "EE:FF" in log[0]
