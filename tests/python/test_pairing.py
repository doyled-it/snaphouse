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
    """A stand-in for the single long-lived bluetoothctl process.

    The root cause of issue #128 was running the whole window across more than
    one bluetoothctl session: `pairable on` in a throwaway session, then a
    separate agent process. With BlueZ AlwaysPairable=false the adapter dropped
    bondable the moment that first session's agent went away. The fix is ONE
    process that registers the agent and turns pairable/discoverable on, and
    stays alive for the window. This fake records every command written to that
    one process in `sent`, so a test can pin the ordering.

    `exited` controls whether it looks already-finished: an exited process reports
    a return code from poll(), a running one reports None.
    """

    def __init__(self, *, exited: bool = False):
        self._exited = exited
        self.sent: list[str] = []
        self.reader = None
        self.terminated = False
        self.killed = False
        self.waited = False

    def send(self, line: str) -> None:
        self.sent.append(line)

    def start_reader(self, on_line) -> None:
        self.reader = on_line

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


def _run(session, proc, clock, **kwargs):
    """run_pairing_window with the fakes wired in, declining any passkey."""
    pairing.run_pairing_window(
        CONTROLLER,
        duration=kwargs.pop("duration", 120.0),
        spawn=lambda: proc,
        confirm=kwargs.pop("confirm", lambda passkey, device=None: False),
        session=session,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
        **kwargs,
    )


def test_open_commands_all_go_to_one_session_with_pairable_after_default_agent():
    # The issue #128 regression pin. The old code set `pairable on` in a throwaway
    # bluetoothctl session and then spawned a separate agent; with
    # AlwaysPairable=false the adapter lost bondable when that first session's
    # agent went away, so every phone got Pairing Not Allowed (0x18). The fix runs
    # the whole open in ONE long-lived process: register the DisplayYesNo agent,
    # make it the default, then (in that same session) select the adapter and turn
    # pairable and discoverable on. This test pins that the open commands all go to
    # the one process and that pairable/discoverable come after default-agent.
    proc = FakeProc()
    clock = FakeClock()
    sessions: list[list[str]] = []

    def session(commands, timeout=None):
        sessions.append(list(commands))
        return ""

    _run(session, proc, clock, duration=120.0)

    assert proc.sent == [
        f"agent {pairing.AGENT_CAPABILITY}",
        "default-agent",
        f"select {CONTROLLER}",
        "pairable-timeout 120",
        "discoverable-timeout 120",
        "pairable on",
        "discoverable on",
    ]
    # The root-cause assertions: pairable and discoverable are turned on in the
    # agent's own session, after the agent is the default one.
    assert proc.sent.index("default-agent") < proc.sent.index("pairable on")
    assert proc.sent.index("default-agent") < proc.sent.index("discoverable on")
    # And nothing turned pairable on in a separate session: the only session calls
    # are the opening snapshot, the select preflight, the trust snapshot, and the
    # closing lockdown, none of which open pairing.
    assert all("pairable on" not in s for s in sessions)
    # The window still closed the adapter at the end.
    assert any("discoverable off" in s and "pairable off" in s for s in sessions)


def test_pairing_window_runs_full_duration_and_closes_even_if_the_process_exits_early():
    sessions: list[list[str]] = []
    proc = FakeProc(exited=True)  # the process is dead from the very first tick
    clock = FakeClock()

    def session(commands, timeout=None):
        sessions.append(list(commands))
        return ""

    start = clock.t
    _run(session, proc, clock, duration=120.0)

    # The window stayed open the whole 120 s despite the process being dead: the
    # clock drives it, so a process that exits early does not shorten it.
    assert clock.t - start >= 120.0
    # The open ran on the chosen controller inside the one process.
    assert f"select {CONTROLLER}" in proc.sent
    assert "pairable on" in proc.sent
    # A later session closed the adapter again, selecting it by address.
    closed = next(i for i, s in enumerate(sessions) if "discoverable off" in s)
    assert sessions[closed][0] == f"select {CONTROLLER}"
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
    _run(session, FakeProc(exited=True), clock, duration=120.0)

    trust_sessions = [s for s in sessions if any(c.startswith("trust ") for c in s)]
    assert len(trust_sessions) == 1
    trusted = {c for c in trust_sessions[0] if c.startswith("trust ")}
    assert trusted == {f"trust {new}"}


