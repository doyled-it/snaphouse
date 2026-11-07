"""snaphouse pair: pair a phone to the hub, or pair a Bluetooth speaker.

Pairing needs a person to put a device into pairing mode, so it stays a manual
command. There are two directions:

  - `snaphouse pair phone` (hub): open a short window on the receive adapter
    during which the hub is discoverable, pairable, and auto-accepts a "just
    works" pairing (bt-agent with NoInputNoOutput). The window always closes the
    adapter again, even if the agent dies early or the operator hits Ctrl-C: the
    hub must never be left discoverable. Devices paired during the window are
    trusted so they reconnect on their own.
  - `snaphouse pair speaker {vizio|jbl}`: scan on the send adapter, let the
    operator pick the speaker by name, then pair, trust, and connect it, and
    record its address in /etc/snaphouse/local.yml.

Adapters are resolved by address (see snaphouse.adapters), never by hciN index,
because the index can swap across reboots. The address and the paired speaker
addresses are the household's; they live only in local.yml and never in logs or
the repo.
"""

from __future__ import annotations

import argparse
import os
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Callable, Optional

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap

from snaphouse.adapters import AdapterNotFound, AdapterSelectFailed, resolve_adapter

LOCAL_SETTINGS = Path("/etc/snaphouse/local.yml")

# How long the inbound pairing window stays open. Short on purpose: the adapter
# is discoverable only while the operator is actively pairing a phone.
PAIRING_WINDOW_SECONDS = 120.0

# How long to scan for a speaker before listing what was found.
SCAN_SECONDS = 20.0

# How often the window loop wakes to re-check the clock.
POLL_INTERVAL = 1.0

# The speakers snaphouse knows about, each a key under `speakers` in local.yml.
SPEAKER_NAMES = ("vizio", "jbl")

# Type aliases for the injectable seams the tests drive. A session runs a batch
# of bluetoothctl commands in one interactive session and returns its stdout.
Session = Callable[..., str]
SpawnAgent = Callable[[str], "subprocess.Popen"]


def _run_session(commands: list[str], *, timeout: Optional[float] = None) -> str:
    """Run a batch of bluetoothctl commands in one session; return its stdout.

    The commands share one session so that a leading `select <controller>` sets
    the target for the rest of the batch. A single command per process would lose
    the selection, which matters on the hub, where two dongles are present and
    each role must act on its own one. With timeout set, the session stays open
    that many seconds (used for scanning); otherwise it exits at end of input.
    """
    argv = ["bluetoothctl"]
    if timeout is not None:
        argv += ["--timeout", str(int(timeout))]
    script = "".join(command + "\n" for command in commands)
    completed = subprocess.run(
        argv, input=script, capture_output=True, text=True, check=False
    )
    return completed.stdout


def _check_select(output: str) -> str:
    """Raise if a bluetoothctl session could not select its controller.

    bluetoothctl prints "Controller <id> not available" when a `select` names a
    controller it cannot use (an hciN index, or an address that is not present),
    then silently stays on the index-ordered default. Acting on that would drive
    the wrong dongle, so any session whose result must reflect the selected
    adapter is passed through here first. The message names no address.
    """
    if "not available" in output:
        raise AdapterSelectFailed(
            "bluetoothctl could not select the Bluetooth adapter"
        )
    return output


def _spawn_agent(controller: str) -> "subprocess.Popen":
    """Start a bt-agent that auto-accepts just-works pairings."""
    return subprocess.Popen(["bt-agent", "-c", "NoInputNoOutput"])


def _stop_agent(proc: "Optional[subprocess.Popen]") -> None:
    """Stop the pairing agent if it is still running."""
    if proc is None:
        return
    if proc.poll() is None:
        proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


def _paired_devices(output: str) -> dict[str, str]:
    """Map address to name from `paired-devices` output ("Device <mac> <name>")."""
    devices: dict[str, str] = {}
    for line in output.splitlines():
        parts = line.split(maxsplit=2)
        if len(parts) >= 2 and parts[0] == "Device":
            address = parts[1]
            devices[address] = parts[2] if len(parts) >= 3 else address
    return devices


def _snapshot_paired(controller: str, session: Session) -> dict[str, str]:
    """The devices paired on controller right now, by address."""
    return _paired_devices(session([f"select {controller}", "paired-devices"]))


