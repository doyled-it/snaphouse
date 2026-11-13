"""Speaker buttons: turn the speaker's AVRCP key presses into stream control.

The Vizio soundbar (paired to the hub) and the JBL (paired to the nightstand) send
play, pause, next and previous as AVRCP. BlueZ exposes them as a Linux input
device named "<speaker name> (AVRCP)", where the name is BlueZ's remote device
Name. Its `uniq` is empty and its `phys` is the local adapter's address, so the
speaker's address cannot find it; the name can. This listener reads that device
and posts the Bluetooth stream's control request
(`Stream.Control`, id "Bluetooth") to snapserver, which avrcp_meta.py forwards to
the phone.

Rules (design: playback controls, Review Focus 2 to 5):

- The listener is unprivileged (local.yml is root-only). A root ExecStartPre runs
  `--write-address` to copy just the speaker's address into a runtime file, and
  to resolve the speaker's BlueZ Name from that address (Device1 `Name` over
  D-Bus, falling back to `Alias`) into a `name` file beside it.
- Only the configured speaker's input device is read: the one whose name equals
  "<name> (AVRCP)" exactly. The hub also has an "(AVRCP)" device for the phone
  (an A2DP source); it never matches, so the phone's own commands cannot loop
  back.
- One journal line when the speaker's input device is found and one when it goes.
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
import os
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
# With no speaker address (not paired yet) the loop returns after this many
# rescans (about ten minutes), so systemd restarts it and the root-only
# ExecStartPre reads local.yml again. A pairing done later is then picked up.
UNPAIRED_SCANS_BEFORE_EXIT = 120

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
# Written by root in ExecStartPre; read by the unprivileged listener.
ADDRESS_FILE = Path("/run/snaphouse-avrcp-keys/address")


# Also written by root: the speaker's BlueZ Name, beside the address.
NAME_FILE = Path("/run/snaphouse-avrcp-keys/name")
DEVICE_INTERFACE = "org.bluez.Device1"


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
    devices: list[InputDevice], name: Optional[str]
) -> Optional[InputDevice]:
    """The input device named exactly "<name> (AVRCP)", or None.

    BlueZ's AVRCP devices have an empty uniq, so the speaker's BlueZ Name is the
    only handle. Exact match only: a near-miss name (or the phone's) is never read.
    """
    if not name:
        return None
    wanted = f"{name} (AVRCP)"
    for device in devices:
        if device.name == wanted:
            return device
    return None


def _unwrap(value):
    # jeepney returns a variant as a (signature, value) tuple.
    if isinstance(value, tuple) and len(value) == 2 and isinstance(value[0], str):
        return value[1]
    return value


def speaker_name(objects: dict, address: str) -> Optional[str]:
    """The BlueZ Name (else Alias) of the device at `address`, from a
    GetManagedObjects reply ({path: {interface: {property: variant}}})."""
    if not address:
        return None
    wanted = address.strip().lower()
    for interfaces in objects.values():
        props = interfaces.get(DEVICE_INTERFACE)
        if not props or "Address" not in props:
            continue
        if str(_unwrap(props["Address"])).lower() != wanted:
            continue
        for key in ("Name", "Alias"):
            if key in props:
                value = str(_unwrap(props[key])).strip()
                if value:
                    return value
    return None


def lookup_name(address: str) -> Optional[str]:  # pragma: no cover - needs the bus
    """Ask BlueZ over the system bus for the speaker's Name (root ExecStartPre)."""
    from jeepney import DBusAddress, MessageType, new_method_call
    from jeepney.io.blocking import open_dbus_connection

    connection = open_dbus_connection(bus="SYSTEM")
    try:
        manager = DBusAddress(
            "/", bus_name="org.bluez", interface="org.freedesktop.DBus.ObjectManager"
        )
        reply = connection.send_and_get_reply(
            new_method_call(manager, "GetManagedObjects"), timeout=5.0
        )
        if reply.header.message_type == MessageType.error:
            raise RuntimeError("GetManagedObjects failed")
        return speaker_name(reply.body[0], address)
    finally:
        connection.close()


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
    name: Callable[[], Optional[str]],
    read_devices: Callable[[], str],
    open_device: Callable[[str], tuple[Callable[[int], bytes], Callable[[], None]]],
    send: Callable[[str], None],
    log: Callable[[str], None],
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """Find the speaker's input device, read it, rescan when it goes.

    Returns only after a long stretch with no speaker name (see
    UNPAIRED_SCANS_BEFORE_EXIT); otherwise it loops forever.
    """
    unpaired = 0
    open_failing = False
    while True:
        device = None
        speaker = None
        try:
            speaker = name()
            if speaker:
                device = speaker_device(parse_input_devices(read_devices()), speaker)
        except Exception:  # noqa: BLE001 - missing name file or /proc: rescan
            device = None
        unpaired = 0 if speaker else unpaired + 1
        if unpaired >= UNPAIRED_SCANS_BEFORE_EXIT:
            return
        if device is None:
            sleep(RESCAN_SECONDS)
            continue
        try:
            read, close = open_device(device.event)
        except OSError as exc:
            if not open_failing:
                open_failing = True
                log(f"speaker buttons: cannot open {device.event}: {exc}")
            sleep(RESCAN_SECONDS)
            continue
        if open_failing:
            open_failing = False
            log(f"speaker buttons: reading again from {device.event}")
        log(f"speaker buttons: found the speaker's input device {device.event}")
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
        log("speaker buttons: the speaker's input device is gone, rescanning")
        sleep(RESCAN_SECONDS)


# --------------------------------------------------------------------------- #
# CLI entry point, run by snaphouse-avrcp-keys.service
# --------------------------------------------------------------------------- #


def _write_private(dest: Path, text: str) -> None:
    """Write `text` to `dest` mode 0600, owned like its directory."""
    fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, text.encode())
        os.fchmod(fd, 0o600)
        parent = dest.parent.stat()
        try:
            os.fchown(fd, parent.st_uid, parent.st_gid)
        except PermissionError:
            pass  # not root (tests): the file is already ours
    finally:
        os.close(fd)


