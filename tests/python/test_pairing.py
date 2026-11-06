"""Tests for snaphouse.pairing, the `snaphouse pair` logic.

Two behaviors are pinned here, both chosen for safety and both hard to get right
by hand on the device:

  - The inbound pairing window stays open for its full duration and then always
    closes the adapter, even if the pairing agent process dies early. An agent
    that exits must not cut the window short, and it must never leave the hub
    discoverable. A fake clock and a fake agent process let the whole window run
    in no real time.
  - Writing a paired speaker's address back into local.yml preserves the file's
    comments and every other key, because that file is edited by hand and the
    comments are what make it usable.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from snaphouse import pairing


class FakeProc:
    """A stand-in for the bt-agent subprocess.

    `exited` controls whether it looks already-finished: an exited agent reports
    a return code from poll(), a running one reports None.
    """

    def __init__(self, *, exited: bool):
        self._exited = exited
        self.terminated = False
        self.killed = False
        self.waited = False

    def poll(self):
        return 0 if self._exited else None

    def terminate(self):
        self.terminated = True
        self._exited = True

    def kill(self):
        self.killed = True
        self._exited = True

    def wait(self, timeout=None):
        self.waited = True
        return 0


class FakeClock:
    """A monotonic clock whose only advance is the sleeps the code asks for."""

    def __init__(self, start: float = 1000.0):
        self.t = start

    def monotonic(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        assert seconds >= 0
        self.t += seconds


CONTROLLER = "AA:BB:CC:DD:EE:FF"


def test_pairing_window_runs_full_duration_and_closes_even_if_agent_exits_early():
    sessions: list[list[str]] = []
    spawned: list[str] = []
    proc = FakeProc(exited=True)  # the agent is dead from the very first tick
    clock = FakeClock()

    def session(commands, timeout=None):
        sessions.append(list(commands))
        return ""

    def spawn_agent(controller):
        spawned.append(controller)
        return proc

    start = clock.t
    pairing.run_pairing_window(
        CONTROLLER,
        duration=120.0,
        session=session,
        spawn_agent=spawn_agent,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )

    # The window stayed open the whole 120 s despite the agent being dead.
    assert clock.t - start >= 120.0
    # The agent was started on the chosen controller.
    assert spawned == [CONTROLLER]
    # One session selected the right controller, then made it discoverable and
    # pairable. Selecting in the same session is what targets the receive adapter
    # and not the hub's other dongle.
    opened = next(i for i, s in enumerate(sessions) if "discoverable on" in s)
    assert f"select {CONTROLLER}" in sessions[opened]
    assert "pairable on" in sessions[opened]
    # A later session closed the adapter again.
    closed = next(i for i, s in enumerate(sessions) if "discoverable off" in s)
    assert closed > opened
    assert f"select {CONTROLLER}" in sessions[closed]
    assert "pairable off" in sessions[closed]


def test_pairing_window_trusts_only_devices_paired_during_the_window():
    # A device is already paired before the window (an existing speaker). A new
    # phone pairs during it. Only the new device should be trusted: opening the
    # window must not hand trust to everything already paired.
    old = "00:00:00:00:00:01"
    new = "00:00:00:00:00:02"
    paired_outputs = iter(
        [
            f"Device {old} OldSpeaker\n",  # snapshot before the window
            f"Device {old} OldSpeaker\nDevice {new} NewPhone\n",  # after it
        ]
    )
    sessions: list[list[str]] = []

    def session(commands, timeout=None):
        sessions.append(list(commands))
        if "paired-devices" in commands:
            return next(paired_outputs)
        return ""

    clock = FakeClock()
    pairing.run_pairing_window(
        CONTROLLER,
        duration=120.0,
        session=session,
        spawn_agent=lambda controller: FakeProc(exited=True),
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )

    trust_sessions = [s for s in sessions if any(c.startswith("trust ") for c in s)]
    assert len(trust_sessions) == 1
    trusted = {c for c in trust_sessions[0] if c.startswith("trust ")}
    assert trusted == {f"trust {new}"}


def test_pairing_window_closes_the_adapter_even_when_the_wait_is_interrupted():
    sessions: list[list[str]] = []
    proc = FakeProc(exited=False)

    def session(commands, timeout=None):
        sessions.append(list(commands))
        return ""

    def interrupt(_seconds):
        raise KeyboardInterrupt

    clock = FakeClock()
    try:
        pairing.run_pairing_window(
            CONTROLLER,
            duration=120.0,
            session=session,
            spawn_agent=lambda controller: proc,
            monotonic=clock.monotonic,
            sleep=interrupt,
        )
    except KeyboardInterrupt:
        pass

    # Interrupted mid-window, the adapter is still closed and the live agent is
    # stopped: Ctrl-C must never leave the hub discoverable.
    assert any("discoverable off" in s for s in sessions)
    assert any("pairable off" in s for s in sessions)
    assert proc.terminated


LOCAL_YML = """\
# Local settings for this machine. Comments here must survive a rewrite.

# Bluetooth adapters, by their own address.
bt_send_adapter: "11:22:33:44:55:66"

# Paired speaker addresses, written by `snaphouse pair speaker <name>`.
speakers:
  vizio: ""   # living-room soundbar, paired from the hub
  jbl: ""     # bedroom speaker, paired from pi-bedroom

art_share_password: "change-me"
"""


def test_write_speaker_records_the_address_and_keeps_comments_and_other_keys(tmp_path):
    path = tmp_path / "local.yml"
    path.write_text(LOCAL_YML)

    pairing.write_speaker(path, "vizio", "AA:BB:CC:DD:EE:FF")

    text = path.read_text()
    data = yaml.safe_load(text)

    # The paired address landed under speakers.vizio; nothing else moved.
    assert data["speakers"]["vizio"] == "AA:BB:CC:DD:EE:FF"
    assert data["speakers"]["jbl"] == ""
    assert data["bt_send_adapter"] == "11:22:33:44:55:66"
    assert data["art_share_password"] == "change-me"

    # The hand-written comments and the inline note survived the rewrite.
    assert "Comments here must survive a rewrite" in text
    assert "written by `snaphouse pair speaker" in text
    assert "living-room soundbar, paired from the hub" in text