def _trust_new(controller: str, session: Session, before: dict[str, str]) -> None:
    """Trust only devices that paired after the `before` snapshot was taken.

    Opening the window must not hand trust to devices that were already paired;
    only the ones a person added during this window are trusted, so they
    reconnect on their own. Each newly trusted device is named on stdout.
    """
    after = _snapshot_paired(controller, session)
    new = [address for address in after if address not in before]
    if not new:
        return
    for address in new:
        print(f"Trusting newly paired device: {after[address]} [{address}]")
    session([f"select {controller}", *(f"trust {address}" for address in new)])


def run_pairing_window(
    controller: str,
    *,
    duration: float = PAIRING_WINDOW_SECONDS,
    session: Session = _run_session,
    spawn_agent: SpawnAgent = _spawn_agent,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    poll_interval: float = POLL_INTERVAL,
) -> None:
    """Open the inbound pairing window on controller, then always close it.

    Powers the controller, starts the pairing agent, and makes the controller
    discoverable and pairable for duration seconds. bluez's own discoverable and
    pairable timeouts are set to the window length, so the adapter closes itself
    even if this process is killed before cleanup runs: the open state can never
    outlive the window. The window is driven by the clock, so a pairing agent
    that exits early does not shorten it. When the window ends for any reason
    (timeout, agent death, or an interrupt during the wait), the controller is
    made non-discoverable and non-pairable again, the agent is stopped, and only
    devices that paired during the window are trusted.
    """
    window = int(duration)
    proc: "Optional[subprocess.Popen]" = None

    # Snapshot the already-paired devices before the try, so a failure here
    # aborts before the window opens and never falls through to the finally. If
    # we cannot read the paired set, we also cannot tell new devices from old
    # ones, so trusting anything would be trusting blind.
    before = _snapshot_paired(controller, session)

    try:
        _check_select(
            session(
                [
                    f"select {controller}",
                    "power on",
                    f"discoverable-timeout {window}",
                    f"pairable-timeout {window}",
                    "pairable on",
                    "discoverable on",
                ]
            )
        )
        proc = spawn_agent(controller)

        deadline = monotonic() + duration
        while True:
            # Read the remaining time once: the while-check and the sleep
            # argument must not be two separate clock reads, or the deadline
            # could cross between them and hand sleep a negative duration.
            remaining = deadline - monotonic()
            if remaining <= 0:
                break
            sleep(min(poll_interval, remaining))
    finally:
        session([f"select {controller}", "discoverable off", "pairable off"])
        _stop_agent(proc)
        _trust_new(controller, session, before)


def write_speaker(path: Path, name: str, mac: str) -> None:
    """Record speaker `name`'s address as mac in the local.yml at path.

    Uses a round-tripping YAML load/dump so the file's hand-written comments and
    every other key survive. The `speakers` mapping (and its entry for name) is
    created if missing, so this works on a freshly written local.yml too.

    The write is atomic: the new document is staged in a temporary file in the
    same directory, flushed to disk, then renamed over path. local.yml is the
    household's only copy of its addresses, so a crash mid-write must never
    truncate or empty it; it either stays as it was or becomes the complete new
    version. The original file's mode is preserved.
    """
    yaml = YAML()
    yaml.preserve_quotes = True
    data = yaml.load(path.read_text())
    if data is None:
        data = CommentedMap()
    speakers = data.get("speakers")
    if speakers is None:
        speakers = CommentedMap()
        data["speakers"] = speakers
    speakers[name] = mac

    mode = stat.S_IMODE(path.stat().st_mode)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            yaml.dump(data, handle)
            handle.flush()
            os.fsync(handle.fileno())
            os.fchmod(handle.fileno(), mode)
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _load_local(path: Path) -> dict:
    """Load local.yml as a plain mapping (empty when the file has no keys)."""
    data = YAML().load(path.read_text())
    return data if data is not None else {}


def _require_adapter(mac: Optional[str], key: str, prog: str) -> Optional[str]:
    """Return mac if it names a present adapter, else print why and return None."""
    if not mac:
        print(
            f"{prog}: {key} is not set in {LOCAL_SETTINGS}; add it and re-run",
            file=sys.stderr,
        )
        return None
    try:
        resolve_adapter(mac)
    except AdapterNotFound as exc:
        print(f"{prog}: {exc}", file=sys.stderr)
        return None
    return mac


def pair_phone(*, settings: Path = LOCAL_SETTINGS) -> int:
    """`snaphouse pair phone`: open the inbound pairing window on the hub."""
    prog = "snaphouse pair phone"
    adapter = _require_adapter(
        _load_local(settings).get("bt_receive_adapter"), "bt_receive_adapter", prog
    )
    if adapter is None:
        return 1
    print(
        f"Pairing window open for {int(PAIRING_WINDOW_SECONDS)} s. "
        "Put the phone into Bluetooth pairing mode now."
    )
    try:
        run_pairing_window(adapter)
    except AdapterSelectFailed as exc:
        # The window aborted before it opened; run_pairing_window's cleanup has
        # already closed the adapter on the intended controller.
        print(f"{prog}: {exc}", file=sys.stderr)
        return 1
    print("Pairing window closed. The adapter is no longer discoverable.")
    return 0