def test_pairing_window_trusts_nothing_when_the_opening_snapshot_fails():
    # If reading the paired set fails before the window opens, the window never
    # opens and nothing is trusted: a failed snapshot must not fall through to
    # trusting the devices that were already paired, nor open the adapter.
    sessions: list[list[str]] = []
    proc = FakeProc()

    def session(commands, timeout=None):
        sessions.append(list(commands))
        if "paired-devices" in commands:
            raise RuntimeError("bluetoothctl unavailable")
        return ""

    clock = FakeClock()
    with pytest.raises(RuntimeError):
        _run(session, proc, clock, duration=120.0)

    # The window never opened: no process was driven and nothing closed or trusted.
    assert proc.sent == []
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
        spawn=lambda: FakeProc(exited=True),
        confirm=lambda passkey, device=None: False,
        session=session,
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
            spawn=lambda: proc,
            confirm=lambda passkey, device=None: False,
            session=session,
            monotonic=clock.monotonic,
            sleep=interrupt,
        )
    except KeyboardInterrupt:
        pass

    # Interrupted mid-window, the adapter is still closed and the live process is
    # stopped: Ctrl-C must never leave the hub discoverable.
    assert any("discoverable off" in s for s in sessions)
    assert any("pairable off" in s for s in sessions)
    assert proc.terminated


def test_pairing_window_stops_early_when_the_stop_event_is_set():
    # The web service closes the window early (on a stop, or once a phone paired)
    # by setting a stop event. The loop observes it and ends, and the finally still
    # closes the adapter and stops the process.
    import threading

    sessions: list[list[str]] = []
    proc = FakeProc(exited=False)
    stop = threading.Event()
    stop.set()

    def session(commands, timeout=None):
        sessions.append(list(commands))
        return ""

    clock = FakeClock()
    start = clock.t
    pairing.run_pairing_window(
        CONTROLLER,
        duration=120.0,
        spawn=lambda: proc,
        confirm=lambda passkey, device=None: False,
        session=session,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
        stop_event=stop,
    )

    # It did not wait out the duration, and it closed the adapter.
    assert clock.t - start < 120.0
    assert any("discoverable off" in s for s in sessions)
    assert proc.terminated


def test_pairing_window_raises_but_still_closes_on_the_right_adapter_when_select_fails():
    # If bluetoothctl cannot select the receive adapter, the window must abort
    # rather than open a pairing window on the wrong (default) controller. But the
    # adapter still has to be closed on the intended controller, selected by
    # address, so Ctrl-C safety and the standing lockdown are never undermined.
    sessions: list[list[str]] = []
    proc = FakeProc()

    def session(commands, timeout=None):
        sessions.append(list(commands))
        if "paired-devices" in commands:
            return ""  # the opening snapshot reads cleanly
        if "show" in commands:
            # The select preflight cannot select the adapter.
            return f"Controller {CONTROLLER} not available\n"
        return ""

    clock = FakeClock()
    with pytest.raises(pairing.AdapterSelectFailed):
        _run(session, proc, clock, duration=120.0)

    # The preflight failed, so the window never opened the process.
    assert proc.sent == []
    # The finally still ran and closed the adapter, selecting it by address.
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


ESC = "\x1b"

# Real `bluetoothctl devices` output after a scan: the "Device" keyword is
# wrapped in ANSI colour, unnamed devices repeat the address as the name (in
# dash or colon form), and genuinely named devices are what the operator must
# pick from. Addresses and names here are placeholders, never a household's.
DEVICES_WITH_ANSI = (
    f"{ESC}[0;92mDevice{ESC}[0m AA:BB:CC:DD:EE:01 JBL Flip 6\n"
    f"{ESC}[0;92mDevice{ESC}[0m AA:BB:CC:DD:EE:02 AA-BB-CC-DD-EE-02\n"
    "Device AA:BB:CC:DD:EE:03 AA:BB:CC:DD:EE:03\n"
    "Device AA:BB:CC:DD:EE:04 Vizio V-Series\n"
)


