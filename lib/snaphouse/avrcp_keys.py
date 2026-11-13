"""Speaker buttons: turn the speaker's AVRCP key presses into stream control.

The Vizio soundbar (paired to the hub) and the JBL (paired to the nightstand) send
play, pause, next and previous as AVRCP. BlueZ exposes them as a Linux input
device named "<speaker name> (AVRCP)" whose `uniq` is the speaker's address. This
listener reads that device and posts the Bluetooth stream's control request
(`Stream.Control`, id "Bluetooth") to snapserver, which avrcp_meta.py forwards to
the phone.

Rules (design: playback controls, Review Focus 2 to 5):

- Only the configured speaker's input device is read. The hub also has an
  "(AVRCP)" device for the phone (an A2DP source); it is never matched, because
  the match is on the speaker's address from local.yml, so the phone's own
  commands cannot loop back.
- Key down only. Repeat (value 2) and release (value 0) are ignored, so a held
  button sends one command.
- A failed post is logged as one line and not retried.
- When the device is gone (the speaker switched off, or a new /dev/input/eventN
  after it reconnects) the loop rescans every few seconds, so it needs no restart.

The address is the household's: it is read from local.yml on the device and is
never logged.
"""

from __future__ import annotations

import argparse
import struct
import sys
import time
from pathlib import Path
from typing import Callable, NamedTuple, Optional

# struct input_event on 64-bit Linux: timeval (two longs), type, code, value.
EVENT_FORMAT = "llHHi"
EVENT_SIZE = struct.calcsize(EVENT_FORMAT)

EV_KEY = 1
RESCAN_SECONDS = 5

KEY_COMMANDS = {
    164: "playPause",  # KEY_PLAYPAUSE
    200: "play",       # KEY_PLAYCD
    201: "pause",      # KEY_PAUSECD
    207: "play",       # KEY_PLAY
    119: "pause",      # KEY_PAUSE
    163: "next",       # KEY_NEXTSONG
    165: "previous",   # KEY_PREVIOUSSONG
}

HUB_LOOPBACK_URL = "http://127.0.0.1:1780/jsonrpc"
PROC_DEVICES = Path("/proc/bus/input/devices")


class InputDevice(NamedTuple):
    name: str
    uniq: str
    event: str


def parse_input_devices(text: str) -> list[InputDevice]:
    """Parse /proc/bus/input/devices into its devices with an event handler."""
    devices: list[InputDevice] = []
    for block in text.split("\n\n"):
        name = uniq = event = ""
        for line in block.splitlines():
            if line.startswith("N: Name="):
                name = line[len("N: Name="):].strip().strip('"')
            elif line.startswith("U: Uniq="):
                uniq = line[len("U: Uniq="):].strip()
            elif line.startswith("H: Handlers="):
                for word in line[len("H: Handlers="):].split():
                    if word.startswith("event"):
                        event = word
        if event:
            devices.append(InputDevice(name, uniq, event))
    return devices


def speaker_device(
    devices: list[InputDevice], address: str
) -> Optional[InputDevice]:
    """The "(AVRCP)" input device whose uniq is `address`, or None."""
    if not address:
        return None
    wanted = address.strip().lower()
    for device in devices:
        if device.name.endswith("(AVRCP)") and device.uniq.lower() == wanted:
            return device
    return None


def decode_events(buf: bytes) -> tuple[list[tuple[int, int, int]], bytes]:
    """Unpack whole input_event records as (type, code, value); return the tail."""
    events = []
    usable = len(buf) - len(buf) % EVENT_SIZE
    for offset in range(0, usable, EVENT_SIZE):
        _sec, _usec, etype, code, value = struct.unpack_from(
            EVENT_FORMAT, buf, offset
        )
        events.append((etype, code, value))
    return events, buf[usable:]


def command_for(code: int, value: int) -> Optional[str]:
    """The stream command for a key press, or None (not a mapped key-down)."""
    if value != 1:
        return None
    return KEY_COMMANDS.get(code)


def control_body(command: str) -> dict:
    """The Bluetooth stream control request every client sends."""
    return {
        "id": 1,
        "jsonrpc": "2.0",
        "method": "Stream.Control",
        "params": {"id": "Bluetooth", "command": command},
    }


def rpc_url(hub: str, host: str) -> str:
    """snapserver's JSON-RPC endpoint: loopback on the hub, the proxy elsewhere."""
    if host == hub:
        return HUB_LOOPBACK_URL
    return f"http://{hub}.local/jsonrpc"


def handle_buffer(
    buf: bytes, send: Callable[[str], None], log: Callable[[str], None]
) -> bytes:
    """Send one command per key-down in `buf`; return the undecoded tail.

    A failed send is logged once and not retried.
    """
    events, rest = decode_events(buf)
    for etype, code, value in events:
        if etype != EV_KEY:
            continue
        command = command_for(code, value)
        if command is None:
            continue
        try:
            send(command)
        except Exception as exc:  # noqa: BLE001 - one log line, never crash
            log(f"speaker button {command} failed: {exc}")
    return rest


def run(
    *,
    address: Callable[[], Optional[str]],
    read_devices: Callable[[], str],
    open_device: Callable[[str], tuple[Callable[[int], bytes], Callable[[], None]]],
    send: Callable[[str], None],
    log: Callable[[str], None],
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """Forever: find the speaker's input device, read it, rescan when it goes."""
    while True:
        device = None
        try:
            addr = address()
            if addr:
                device = speaker_device(parse_input_devices(read_devices()), addr)
        except Exception:  # noqa: BLE001 - missing local.yml or /proc: rescan
            device = None
        if device is None:
            sleep(RESCAN_SECONDS)
            continue
        try:
            read, close = open_device(device.event)
        except OSError:
            sleep(RESCAN_SECONDS)
            continue
        pending = b""
        try:
            while True:
                chunk = read(EVENT_SIZE * 16)
                if not chunk:
                    break
                pending = handle_buffer(pending + chunk, send, log)
        except OSError:
            pass  # the device went away (speaker off or reconnected)
        finally:
            close()
        sleep(RESCAN_SECONDS)


# --------------------------------------------------------------------------- #
# CLI entry point, run by snaphouse-avrcp-keys.service
# --------------------------------------------------------------------------- #


def main(argv: Optional[list[str]] = None) -> int:
    import os

    from snaphouse import clients

    parser = argparse.ArgumentParser(prog="snaphouse.avrcp_keys")
    parser.add_argument("speaker", help="speaker name, a key under speakers in local.yml")
    parser.add_argument("--hub", required=True, help="hub hostname")
    parser.add_argument("--host", required=True, help="this machine's inventory key")
    parser.add_argument("--settings", type=Path, default=clients.LOCAL_SETTINGS)
    args = parser.parse_args(argv)

    rpc = clients.HttpRpc(rpc_url(args.hub, args.host))

    def send(command: str) -> None:
        rpc("Stream.Control", {"id": "Bluetooth", "command": command})

    def log(message: str) -> None:
        print(message, file=sys.stderr, flush=True)

    def open_device(event: str):
        fd = os.open(f"/dev/input/{event}", os.O_RDONLY)
        return (lambda n: os.read(fd, n)), (lambda: os.close(fd))

    run(
        address=lambda: clients._speaker_mac(args.speaker, args.settings),
        read_devices=PROC_DEVICES.read_text,
        open_device=open_device,
        send=send,
        log=log,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
