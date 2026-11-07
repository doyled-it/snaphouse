"""snapclient-side helpers: room settings on the hub, speaker keepalive on a Pi.

Two jobs that both live on the snapclient side of snaphouse:

  - `apply_client_settings` runs on the hub, once per apply, after snapserver is
    up. snapserver is the single place that knows every room's name and latency
    offset, so the repo's room labels (vars/hosts.yml `room_label`) and tuned
    offsets (`latency_ms`) are pushed into it over the JSON-RPC control API. It
    sends only the requests that change something and is a no-op on a re-run.

  - `keepalive` runs on nightstand every 30 seconds from a systemd timer. The JBL
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

from snaphouse.adapters import AdapterNotFound, AdapterSelectFailed, resolve_adapter

LOCAL_SETTINGS = Path("/etc/snaphouse/local.yml")

# snapserver's JSON-RPC endpoint on the hub itself. The hub applies client
# settings against its own loopback, so this never leaves the machine.
DEFAULT_RPC_URL = "http://localhost:1780/jsonrpc"

# Where the last-seen connection state is persisted between timer ticks. It lives
# on tmpfs: a reboot clears it, and the first tick after boot logs its state once.
DEFAULT_STATE_PATH = Path("/run/snaphouse/bt-keepalive.state")

# The connection states keepalive reports.
CONNECTED = "connected"
ABSENT = "absent"
# The send adapter could not be selected (bluetoothctl reported it "not
# available"). Treated like absence for snapclient's sake: the player is kept
# stopped, but logged distinctly so an adapter fault is not mistaken for a
# speaker that is simply switched off.
FAULT = "fault"

# The snapclient service keepalive drives on a Bluetooth-output host.
SNAPCLIENT_SERVICE = "snapclient.service"

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


def _client_groups(status: dict) -> dict[str, dict]:
    """Map client id to its group's id and current stream from a status result.

    snapserver holds every client in a group, and the group is what carries the
    stream a room plays. A client it has seen is listed whether or not it is
    connected, so a disconnected room's group can still be read and corrected.
    """
    groups: dict[str, dict] = {}
    for group in status.get("server", {}).get("groups", []):
        group_id = group.get("id")
        stream = group.get("stream_id")
        for client in group.get("clients", []):
            groups[client["id"]] = {"id": group_id, "stream": stream}
    return groups


def apply_client_settings(
    rpc: Rpc, clients: dict[str, dict], house_stream: str = "House"
) -> list[str]:
    """Push each room's name, latency, and stream into snapserver; return ids changed.

    `clients` maps a host id (living/office/bedroom) to {"label", "latency_ms"}.
    For every room snapserver already knows:

      - a Client.SetName or Client.SetLatency is sent only where the server's
        current value differs from the repo's, and
      - the room's group is moved onto the House meta stream (`house_stream`) with
        Group.SetStream whenever snapserver has it on any other stream.

    The stream move is why this exists alongside the snapserver.conf
    `default_source`: default_source only steers a client snapserver has never
    seen, but the living, office, and bedroom groups are already remembered in
    server.json on whatever stream they last had (often the bare AirPlay input),
    and stay there until moved here. One Group.SetStream is sent per group (rooms
    that share a group are moved once). Rooms snapserver has never seen are
    skipped. Re-running against a server that already carries the applied values
    (names, latencies, and House as every room's stream) sends nothing and
    returns an empty list.
    """
    status = rpc("Server.GetStatus", {})
    known = _known_clients(status)
    groups = _client_groups(status)
    changed: list[str] = []
    moved_groups: set[str] = set()
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
        group = groups.get(host_id)
        if (
            group is not None
            and group["id"] is not None
            and group["stream"] != house_stream
        ):
            if group["id"] not in moved_groups:
                rpc("Group.SetStream", {"id": group["id"], "stream_id": house_stream})
                moved_groups.add(group["id"])
            touched = True
        if touched:
            changed.append(host_id)
    return changed


def check_streams(
    rpc: Rpc,
    *,
    required: tuple[str, ...] = ("AirPlay", "Bluetooth", "House"),
    bt_sampleformat: str = "44100:16:2",
) -> list[str]:
    """Return a list of problems with snapserver's streams (empty when healthy).

    The hub's verify checks call this through the venv Python instead of jq:
    Server.GetStatus carries cover art and track metadata inline, and that
    payload can contain unescaped control characters that jq (and a strict JSON
    parser) reject, so a bloated status would otherwise fail verify and roll the
    hub back. `rpc` is a tolerant caller (HttpRpc parses with strict=False).
    """
    status = rpc("Server.GetStatus", {})
    server = status.get("server")
    if not server:
        return ["snapserver returned no server status"]
    streams = server.get("streams", [])
    ids = {stream.get("id") for stream in streams}
    problems: list[str] = []
    missing = [want for want in required if want not in ids]
    if missing:
        problems.append("snapserver is missing streams: " + " ".join(missing))
    bluetooth = next((s for s in streams if s.get("id") == "Bluetooth"), None)
    if bluetooth is not None:
        fmt = bluetooth.get("uri", {}).get("query", {}).get("sampleformat")
        if fmt != bt_sampleformat:
            problems.append(
                f"Bluetooth stream sample format is '{fmt}', expected {bt_sampleformat}"
            )
    return problems


def rpc_result(text: str, method: str) -> dict:
    """Parse a JSON-RPC response body and return its `result`, or raise on error.

    snapserver embeds cover art and track metadata straight into its responses
    (Server.GetStatus carries the active stream's artData and title), and that
    payload can contain unescaped control characters. Strict JSON (and jq) reject
    those with 'control characters must be escaped', which made a bloated status
    fail the hub's own readers: the apply step's apply-clients and the verify
    checks, rolling the hub back to its last-good commit. json.loads(strict=False)
    accepts control characters inside strings, so every hub-side reader tolerates
    the payload. A JSON-RPC error field still raises.
    """
    body = json.loads(text, strict=False)
    if "error" in body:
        raise RuntimeError(f"snapserver error for {method}: {body['error']}")
    return body.get("result", {})


class HttpRpc:
    """A JSON-RPC 2.0 caller over HTTP, returning each response's `result`.

    Stdlib only (urllib): the hub applies settings against its own snapserver, so
    no third-party HTTP client is pulled in. A JSON-RPC error or a transport
    failure raises. The response is parsed tolerantly (see rpc_result), so a
    status bloated with inline art never fails the parse.
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
            text = response.read().decode("utf-8")
        return rpc_result(text, method)


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
    is_client_active: Callable[[], bool],
    start_client: Callable[[], None],
    stop_client: Callable[[], None],
    state_path: Path = DEFAULT_STATE_PATH,
    log: Log = lambda message: print(message, flush=True),
) -> str:
    """Reconnect the speaker if needed, own snapclient's lifecycle, log on change.

    Returns "connected" or "absent":

      - connected:  the link is up, either already or because a connect attempt
                    just took (bluetoothctl connect is synchronous, so a success
                    means the speaker is connected now).
      - absent:     the link is down and a connect attempt did not take, which is
                    the normal resting state while the speaker is switched off.

    On a Bluetooth-output host snapclient is not enabled at boot; keepalive drives
    it, so it only ever runs while the speaker is actually there:

      - speaker up, snapclient not running   -> start it (recovery within one tick)
      - speaker gone, snapclient still running -> stop it, so a speaker that
        vanished cannot leave snapclient in a restart loop filling the journal.

    The reconcile is against snapclient's real state, so a run where it already
    matches the link sends no start or stop command (no churn on every tick). A
    connect command is issued only when the speaker is not already connected.

    The last-seen state is persisted to `state_path`, so across the fresh process
    the timer starts each tick a log line is written only on a transition. A long
    stretch of absence logs once; the speaker returning logs once.
    """
    try:
        if is_connected(mac):
            state = CONNECTED
        elif connect(mac):
            # bluetoothctl connect is synchronous: a success means the link is up.
            state = CONNECTED
        else:
            state = ABSENT
    except AdapterSelectFailed:
        # The send adapter could not be selected, so the speaker cannot be
        # reached through it. Treat it as a fault: keep snapclient stopped (the
        # link is not up) and report it distinctly below.
        state = FAULT

    link_up = state == CONNECTED
    if link_up and not is_client_active():
        start_client()
    elif not link_up and is_client_active():
        stop_client()

    previous = _read_state(state_path)
    if state != previous:
        if state == FAULT:
            # No address in this line: the fault is the adapter's, not a speaker's.
            log("bt-keepalive: send adapter fault; keeping snapclient stopped")
        else:
            log(
                f"bt-keepalive: speaker ending in :{redact(mac)} "
                f"{previous or 'unknown'} -> {state}"
            )
        _write_state(state_path, state)
    return state


def snapclient_verify_ok(*, speaker_connected: bool, client_active: bool) -> bool:
    """Whether snapclient's state is healthy on a Bluetooth-output host.

    snapclient follows the speaker there, so the two must agree: the link is up
    and the player is running, or the speaker is absent and the player is stopped.
    A speaker that is simply switched off (the JBL's normal state) is healthy and
    must never fail verify, or the self-update timer would roll the machine back
    to its last-good commit every time the speaker is off. Disagreement is a real
    fault: the link is up but the player is down, or the player is up while the
    speaker is gone.
    """
    return speaker_connected == client_active


def _run_session(commands: list[str]) -> str:
    """Run a batch of bluetoothctl commands in one session; return its stdout.

    The commands share one session so a leading `select <controller>` sets the
    target for the rest of the batch, exactly as snaphouse.pairing does. That
    selection is what drives the speaker through the send adapter and not, on the
    hub, through the receive dongle that happens to be bluetoothctl's default.
    """
    script = "".join(command + "\n" for command in commands)
    completed = subprocess.run(
        ["bluetoothctl"], input=script, capture_output=True, text=True, check=False
    )
    return completed.stdout


def _controller_probe(
    controller: str, *, session: Callable[[list[str]], str] = _run_session
) -> tuple[Probe, Probe]:
    """is_connected / connect for `mac`, each selecting `controller` first.

    `controller` is the adapter's address (local.yml's bt_send_adapter), not its
    hciN. bluetoothctl's `select` takes the address: a `select hciN` prints
    "Controller hciN not available" and silently stays on the index-ordered
    default (issue #30), which on the hub is the receive dongle, the wrong one.
    Every session begins with `select <address>`, so info and connect act on the
    send adapter.

    bluetoothctl's exit status is not reliable here, so both results are read from
    its output; a connect to a powered-off speaker fails quietly (its output is
    captured, never printed). If a session's output reports the controller is not
    available after the select, the adapter could not be selected and both probes
    raise AdapterSelectFailed rather than read the default controller's state.
    """

    def run(commands: list[str]) -> str:
        output = session(commands)
        if "not available" in output:
            raise AdapterSelectFailed(
                "bluetoothctl could not select the send Bluetooth adapter"
            )
        return output

    def is_connected(mac: str) -> bool:
        return "Connected: yes" in run([f"select {controller}", f"info {mac}"])

    def connect(mac: str) -> bool:
        return "Connection successful" in run([f"select {controller}", f"connect {mac}"])

    return is_connected, connect


def _send_controller(settings: Path, *, sys_root: Optional[Path] = None) -> str:
    """Return the send adapter's address from local.yml, proving it is present.

    The address (not an hciN) is what bluetoothctl's `select` takes, so this is
    the value every send-adapter session selects by. resolve_adapter is the
    presence check: it maps the address to a current hciN and raises if no present
    controller matches, but the hciN it returns is discarded here. The hub has two
    dongles (receive and send), and the speaker must be driven through the send
    one. Raises AdapterNotFound when the address is unset or absent; the message
    names only the last two octets.
    """
    mac = _load_local(settings).get("bt_send_adapter")
    if not mac:
        raise AdapterNotFound("bt_send_adapter is not set in /etc/snaphouse/local.yml")
    if sys_root is None:
        resolve_adapter(mac)
    else:
        resolve_adapter(mac, sys_root)
    return mac


def _speaker_connected(name: str, settings: Path) -> bool:
    """Whether speaker `name` is connected, probed through the send adapter.

    Unpaired, or the send adapter not present, counts as not connected (absent),
    so a verify check reads the speaker's resting state without raising.
    """
    mac = _speaker_mac(name, settings)
    if mac is None:
        return False
    try:
        controller = _send_controller(settings)
    except AdapterNotFound:
        return False
    is_connected, _connect = _controller_probe(controller)
    try:
        return is_connected(mac)
    except AdapterSelectFailed:
        # The send adapter could not be selected, so the speaker cannot be read
        # through it. For verify this is the resting "absent" state, not a crash.
        return False


def _client_is_active() -> bool:
    """True when systemd reports snapclient active."""
    completed = subprocess.run(
        ["systemctl", "is-active", "--quiet", SNAPCLIENT_SERVICE], check=False
    )
    return completed.returncode == 0


def _start_client() -> None:
    subprocess.run(["systemctl", "start", SNAPCLIENT_SERVICE], check=False)


def _stop_client() -> None:
    subprocess.run(["systemctl", "stop", SNAPCLIENT_SERVICE], check=False)


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
    changed = apply_client_settings(HttpRpc(args.url), clients, args.house_stream)
    for host_id in changed:
        print(host_id)
    return 0


def _cmd_verify_streams(args: argparse.Namespace) -> int:
    try:
        problems = check_streams(HttpRpc(args.url))
    except Exception as exc:  # noqa: BLE001 - a verify check reports, never crashes
        print(f"could not read snapserver status: {exc}", file=sys.stderr)
        return 1
    for problem in problems:
        print(problem, file=sys.stderr)
    return 1 if problems else 0


def _cmd_keepalive(args: argparse.Namespace) -> int:
    settings = Path(args.settings)
    mac = _speaker_mac(args.name, settings)
    if mac is None:
        # Nothing paired yet (for example the hub before the Vizio is paired in
        # build step 6). There is no speaker to reach, so snapclient must stay
        # stopped; make sure it is, then leave. Not an error.
        if _client_is_active():
            _stop_client()
        print(f"bt-keepalive: speaker {args.name!r} is not paired yet")
        return 0
    try:
        controller = _send_controller(settings)
    except AdapterNotFound as exc:
        # Without the send adapter there is no way to reach the speaker, so keep
        # snapclient stopped and leave. The bluetooth verify check reports the
        # missing adapter; keepalive must not fail the apply over it. The message
        # names only the last two octets of the address.
        if _client_is_active():
            _stop_client()
        print(f"bt-keepalive: {exc}", file=sys.stderr)
        return 0
    is_connected, connect = _controller_probe(controller)
    keepalive(
        mac,
        is_connected=is_connected,
        connect=connect,
        is_client_active=_client_is_active,
        start_client=_start_client,
        stop_client=_stop_client,
        state_path=Path(args.state),
    )
    return 0


def _cmd_verify_snapclient(args: argparse.Namespace) -> int:
    """Verify snapclient on a Bluetooth-output host (both states must agree).

    Passes when the speaker is connected and snapclient is active, or the speaker
    is absent and snapclient is inactive. The speaker being off is healthy. An
    unpaired speaker counts as absent.
    """
    speaker_connected = _speaker_connected(args.name, Path(args.settings))
    client_active = _client_is_active()
    if snapclient_verify_ok(
        speaker_connected=speaker_connected, client_active=client_active
    ):
        return 0
    speaker_word = "connected" if speaker_connected else "absent"
    client_word = "active" if client_active else "inactive"
    print(
        f"snapclient is {client_word} but the {args.name} speaker is {speaker_word}",
        file=sys.stderr,
    )
    return 1


def _cmd_speaker_state(args: argparse.Namespace) -> int:
    """Print the speaker's resting state for the verify check.

    Connected and absent are both normal for a speaker that comes and goes, so
    both exit 0; only an unreadable Bluetooth stack (bluetoothctl missing) raises
    and fails the check. The state is probed through the send adapter.
    """
    settings = Path(args.settings)
    if _speaker_mac(args.name, settings) is None:
        print(f"{args.name} not paired yet")
        return 0
    state = CONNECTED if _speaker_connected(args.name, settings) else ABSENT
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
    apply_clients.add_argument(
        "--house-stream",
        default="House",
        help="the stream every room's group is set to (the House meta stream)",
    )
    apply_clients.set_defaults(func=_cmd_apply_clients)

    verify_streams = sub.add_parser(
        "verify-streams", help="check snapserver serves the house streams (verify)"
    )
    verify_streams.add_argument("--url", default=DEFAULT_RPC_URL)
    verify_streams.set_defaults(func=_cmd_verify_streams)

    keep = sub.add_parser("keepalive", help="reconnect a speaker if needed (timer)")
    keep.add_argument("name", help="which speaker (a key under speakers in local.yml)")
    keep.add_argument("--settings", default=str(LOCAL_SETTINGS))
    keep.add_argument("--state", default=str(DEFAULT_STATE_PATH))
    keep.set_defaults(func=_cmd_keepalive)

    state = sub.add_parser("speaker-state", help="print a speaker's state (verify)")
    state.add_argument("name", help="which speaker (a key under speakers in local.yml)")
    state.add_argument("--settings", default=str(LOCAL_SETTINGS))
    state.set_defaults(func=_cmd_speaker_state)

    verify = sub.add_parser(
        "verify-snapclient", help="check snapclient matches the speaker (verify)"
    )
    verify.add_argument("name", help="which speaker (a key under speakers in local.yml)")
    verify.add_argument("--settings", default=str(LOCAL_SETTINGS))
    verify.set_defaults(func=_cmd_verify_snapclient)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
