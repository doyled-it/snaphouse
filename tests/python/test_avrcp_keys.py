"""Tests for the speaker-button listener (lib/snaphouse/avrcp_keys.py).

The Vizio (on the hub) and the JBL (on the nightstand) send AVRCP key presses that
BlueZ exposes as a "<speaker> (AVRCP)" input device. The listener turns them into
the Bluetooth stream's control contract. Pure functions are tested directly; the
run loop is driven through injected seams.
"""

from __future__ import annotations

import struct

from snaphouse import avrcp_keys as ak

SPEAKER = ":".join(["aa", "bb", "cc", "dd", "ee", "01"])
PHONE = ":".join(["aa", "bb", "cc", "dd", "ee", "02"])

PROC = f"""I: Bus=0019 Vendor=0000 Product=0001 Version=0000
N: Name="Power Button"
P: Phys=PNP0C0C/button/input0
S: Sysfs=/devices/LNXSYSTM:00/input/input0
U: Uniq=
H: Handlers=kbd event0 
B: PROP=0

I: Bus=0005 Vendor=0000 Product=0000 Version=0000
N: Name="Living Room Soundbar (AVRCP)"
P: Phys=ff:ee:dd:cc:bb:00
S: Sysfs=/devices/virtual/input/input20
U: Uniq={SPEAKER.upper()}
H: Handlers=kbd event5 
B: PROP=0

I: Bus=0005 Vendor=0000 Product=0000 Version=0000
N: Name="Some Phone (AVRCP)"
P: Phys=ff:ee:dd:cc:bb:00
S: Sysfs=/devices/virtual/input/input21
U: Uniq={PHONE}
H: Handlers=kbd event6 
B: PROP=0
"""


def test_parse_input_devices_reads_name_uniq_and_event():
    devices = ak.parse_input_devices(PROC)
    assert [d.event for d in devices] == ["event0", "event5", "event6"]
    assert devices[1].name == "Living Room Soundbar (AVRCP)"
    assert devices[1].uniq == SPEAKER.upper()


def test_speaker_device_matches_address_case_insensitively():
    devices = ak.parse_input_devices(PROC)
    assert ak.speaker_device(devices, SPEAKER).event == "event5"
    assert ak.speaker_device(devices, SPEAKER.upper()).event == "event5"


def test_speaker_device_never_picks_the_phone_or_a_non_avrcp_device():
    devices = ak.parse_input_devices(PROC)
    assert ak.speaker_device(devices, PHONE).event == "event6"  # only if configured
    other = ":".join(["aa", "bb", "cc", "dd", "ee", "99"])
    assert ak.speaker_device(devices, other) is None
    assert ak.speaker_device(devices, "") is None
    plain = [ak.InputDevice("Keyboard", SPEAKER, "event1")]
    assert ak.speaker_device(plain, SPEAKER) is None


def _event(etype, code, value):
    return struct.pack("llHHi", 0, 0, etype, code, value)


def test_decode_events_unpacks_24_byte_records_and_keeps_partial_tail():
    buf = _event(1, 164, 1) + _event(0, 0, 0) + b"\x00\x01"
    events, rest = ak.decode_events(buf)
    assert events == [(1, 164, 1), (0, 0, 0)]
    assert rest == b"\x00\x01"


def test_command_for_maps_keys_on_key_down_only():
    expected = {164: "playPause", 200: "play", 201: "pause", 207: "play",
                119: "pause", 163: "next", 165: "previous"}
    for code, command in expected.items():
        assert ak.command_for(code, 1) == command
        assert ak.command_for(code, 0) is None
        assert ak.command_for(code, 2) is None
    assert ak.command_for(30, 1) is None


def test_control_body_is_the_contract():
    body = ak.control_body("next")
    assert body["method"] == "Stream.Control"
    assert body["params"] == {"id": "Bluetooth", "command": "next"}


def test_handle_buffer_sends_one_command_for_a_held_button():
    sent = []
    buf = _event(1, 164, 1) + _event(1, 164, 2) + _event(1, 164, 2) + _event(1, 164, 0)
    ak.handle_buffer(buf, sent.append, lambda m: None)
    assert sent == ["playPause"]


def test_handle_buffer_logs_a_failed_post_once_and_does_not_retry():
    logs, calls = [], []

    def send(command):
        calls.append(command)
        raise RuntimeError("snapserver error: not controllable")

    ak.handle_buffer(_event(1, 163, 1), send, logs.append)
    assert calls == ["next"]
    assert len(logs) == 1 and "next" in logs[0]


