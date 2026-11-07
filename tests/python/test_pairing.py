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

import os
import stat

import pytest
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


def test_pairing_window_trusts_nothing_when_the_opening_snapshot_fails():
    # If reading the paired set fails before the window opens, the window never
    # opens and nothing is trusted: a failed snapshot must not fall through to
    # trusting the devices that were already paired.
    sessions: list[list[str]] = []

    def session(commands, timeout=None):
        sessions.append(list(commands))
        if "paired-devices" in commands:
            raise RuntimeError("bluetoothctl unavailable")
        return ""

    clock = FakeClock()
    with pytest.raises(RuntimeError):
        pairing.run_pairing_window(
            CONTROLLER,
            duration=120.0,
            session=session,
            spawn_agent=lambda controller: FakeProc(exited=True),
            monotonic=clock.monotonic,
            sleep=clock.sleep,
        )

    # The window never opened and nothing was trusted or closed: the only session
    # attempted was the snapshot read that raised.
    assert all("discoverable on" not in s for s in sessions)
    assert all("discoverable off" not in s for s in sessions)
    assert all(not any(c.startswith("trust ") for c in s) for s in sessions)


def test_pairing_window_never_sleeps_negative_when_the_deadline_passes_mid_check():
    # Every monotonic() read advances the clock, so the deadline can pass between
    # the loop's time checks. The loop must read the remaining time once and stop
    # when it is spent, never handing sleep() a negative value.
    class TickingClock:
        def __init__(self, step: float):
            self.t = 0.0
            self.step = step

        def monotonic(self) -> float:
            now = self.t
            self.t += self.step
            return now

        def sleep(self, seconds: float) -> None:
            assert seconds >= 0, f"sleep got a negative duration: {seconds}"

    sessions: list[list[str]] = []

    def session(commands, timeout=None):
        sessions.append(list(commands))
        return ""

    clock = TickingClock(step=3.0)
    pairing.run_pairing_window(
        CONTROLLER,
        duration=10.0,
        session=session,
        spawn_agent=lambda controller: FakeProc(exited=True),
        monotonic=clock.monotonic,
        sleep=clock.sleep,
        poll_interval=1.0,
    )

    # It ran to completion and closed the adapter, with no negative sleep.
    assert any("discoverable off" in s for s in sessions)


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


def test_pairing_window_raises_but_still_closes_on_the_right_adapter_when_select_fails():
    # If bluetoothctl cannot select the receive adapter, the window must abort
    # rather than open a pairing window on the wrong (default) controller. But the
    # adapter still has to be closed on the intended controller, selected by
    # address, so Ctrl-C safety and the standing lockdown are never undermined.
    sessions: list[list[str]] = []

    def session(commands, timeout=None):
        sessions.append(list(commands))
        if "paired-devices" in commands:
            return ""  # the opening snapshot reads cleanly
        if "discoverable on" in commands:
            # The open session cannot select the adapter.
            return f"Controller {CONTROLLER} not available\n"
        return ""

    clock = FakeClock()
    with pytest.raises(pairing.AdapterSelectFailed):
        pairing.run_pairing_window(
            CONTROLLER,
            duration=120.0,
            session=session,
            spawn_agent=lambda controller: FakeProc(exited=True),
            monotonic=clock.monotonic,
            sleep=clock.sleep,
        )

    # The finally ran and closed the adapter, selecting it by address.
    closed = [s for s in sessions if "discoverable off" in s]
    assert closed
    for commands in closed:
        assert commands[0] == f"select {CONTROLLER}"
        assert "pairable off" in commands


SEND_ADAPTER = "11:22:33:44:55:66"

LOCAL_FOR_SPEAKER = """\
# Local settings. Comments here must survive a rewrite.
bt_send_adapter: "11:22:33:44:55:66"
speakers:
  vizio: ""   # living-room soundbar
  jbl: ""     # bedroom speaker
"""