def _named_devices(output: str) -> list[tuple[str, str]]:
    """The (address, name) of discovered devices that advertise a name."""
    found: list[tuple[str, str]] = []
    for line in output.splitlines():
        parts = line.split(maxsplit=2)
        if len(parts) < 3 or parts[0] != "Device":
            continue
        address, name = parts[1], parts[2]
        # bluetoothctl prints the address again as the "name" for unnamed
        # devices; skip those so the operator only picks from named speakers.
        if name.replace(":", "").upper() == address.replace(":", "").upper():
            continue
        found.append((address, name))
    return found


def _prompt_choice(devices: list[tuple[str, str]]) -> Optional[tuple[str, str]]:
    """Show the found devices and read the operator's pick (1-based)."""
    for index, (address, name) in enumerate(devices, start=1):
        print(f"  {index}) {name} [{address}]")
    raw = input("Which one is the speaker? Enter a number (or blank to cancel): ")
    raw = raw.strip()
    if not raw.isdigit():
        return None
    choice = int(raw)
    if not 1 <= choice <= len(devices):
        return None
    return devices[choice - 1]


def pair_speaker(
    name: str,
    *,
    settings: Path = LOCAL_SETTINGS,
    session: Session = _run_session,
    choose: Callable[[list[tuple[str, str]]], Optional[tuple[str, str]]] = _prompt_choice,
) -> int:
    """`snaphouse pair speaker {vizio|jbl}`: scan, pick, pair, and record."""
    prog = "snaphouse pair speaker"
    if name not in SPEAKER_NAMES:
        print(
            f"{prog}: unknown speaker {name!r}; choose one of "
            f"{', '.join(SPEAKER_NAMES)}",
            file=sys.stderr,
        )
        return 2

    adapter = _require_adapter(
        _load_local(settings).get("bt_send_adapter"), "bt_send_adapter", prog
    )
    if adapter is None:
        return 1

    print(f"Scanning {int(SCAN_SECONDS)} s. Put the {name} speaker into pairing mode now.")
    try:
        # One timed session scans on the send adapter; discovered devices stay in
        # bluez's cache after it ends, so the next session can list them. Both
        # sessions select by address and must have actually selected it, or the
        # scan and listing would reflect the wrong (default) controller.
        _check_select(
            session([f"select {adapter}", "power on", "scan on"], timeout=SCAN_SECONDS)
        )
        devices = _named_devices(
            _check_select(session([f"select {adapter}", "devices"]))
        )
    except AdapterSelectFailed as exc:
        print(f"{prog}: {exc}", file=sys.stderr)
        return 1
    if not devices:
        print(
            f"{prog}: found no named devices; make sure the speaker is in pairing mode",
            file=sys.stderr,
        )
        return 1

    target = choose(devices)
    if target is None:
        print(f"{prog}: nothing selected", file=sys.stderr)
        return 1

    address, device_name = target
    try:
        _check_select(
            session(
                [
                    f"select {adapter}",
                    f"pair {address}",
                    f"trust {address}",
                    f"connect {address}",
                ]
            )
        )
    except AdapterSelectFailed as exc:
        print(f"{prog}: {exc}", file=sys.stderr)
        return 1
    write_speaker(settings, name, address)
    print(f"Paired {device_name} as '{name}' and recorded it in {settings}.")
    # The Bluetooth output ALSA alias reads the speaker address at apply time, so
    # it only picks up this new pairing on the next apply. Prompt for it.
    print("Now run 'snaphouse update --force' so this room's audio output uses it.")
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    parser = argparse.ArgumentParser(
        prog="snaphouse pair",
        description="Pair a phone to the hub, or pair a Bluetooth speaker.",
    )
    targets = parser.add_subparsers(dest="target", required=True)
    targets.add_parser("phone", help="open an inbound pairing window on the hub")
    speaker = targets.add_parser("speaker", help="pair and record a Bluetooth speaker")
    speaker.add_argument("name", choices=SPEAKER_NAMES, help="which speaker to pair")

    args = parser.parse_args(argv)
    if args.target == "phone":
        return pair_phone()
    return pair_speaker(args.name)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