def test_run_rescans_until_the_device_appears_and_again_after_it_goes():
    texts = [PROC.replace("(AVRCP)", "(X)"), PROC, PROC]
    opened, sleeps, sent = [], [], []
    events = [_event(1, 165, 1)]

    class Gone(Exception):
        pass

    def read_devices():
        return texts.pop(0) if texts else ""

    def sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) >= 4:
            raise Gone()

    def open_device(event):
        opened.append(event)
        chunks = list(events)
        events.clear()

        def read(n):
            if chunks:
                return chunks.pop(0)
            raise OSError(19, "No such device")

        return read, lambda: None

    try:
        ak.run(
            address=lambda: SPEAKER,
            read_devices=read_devices,
            open_device=open_device,
            send=sent.append,
            log=lambda m: None,
            sleep=sleep,
        )
    except Gone:
        pass
    assert opened == ["event5", "event5"]
    assert sent == ["previous"]
    assert sleeps and all(s == ak.RESCAN_SECONDS for s in sleeps)


def test_hub_posts_to_loopback_and_others_to_the_hub_name():
    assert ak.rpc_url("house", "house") == "http://127.0.0.1:1780/jsonrpc"
    assert ak.rpc_url("house", "nightstand") == "http://house.local/jsonrpc"


# --- Privilege split: root writes the address, the listener only reads it ------


def test_write_address_dumps_the_speaker_address_for_the_listener(tmp_path):
    settings = tmp_path / "local.yml"
    settings.write_text(f"speakers:\n  vizio: \"{SPEAKER}\"\n")
    out = tmp_path / "address"
    rc = ak.main(["vizio", "--hub", "house", "--host", "house",
                  "--settings", str(settings), "--write-address", str(out)])
    assert rc == 0
    assert out.read_text().strip() == SPEAKER
    assert (out.stat().st_mode & 0o777) == 0o600


def test_write_address_writes_an_empty_file_when_unpaired_or_unreadable(tmp_path):
    out = tmp_path / "address"
    rc = ak.main(["vizio", "--hub", "house", "--host", "house",
                  "--settings", str(tmp_path / "missing.yml"),
                  "--write-address", str(out)])
    assert rc == 0
    assert out.read_text() == ""


def test_main_wires_the_listener_to_the_address_file(tmp_path, monkeypatch):
    address_file = tmp_path / "address"
    address_file.write_text(SPEAKER + "\n")
    captured = {}
    monkeypatch.setattr(ak, "run", lambda **kw: captured.update(kw))
    posts = []

    class FakeRpc:
        def __init__(self, url):
            posts.append(url)

        def __call__(self, method, params):
            posts.append((method, params))

    from snaphouse import clients

    monkeypatch.setattr(clients, "HttpRpc", FakeRpc)
    rc = ak.main(["vizio", "--hub", "house", "--host", "nightstand",
                  "--address-file", str(address_file)])
    assert rc == 0
    assert captured["address"]() == SPEAKER
    captured["send"]("next")
    assert posts == [
        "http://house.local/jsonrpc",
        ("Stream.Control", {"id": "Bluetooth", "command": "next"}),
    ]
    address_file.write_text("")
    assert captured["address"]() is None
    address_file.unlink()
    assert captured["address"]() is None


def test_speaker_mac_is_a_public_helper():
    from snaphouse import clients

    assert callable(clients.speaker_mac)
    assert not hasattr(clients, "_speaker_mac")


def test_run_logs_once_when_open_keeps_failing_and_once_when_it_recovers():
    logs, sleeps, opened = [], [], []

    class Gone(Exception):
        pass

    def sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) >= 6:
            raise Gone()

    def open_device(event):
        opened.append(event)
        if len(opened) <= 3:
            raise PermissionError(13, "denied")

        def read(n):
            raise OSError(19, "No such device")

        return read, lambda: None

    try:
        ak.run(address=lambda: SPEAKER, read_devices=lambda: PROC,
               open_device=open_device, send=lambda c: None,
               log=logs.append, sleep=sleep)
    except Gone:
        pass
    failing = [m for m in logs if "cannot open" in m]
    recovered = [m for m in logs if "reading again" in m]
    assert len(failing) == 1 and len(recovered) == 1
    assert SPEAKER not in " ".join(logs)


def test_run_returns_after_a_long_unpaired_stretch_so_systemd_rereads_the_address():
    sleeps = []
    ak.run(address=lambda: None, read_devices=lambda: PROC,
           open_device=lambda e: None, send=lambda c: None,
           log=lambda m: None, sleep=sleeps.append)
    assert len(sleeps) == ak.UNPAIRED_SCANS_BEFORE_EXIT - 1
