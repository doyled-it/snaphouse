"""snapclient-side helpers: room settings on the hub, speaker keepalive on a Pi.

Two jobs that both live on the snapclient side of snaphouse:

  - `apply_client_settings` runs on the hub, once per apply, after snapserver is
    up. snapserver is the single place that knows every room's name and latency
    offset, so the repo's room labels (vars/hosts.yml `room_label`) and tuned
    offsets (`latency_ms`) are pushed into it over the JSON-RPC control API. It
    sends only the requests that change something and is a no-op on a re-run.

  - `keepalive` runs on pi-bedroom every 30 seconds from a systemd timer. The JBL
    is off most of the time; keepalive reconnects it whenever it powers on and
    logs only when the connection state changes, so days with the speaker off
    leave one log line, not a loop of errors, and the speaker returns within a
    tick or two (design: "Bluetooth speakers that come and go", Review Focus 1).

Both the RPC transport and the Bluetooth commands are injected so the logic is
unit tested without a network or hardware (tests/python/test_clients.py and
test_keepalive.py). The `main` entry points wire the real transport and the real
`bluetoothctl` for the roles to call from the on-device venv.

The speaker's address is the household's. It lives only in /etc/snaphouse/
local.yml and is read in at runtime; it never reaches the repo, and a log line
carries only its last two octets.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path
from typing import Callable, Optional

LOCAL_SETTINGS = Path("/etc/snaphouse/local.yml")

# snapserver's JSON-RPC endpoint on the hub itself. The hub applies client
# settings against its own loopback, so this never leaves the machine.
DEFAULT_RPC_URL = "http://localhost:1780/jsonrpc"

# Where the last-seen connection state is persisted between timer ticks. It lives
# on tmpfs: a reboot clears it, and the first tick after boot logs its state once.
DEFAULT_STATE_PATH = Path("/run/snaphouse/bt-keepalive.state")

# The connection states keepalive reports.
CONNECTED = "connected"
CONNECTING = "connecting"
ABSENT = "absent"

# Injectable seams.
Rpc = Callable[[str, dict], dict]
Probe = Callable[[str], bool]
Log = Callable[[str], None]


# --------------------------------------------------------------------------- #
# Hub: room names and latency offsets
# --------------------------------------------------------------------------- #


def _known_clients(status: dict) -> dict[str, dict]:
    """Map client id to its current {name, latency} from a Server.GetStatus result.

    A client snapserver has seen appears in a group whether or not it is
    connected right now, which is what lets the hub set a disconnected room's
    name and latency so they take effect when it returns.
    """
    known: dict[str, dict] = {}
    for group in status.get("server", {}).get("groups", []):
        for client in group.get("clients", []):
            config = client.get("config", {})
            known[client["id"]] = {
                "name": config.get("name"),
                "latency": config.get("latency"),
            }
    return known


def apply_client_settings(rpc: Rpc, clients: dict[str, dict]) -> list[str]:
    """Push each room's name and latency into snapserver; return the ids changed.

    `clients` maps a host id (living/office/bedroom) to {"label", "latency_ms"}.
    For every room snapserver already knows, a Client.SetName or Client.SetLatency
    is sent only where the server's current value differs from the repo's. Rooms
    snapserver has never seen are skipped. Re-running against a server that
    already carries the values sends nothing and returns an empty list.
    """
    known = _known_clients(rpc("Server.GetStatus", {}))
    changed: list[str] = []
    for host_id, want in clients.items():
        current = known.get(host_id)
        if current is None:
            continue
        touched = False
        if current["name"] != want["label"]:
            rpc("Client.SetName", {"id": host_id, "name": want["label"]})
            touched = True
        if current["latency"] != want["latency_ms"]:
            rpc("Client.SetLatency", {"id": host_id, "latency": want["latency_ms"]})
            touched = True
        if touched:
            changed.append(host_id)
    return changed


class HttpRpc:
    """A JSON-RPC 2.0 caller over HTTP, returning each response's `result`.

    Stdlib only (urllib): the hub applies settings against its own snapserver, so
    no third-party HTTP client is pulled in. A JSON-RPC error or a transport
    failure raises.
    """

    def __init__(self, url: str = DEFAULT_RPC_URL, *, timeout: float = 5.0):
        self.url = url
        self.timeout = timeout
        self._next_id = 0

    def __call__(self, method: str, params: dict) -> dict:
        self._next_id += 1
        payload = {
            "id": self._next_id,
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
        }
        request = urllib.request.Request(
            self.url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            body = json.loads(response.read().decode("utf-8"))
        if "error" in body:
            raise RuntimeError(f"snapserver error for {method}: {body['error']}")
        return body.get("result", {})


# --------------------------------------------------------------------------- #
# Bedroom: Bluetooth speaker keepalive
# --------------------------------------------------------------------------- #


def redact(mac: str) -> str:
    """The address reduced to its last two octets, for a safe log line.

    The same rule snaphouse.adapters uses: the full address identifies the house,
    so it never reaches the journal.
    """
    octets = [part for part in mac.split(":") if part]
    tail = ":".join(octets[-2:]) if octets else mac
    return tail.upper()


def _read_state(state_path: Path) -> Optional[str]:
    try:
        return state_path.read_text().strip() or None
    except FileNotFoundError:
        return None


def _write_state(state_path: Path, state: str) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=state_path.parent, prefix=state_path.name + ".")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(state + "\n")
        os.replace(tmp_name, state_path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def keepalive(
    mac: str,
    *,
    is_connected: Probe,
    connect: Probe,
    state_path: Path = DEFAULT_STATE_PATH,
    log: Log = lambda message: print(message, flush=True),
) -> str:
    """Reconnect the speaker if needed and log only when its state changes.

    Returns one of "connected", "connecting", or "absent":

      - connected:  the link is already up; no connect command is issued.
      - connecting: the link was down and a connect attempt was accepted (the
                    speaker is powered on; it will show connected on the next tick).
      - absent:     the link is down and a connect attempt did not take, which is
                    the normal resting state while the speaker is switched off.

    The last-seen state is persisted to `state_path`, so across the fresh process
    the timer starts each tick, a log line is written only on a transition. A long
    stretch of absence logs once; the speaker returning logs once.
    """
    if is_connected(mac):
        state = CONNECTED
    elif connect(mac):
        state = CONNECTING
    else:
        state = ABSENT

    previous = _read_state(state_path)
    if state != previous:
        log(
            f"bt-keepalive: speaker ending in :{redact(mac)} "
            f"{previous or 'unknown'} -> {state}"
        )
        _write_state(state_path, state)
    return state


def _bluetoothctl(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bluetoothctl", *args], capture_output=True, text=True, check=False
    )


def _is_connected(mac: str) -> bool:
    """True when bluetoothctl reports the speaker currently connected."""
    return "Connected: yes" in _bluetoothctl("info", mac).stdout


def _connect(mac: str) -> bool:
    """Attempt to connect the speaker; True only when the link actually came up.

    bluetoothctl's exit status is not reliable here, so success is read from its
    output. A connect to a powered-off speaker fails quickly and quietly: its
    output is captured, never printed, so an absent speaker produces no log line.
    """
    completed = _bluetoothctl("connect", mac)
    return "Connection successful" in (completed.stdout + completed.stderr)


# --------------------------------------------------------------------------- #
# CLI entry points, used by the roles from the on-device venv
# --------------------------------------------------------------------------- #


def _load_local(settings: Path) -> dict:
    """Load local.yml as a plain mapping (empty when it has no keys)."""
    from ruamel.yaml import YAML

    data = YAML().load(settings.read_text())
    return data if data is not None else {}


def _speaker_mac(name: str, settings: Path) -> Optional[str]:
    """The paired address for speaker `name`, or None when it is not set yet."""
    speakers = _load_local(settings).get("speakers") or {}
    mac = speakers.get(name)
    return mac or None


def _cmd_apply_clients(args: argparse.Namespace) -> int:
    clients = json.loads(args.clients)
    changed = apply_client_settings(HttpRpc(args.url), clients)
    for host_id in changed:
        print(host_id)
    return 0


def _cmd_keepalive(args: argparse.Namespace) -> int:
    mac = _speaker_mac(args.name, Path(args.settings))
    if mac is None:
        # Nothing paired yet: nothing to keep alive, and not an error.
        print(f"bt-keepalive: speaker {args.name!r} is not paired yet")
        return 0
    keepalive(
        mac,
        is_connected=_is_connected,
        connect=_connect,
        state_path=Path(args.state),
    )
    return 0


def _cmd_speaker_state(args: argparse.Namespace) -> int:
    """Print the speaker's resting state for the verify check.

    Connected and absent are both normal for a speaker that comes and goes, so
    both exit 0; only an unreadable Bluetooth stack (bluetoothctl missing) raises
    and fails the check.
    """
    mac = _speaker_mac(args.name, Path(args.settings))
    if mac is None:
        print(f"{args.name} not paired yet")
        return 0
    state = CONNECTED if _is_connected(mac) else ABSENT
    print(f"{args.name} {state}")
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    parser = argparse.ArgumentParser(
        prog="snaphouse.clients",
        description="Apply room settings on the hub, or keep a speaker connected.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    apply_clients = sub.add_parser(
        "apply-clients", help="push room names and latencies into snapserver (hub)"
    )
    apply_clients.add_argument("--clients", required=True, help="JSON host_id -> {label, latency_ms}")
    apply_clients.add_argument("--url", default=DEFAULT_RPC_URL)
    apply_clients.set_defaults(func=_cmd_apply_clients)

    keep = sub.add_parser("keepalive", help="reconnect a speaker if needed (timer)")
    keep.add_argument("name", help="which speaker (a key under speakers in local.yml)")
    keep.add_argument("--settings", default=str(LOCAL_SETTINGS))
    keep.add_argument("--state", default=str(DEFAULT_STATE_PATH))
    keep.set_defaults(func=_cmd_keepalive)

    state = sub.add_parser("speaker-state", help="print a speaker's state (verify)")
    state.add_argument("name", help="which speaker (a key under speakers in local.yml)")
    state.add_argument("--settings", default=str(LOCAL_SETTINGS))
    state.set_defaults(func=_cmd_speaker_state)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