def test_named_devices_reads_names_through_ansi_and_drops_address_named_entries():
    # Issue #40: the parser printed "no named devices" while bluetoothctl listed
    # them, because ANSI colour on the "Device" keyword broke a "first token is
    # Device" check and address-as-name entries slipped through. It must strip the
    # colour, keep the two named devices, and drop both forms of address-as-name.
    found = pairing._named_devices(DEVICES_WITH_ANSI)
    assert found == [
        ("AA:BB:CC:DD:EE:01", "JBL Flip 6"),
        ("AA:BB:CC:DD:EE:04", "Vizio V-Series"),
    ]


def test_named_devices_deduplicates_repeated_entries():
    # A scan can list the same device more than once (a [NEW] then a [CHG]); the
    # operator should see it once.
    output = (
        "[NEW] Device AA:BB:CC:DD:EE:01 JBL Flip 6\n"
        "[CHG] Device AA:BB:CC:DD:EE:01 JBL Flip 6\n"
    )
    assert pairing._named_devices(output) == [("AA:BB:CC:DD:EE:01", "JBL Flip 6")]


def test_paired_devices_reads_addresses_through_ansi_and_dash_names():
    # The trust path diffs paired-devices snapshots by address, so this parse must
    # survive the same ANSI colour and dash-form names that #40 fixed in
    # _named_devices. If it does not, both snapshots come back empty, _trust_new
    # sees no new device, and the phone just paired in the window is never trusted
    # (review Important #1). Unnamed devices keep the address as their name.
    output = (
        f"{ESC}[0;92mDevice{ESC}[0m AA:BB:CC:DD:EE:01 JBL Flip 6\n"
        f"{ESC}[0;92mDevice{ESC}[0m AA:BB:CC:DD:EE:02 AA-BB-CC-DD-EE-02\n"
    )
    assert pairing._paired_devices(output) == {
        "AA:BB:CC:DD:EE:01": "JBL Flip 6",
        "AA:BB:CC:DD:EE:02": "AA-BB-CC-DD-EE-02",
    }


def test_service_allowed_covers_a2dp_and_avrcp_only():
    # The inbound agent authorizes only the audio services the phone needs: A2DP
    # (source/sink) and AVRCP. Anything else is declined.
    assert pairing._service_allowed("0000110b-0000-1000-8000-00805f9b34fb")  # A2DP Sink
    assert pairing._service_allowed("0000110A-0000-1000-8000-00805F9B34FB")  # A2DP Source
    assert pairing._service_allowed("0000110e-0000-1000-8000-00805f9b34fb")  # AVRCP
    assert not pairing._service_allowed("00001124-0000-1000-8000-00805f9b34fb")  # HID
    assert not pairing._service_allowed("0000111e-0000-1000-8000-00805f9b34fb")  # Handsfree


def test_parse_confirmation_prompt_reads_the_passkey_through_ansi():
    line = f"{ESC}[0;93m[agent]{ESC}[0m Confirm passkey 024680 (yes/no): "
    assert pairing._parse_confirmation_prompt(line) == 24680
    assert pairing._parse_confirmation_prompt("[CHG] Device AA:BB:CC:DD:EE:01 Paired: yes") is None


def test_parse_service_prompt_reads_the_uuid_through_ansi():
    line = f"{ESC}[0;93m[agent]{ESC}[0m Authorize service 0000110b-0000-1000-8000-00805f9b34fb (yes/no): "
    assert pairing._parse_service_prompt(line) == "0000110b-0000-1000-8000-00805f9b34fb"
    assert pairing._parse_service_prompt("just some other output") is None


