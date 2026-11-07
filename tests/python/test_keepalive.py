"""Tests for keepalive, the Bluetooth speaker reconnect and snapclient lifecycle.

On a Bluetooth-output host (nightstand's JBL, the hub's Vizio) snapclient is not
enabled at boot. Instead a systemd timer runs keepalive every 30 seconds, and
keepalive owns snapclient's lifecycle: it starts snapclient when the speaker
connects and stops it when the speaker drops, so a speaker that is off for days
never leaves snapclient in a restart loop filling the journal (design:
"Bluetooth speakers that come and go", Review Focus 1). keepalive still logs only
when the connection state changes.

Pinned here:

  - 100 ticks with the speaker off report "absent" every time, attempt a connect
    every time (so the speaker is picked up the moment it returns), and log once.
  - The speaker powering on starts snapclient exactly once and logs once; the
    speaker dropping stops snapclient exactly once and logs once.
  - A connect command is issued only when the speaker is not already connected.
  - snapclient is reconciled against the link, not churned: when snapclient is
    already in the right state no start or stop command is sent.
  - The household address never reaches the log; only its last two octets do.

The Bluetooth probe/connect and the snapclient systemctl calls are injected, and
the last-seen state is persisted to a temporary file, so no hardware is touched.
"""

from __future__ import annotations

from pathlib import Path

from snaphouse import clients