def test_pair_speaker_selects_by_address_records_and_prompts_a_reapply(
    tmp_path, capsys, monkeypatch
):
    settings = tmp_path / "local.yml"
    settings.write_text(LOCAL_FOR_SPEAKER)
    # resolve_adapter is the presence check; stub it so the test needs no sysfs.
    monkeypatch.setattr(pairing, "resolve_adapter", lambda mac, *a, **k: "hci9")

    speaker_mac = "AA:BB:CC:DD:EE:01"
    seen: list[list[str]] = []

    def session(commands, timeout=None):
        seen.append(list(commands))
        if "devices" in commands:
            return f"Device {speaker_mac} Vizio TV\n"
        return ""

    def choose(devices):
        assert devices == [(speaker_mac, "Vizio TV")]
        return devices[0]

    rc = pairing.pair_speaker(
        "vizio", settings=settings, session=session, choose=choose
    )
    assert rc == 0

    # The address was recorded under speakers.vizio.
    data = yaml.safe_load(settings.read_text())
    assert data["speakers"]["vizio"] == speaker_mac

    # Every session selected the send adapter by address, never by hciN (issue
    # #30): a scan, a device listing, and the pair/trust/connect.
    assert seen
    for commands in seen:
        assert commands[0] == f"select {SEND_ADAPTER}"
        assert not any(c.startswith("select hci") for c in commands)

    out = capsys.readouterr().out
    # The ALSA output alias only picks up the new address on the next apply, so
    # the operator is told to re-apply.
    assert "snaphouse update --force" in out


def test_pair_speaker_aborts_clearly_when_the_adapter_cannot_be_selected(
    tmp_path, capsys, monkeypatch
):
    settings = tmp_path / "local.yml"
    settings.write_text(LOCAL_FOR_SPEAKER)
    monkeypatch.setattr(pairing, "resolve_adapter", lambda mac, *a, **k: "hci9")

    def session(commands, timeout=None):
        # bluetoothctl cannot select the adapter in any session.
        return "Controller not available\n"

    rc = pairing.pair_speaker(
        "vizio", settings=settings, session=session, choose=lambda devices: devices[0]
    )
    assert rc != 0
    err = capsys.readouterr().err
    assert "could not select" in err.lower()
    # Nothing was recorded: speakers.vizio is still empty.
    data = yaml.safe_load(settings.read_text())
    assert data["speakers"]["vizio"] == ""


LOCAL_YML = """\
# Local settings for this machine. Comments here must survive a rewrite.

# Bluetooth adapters, by their own address.
bt_send_adapter: "11:22:33:44:55:66"

# Paired speaker addresses, written by `snaphouse pair speaker <name>`.
speakers:
  vizio: ""   # living-room soundbar, paired from the hub
  jbl: ""     # bedroom speaker, paired from nightstand

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


def test_write_speaker_preserves_the_file_mode(tmp_path):
    path = tmp_path / "local.yml"
    path.write_text(LOCAL_YML)
    os.chmod(path, 0o600)

    pairing.write_speaker(path, "vizio", "AA:BB:CC:DD:EE:FF")

    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600


def test_write_speaker_leaves_the_original_intact_when_dump_fails(tmp_path, monkeypatch):
    # local.yml is the household's only copy of its addresses. A crash while
    # writing it must not truncate or empty it: the write is staged in a temp
    # file and swapped in atomically, so a failure leaves the original in place.
    path = tmp_path / "local.yml"
    path.write_text(LOCAL_YML)
    original = path.read_text()

    from ruamel.yaml import YAML

    def boom(self, data, stream):
        stream.write("partial")  # a half-written document, as a real crash would leave
        raise RuntimeError("disk full during dump")

    monkeypatch.setattr(YAML, "dump", boom)

    with pytest.raises(RuntimeError):
        pairing.write_speaker(path, "vizio", "AA:BB:CC:DD:EE:FF")

    # The original survived untouched and no temp file was left behind.
    assert path.read_text() == original
    assert list(tmp_path.iterdir()) == [path]