def test_agent_confirms_passkey_then_authorizes_audio_and_declines_the_rest():
    written: list[str] = []
    shown: list[int] = []

    def confirm(passkey: int) -> bool:
        shown.append(passkey)
        return True

    # A numeric-comparison confirmation is shown to the operator, who accepts it.
    pairing._handle_agent_line(
        f"{ESC}[0;93m[agent]{ESC}[0m Confirm passkey 123456 (yes/no): ",
        write=written.append,
        confirm=confirm,
    )
    assert shown == [123456]
    assert written == ["yes\n"]

    # An A2DP service is authorized; an unrelated service is declined.
    pairing._handle_agent_line(
        "[agent] Authorize service 0000110b-0000-1000-8000-00805f9b34fb (yes/no): ",
        write=written.append,
        confirm=confirm,
    )
    assert written[-1] == "yes\n"
    pairing._handle_agent_line(
        "[agent] Authorize service 00001124-0000-1000-8000-00805f9b34fb (yes/no): ",
        write=written.append,
        confirm=confirm,
    )
    assert written[-1] == "no\n"


def test_agent_declines_the_pairing_when_the_operator_does_not_confirm():
    written: list[str] = []
    pairing._handle_agent_line(
        "[agent] Confirm passkey 123456 (yes/no): ",
        write=written.append,
        confirm=lambda passkey: False,
    )
    assert written == ["no\n"]


def test_agent_ignores_lines_that_are_not_prompts():
    written: list[str] = []
    pairing._handle_agent_line(
        "[CHG] Controller AA:BB:CC:DD:EE:FF Discoverable: yes",
        write=written.append,
        confirm=lambda passkey: True,
    )
    assert written == []


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


ESC2 = "\x1b"


def test_open_commands_order_registers_the_agent_before_opening():
    # The ordered open sequence the single session runs. agent/default-agent first
    # so the DisplayYesNo agent is the default before pairable/discoverable go on in
    # that same session (the issue #128 fix); select targets the receive adapter;
    # the bluez-side timeouts match the window so the adapter self-closes even if
    # this process is killed.
    commands = pairing._open_commands("AA:BB:CC:DD:EE:FF", 120)
    assert commands == [
        "agent DisplayYesNo",
        "default-agent",
        "select AA:BB:CC:DD:EE:FF",
        "pairable-timeout 120",
        "discoverable-timeout 120",
        "pairable on",
        "discoverable on",
    ]


def test_parse_device_name_reads_a_name_through_ansi_and_the_name_field_form():
    # The window tracks the phone's name so the web page can show whose pairing
    # request it is. bluetoothctl prints it two ways: a plain "Device <addr> <name>"
    # line and a "[CHG] Device <addr> Name: <name>" change line. Both are parsed
    # through the same ANSI stripping as the scan listing.
    assert pairing._parse_device_name(
        f"{ESC2}[0;92m[NEW]{ESC2}[0m Device AA:BB:CC:DD:EE:01 Pixel 7"
    ) == "Pixel 7"
    assert pairing._parse_device_name(
        "[CHG] Device AA:BB:CC:DD:EE:01 Name: Pixel 7"
    ) == "Pixel 7"
    # An address-as-name line carries no real name, and an unrelated line none.
    assert pairing._parse_device_name("Device AA:BB:CC:DD:EE:01 AA-BB-CC-DD-EE-01") is None
    assert pairing._parse_device_name("[agent] Confirm passkey 123456 (yes/no): ") is None


def test_parse_pair_result_reads_success_and_failure_through_ansi():
    # The window reports a terminal result to the web state machine from the
    # process output: "Pairing successful" means paired, the failure lines mean
    # failed. Anything else is not a result.
    assert pairing._parse_pair_result("Pairing successful") == "ok"
    assert pairing._parse_pair_result(
        f"{ESC2}[0;91mFailed to pair{ESC2}[0m: org.bluez.Error.AuthenticationFailed"
    ) == "fail"
    assert pairing._parse_pair_result("Pairing Not Allowed") == "fail"
    assert pairing._parse_pair_result("[CHG] Device AA:BB:CC:DD:EE:01 Connected: yes") is None