def write_address(speaker: str, settings: Path, dest: Path) -> int:
    """Root step: copy the speaker's address out of local.yml for the listener,
    and resolve its BlueZ Name into `name` beside it.

    Both files are owned like their directory (the service user's RuntimeDirectory)
    and mode 0600. An unpaired speaker, unreadable local.yml or unanswered BlueZ
    writes an empty file, so the listener idles instead of failing the unit.
    """
    from snaphouse import clients

    try:
        address = clients.speaker_mac(speaker, settings) or ""
    except Exception:  # noqa: BLE001 - missing local.yml: nothing to listen for
        address = ""
    name = ""
    if address:
        try:
            name = lookup_name(address) or ""
        except Exception:  # noqa: BLE001 - bus down or device unknown: idle
            name = ""
    _write_private(dest, address)
    _write_private(dest.parent / "name", name)
    return 0


def read_address(path: Path) -> Optional[str]:
    """The address (or name) root wrote, or None when absent or empty."""
    try:
        return path.read_text().strip() or None
    except OSError:
        return None


def main(argv: Optional[list[str]] = None) -> int:
    from snaphouse import clients

    parser = argparse.ArgumentParser(prog="snaphouse.avrcp_keys")
    parser.add_argument("speaker", help="speaker name, a key under speakers in local.yml")
    parser.add_argument("--hub", required=True, help="hub hostname")
    parser.add_argument("--host", required=True, help="this machine's inventory key")
    parser.add_argument("--settings", type=Path, default=clients.LOCAL_SETTINGS)
    parser.add_argument(
        "--write-address",
        type=Path,
        help="root only (ExecStartPre): write the speaker address to this file, then exit",
    )
    parser.add_argument(
        "--address-file",
        type=Path,
        default=ADDRESS_FILE,
        help="where the listener reads the address root wrote",
    )
    parser.add_argument(
        "--name-file",
        type=Path,
        default=NAME_FILE,
        help="where the listener reads the speaker name root wrote",
    )
    args = parser.parse_args(argv)

    if args.write_address is not None:
        return write_address(args.speaker, args.settings, args.write_address)

    # A good Stream.Control gets an empty 200 reply, and a dead hub must not
    # stall the read loop for long on each press.
    rpc = clients.HttpRpc(rpc_url(args.hub, args.host), empty_ok=True, timeout=2.0)

    def send(command: str) -> None:
        rpc("Stream.Control", {"id": "Bluetooth", "command": command})

    def log(message: str) -> None:
        print(message, file=sys.stderr, flush=True)

    def open_device(event: str):
        fd = os.open(f"/dev/input/{event}", os.O_RDONLY)
        return (lambda n: os.read(fd, n)), (lambda: os.close(fd))

    run(
        name=lambda: read_address(args.name_file),
        read_devices=PROC_DEVICES.read_text,
        open_device=open_device,
        send=send,
        log=log,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