class Speaker:
    """A fake speaker whose reachability a test controls.

    `connected` is what the probe reports now; `reachable` is whether a connect
    attempt would succeed (powered on and in range). Connect attempts are counted
    so a test can confirm keepalive keeps trying while the speaker is off.
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


class FakeClient:
    """A fake snapclient service, standing in for systemctl start/stop/is-active."""

    def __init__(self, active: bool = False):
        self.active = active
        self.starts = 0
        self.stops = 0

    def is_active(self) -> bool:
        return self.active

    def start(self) -> None:
        self.starts += 1
        self.active = True

    def stop(self) -> None:
        self.stops += 1
        self.active = False


MAC = "AA:BB:CC:DD:EE:FF"


def run(speaker: Speaker, client: FakeClient, state_path: Path, log: list[str]) -> str:
    return clients.keepalive(
        MAC,
        is_connected=speaker.is_connected,
        connect=speaker.connect,
        is_client_active=client.is_active,
        start_client=client.start,
        stop_client=client.stop,
        state_path=state_path,
        log=log.append,
    )


def test_logs_once_across_many_absent_cycles(tmp_path):
    # Speaker off: never connected, connect never succeeds, snapclient inactive.
    speaker = Speaker(connected=False, reachable=False)
    client = FakeClient(active=False)
    state_path = tmp_path / "keepalive.state"
    log: list[str] = []

    states = [run(speaker, client, state_path, log) for _ in range(100)]

    assert states == ["absent"] * 100
    # A connect is tried every tick, so the speaker is picked up as soon as it
    # returns, but only the first transition into "absent" is logged.
    assert speaker.connect_attempts == 100
    assert len(log) == 1
    # snapclient was already stopped, so it is left alone: no churn.
    assert client.starts == 0
    assert client.stops == 0


def test_starts_snapclient_once_when_the_speaker_connects(tmp_path):
    speaker = Speaker(connected=False, reachable=False)
    client = FakeClient(active=False)
    state_path = tmp_path / "keepalive.state"
    log: list[str] = []

    # A stretch of absence: snapclient stays stopped, one log line.
    for _ in range(3):
        assert run(speaker, client, state_path, log) == "absent"
    assert client.starts == 0
    assert len(log) == 1

    # The speaker powers on. The next tick connects it and starts snapclient, in
    # one tick (recovery within the 30 s timer plus the start).
    speaker.reachable = True
    assert run(speaker, client, state_path, log) == "connected"
    assert client.starts == 1
    assert client.active is True
    assert len(log) == 2

    # Staying connected neither restarts snapclient nor logs again.
    assert run(speaker, client, state_path, log) == "connected"
    assert client.starts == 1
    assert client.stops == 0
    assert len(log) == 2


def test_stops_snapclient_once_when_the_speaker_drops(tmp_path):
    speaker = Speaker(connected=True, reachable=True)
    client = FakeClient(active=True)
    state_path = tmp_path / "keepalive.state"
    log: list[str] = []

    # Connected and playing: no lifecycle command, and a connect is not issued
    # because the speaker is already connected.
    assert run(speaker, client, state_path, log) == "connected"
    assert speaker.connect_attempts == 0
    assert client.starts == 0
    assert client.stops == 0

    # The speaker switches off. The next tick stops snapclient once.
    speaker.connected = False
    speaker.reachable = False
    assert run(speaker, client, state_path, log) == "absent"
    assert client.stops == 1
    assert client.active is False
    assert len(log) == 2


def test_one_log_line_per_transition(tmp_path):
    speaker = Speaker(connected=False, reachable=False)
    client = FakeClient(active=False)
    state_path = tmp_path / "keepalive.state"
    log: list[str] = []

    run(speaker, client, state_path, log)            # -> absent  (log 1)
    speaker.reachable = True
    run(speaker, client, state_path, log)            # -> connected (log 2)
    speaker.connected = False
    speaker.reachable = False
    run(speaker, client, state_path, log)            # -> absent  (log 3)

    assert len(log) == 3
    assert client.starts == 1
    assert client.stops == 1


def test_does_not_churn_snapclient_when_already_active_and_connected(tmp_path):
    speaker = Speaker(connected=True, reachable=True)
    client = FakeClient(active=True)
    state_path = tmp_path / "keepalive.state"
    log: list[str] = []

    for _ in range(10):
        assert run(speaker, client, state_path, log) == "connected"

    assert client.starts == 0
    assert client.stops == 0
    assert len(log) == 1  # the first observation only


SEND_ADAPTER = "11:22:33:44:55:66"


def test_keepalive_targets_the_send_adapter_not_the_bluetoothctl_default(tmp_path):
    # Two controllers are present. hci0 sorts first, so it is the index-ordered
    # controller a bare bluetoothctl `connect` would fall back to (on the hub, the
    # receive dongle, the wrong one). hci1 is the send adapter named in local.yml
    # by address, and the speaker answers only through it.
    sysroot = tmp_path / "sys"
    (sysroot / "hci0").mkdir(parents=True)
    (sysroot / "hci0" / "address").write_text("99:99:99:99:99:99\n")
    (sysroot / "hci1").mkdir(parents=True)
    (sysroot / "hci1" / "address").write_text(SEND_ADAPTER + "\n")

    settings = tmp_path / "local.yml"
    settings.write_text(
        f'bt_send_adapter: "{SEND_ADAPTER}"\nspeakers:\n  jbl: "{MAC}"\n'
    )

    # Resolved by MAC to hci1, never the first/default hci0.
    controller = clients._send_controller(settings, sys_root=sysroot)
    assert controller == "hci1"

    seen: list[list[str]] = []

    def session(commands: list[str]) -> str:
        seen.append(list(commands))
        # The speaker is reachable only when the session targets the send adapter.
        if commands[0] != f"select {controller}":
            return ""
        if any(c.startswith("info ") for c in commands):
            return "Connected: no\n"
        if any(c.startswith("connect ") for c in commands):
            return "Connection successful\n"
        return ""

    # The default controller cannot reach the speaker: proof that selecting the
    # send adapter is what makes the connect work.
    _wrong_connected, wrong_connect = clients._controller_probe("hci0", session=session)
    assert wrong_connect(MAC) is False
    seen.clear()

    is_connected, connect = clients._controller_probe(controller, session=session)
    client = FakeClient(active=False)
    state = clients.keepalive(
        MAC,
        is_connected=is_connected,
        connect=connect,
        is_client_active=client.is_active,
        start_client=client.start,
        stop_client=client.stop,
        state_path=tmp_path / "state",
        log=[].append,
    )

    # The link came up and snapclient was started, through the send adapter.
    assert state == "connected"
    assert client.starts == 1
    assert seen  # bluetoothctl was actually invoked
    # Every session selected the send adapter before acting, and the connect to
    # the speaker went through it.
    for commands in seen:
        assert commands[0] == f"select {controller}"
    assert [f"select {controller}", f"connect {MAC}"] in seen


def test_does_not_leak_the_full_address_in_the_log(tmp_path):
    speaker = Speaker(connected=False, reachable=False)
    client = FakeClient(active=False)
    state_path = tmp_path / "keepalive.state"
    log: list[str] = []

    run(speaker, client, state_path, log)

    assert len(log) == 1
    # The household's address must not reach the journal; only the last two
    # octets may appear, the way snaphouse.adapters redacts an address.
    assert MAC not in log[0]
    assert "EE:FF" in log[0]