def test_trusting_a_new_phone_prints_no_address(capsys):
    # The web service runs the window under systemd, so stdout is the journal.
    # The trust line must name the phone but never carry its Bluetooth address.
    phone = "11:22:33:44:55:66"
    calls = {"n": 0}

    def session(commands, timeout=None):
        if "paired-devices" in commands:
            calls["n"] += 1
            return "" if calls["n"] == 1 else f"Device {phone} Pat's Phone\n"
        return ""

    _run(session, FakeProc(), FakeClock(), duration=2.0)

    out = capsys.readouterr().out
    assert "Pat's Phone" in out
    assert phone not in out
    import re

    assert not re.search(r"(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}", out)


def test_window_aborts_and_closes_when_the_long_lived_select_fails():
    # The preflight can pass and the adapter still vanish before the real open.
    # The long-lived session then reports "not available" and would leave
    # pairable/discoverable on the default controller. The window must notice,
    # abort with AdapterSelectFailed, and still close on the intended adapter.
    sessions: list[list[str]] = []
    proc = FakeProc()
    clock = FakeClock()

    def session(commands, timeout=None):
        sessions.append(list(commands))
        return ""

    def sleep(seconds):
        clock.sleep(seconds)
        if proc.reader is not None:
            proc.reader(f"Controller {CONTROLLER} not available\n")

    with pytest.raises(pairing.AdapterSelectFailed):
        pairing.run_pairing_window(
            CONTROLLER,
            duration=120.0,
            spawn=lambda: proc,
            confirm=lambda passkey, device=None: False,
            session=session,
            monotonic=clock.monotonic,
            sleep=sleep,
        )

    assert clock.t - 1000.0 < 120.0
    assert proc.terminated
    closed = [s for s in sessions if "discoverable off" in s]
    assert closed and closed[0][0] == f"select {CONTROLLER}"


# --------------------------------------------------------------------------- #
# Writes to the agent process after it has gone (BrokenPipeError on the hub)
# --------------------------------------------------------------------------- #


class _BrokenStdin:
    """A stdin whose reader (bluetoothctl) has exited: every write breaks."""

    def __init__(self, error: Exception):
        self._error = error

    def write(self, data):
        raise self._error

    def flush(self):
        raise self._error


class _GonePopen:
    def __init__(self, stdin):
        self.stdin = stdin


@pytest.mark.parametrize(
    "error",
    [BrokenPipeError(32, "Broken pipe"), OSError(9, "bad fd"), ValueError("I/O operation on closed file.")],
)
def test_send_after_the_process_has_exited_does_not_raise(error):
    # The hub log showed BrokenPipeError out of send() when a confirm answer
    # arrived after bluetoothctl had already gone. A write to a dead agent must be
    # a quiet no-op that reports it was not delivered, never an exception.
    ctl = pairing._Bluetoothctl(_GonePopen(_BrokenStdin(error)))
    assert ctl.send("yes") is False


def test_send_with_no_stdin_does_not_raise():
    assert pairing._Bluetoothctl(_GonePopen(None)).send("yes") is False


def test_a_confirm_answer_that_arrives_after_the_process_exited_is_dropped():
    # The reader thread hands a "Confirm passkey" prompt to the dispatcher, whose
    # confirm callback can block for a long time. By the time it answers, the
    # process may be gone; the answer must be dropped without raising.
    class DeadProc:
        def send(self, line):
            raise BrokenPipeError(32, "Broken pipe")

    on_line = pairing._make_line_handler(
        DeadProc(),
        confirm=lambda passkey, device=None: True,
        report=None,
        stop_event=None,
    )
    on_line("[agent] Confirm passkey 123456 (yes/no): ")
    on_line("[agent] Authorize service 0000110b-0000-1000-8000-00805f9b34fb (yes/no): ")


def test_opening_the_window_survives_a_process_that_died_at_once():
    # If bluetoothctl dies before the open commands are written, the window must
    # still run its clock and close the adapter, not crash on the first write.
    class DyingProc(FakeProc):
        def send(self, line):
            raise BrokenPipeError(32, "Broken pipe")

    sessions: list[list[str]] = []

    def session(commands, timeout=None):
        sessions.append(list(commands))
        return ""

    _run(session, DyingProc(), FakeClock(), duration=5.0)
    assert any("discoverable off" in s for s in sessions)
