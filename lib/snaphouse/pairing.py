"""snaphouse pair: pair a phone to the hub, or pair a Bluetooth speaker.

Pairing needs a person to put a device into pairing mode, so it stays a manual
command. There are two directions:

  - `snaphouse pair phone` (hub): open a short window on the receive adapter
    during which the hub is discoverable and pairable. Pairing is supervised: an
    agent the CLI drives (a bluetoothctl agent with the DisplayYesNo capability)
    shows the 6-digit passkey and asks the person at the keyboard to confirm it
    matches the phone, declining if nobody answers. Android uses this numeric
    comparison, so the old always-accept NoInputNoOutput agent failed it. The
    window always closes the adapter again, even if the agent dies early or the
    operator hits Ctrl-C: the hub must never be left discoverable. Devices paired
    during the window are trusted so they reconnect on their own.
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
import re
import select
import stat
import subprocess
import sys
import tempfile
import threading
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


# bluetoothctl colours its output; the escape sequences carry no information and
# only break token matching, so every line is stripped before it is parsed.
_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def _strip_ansi(text: str) -> str:
    return _ANSI.sub("", text)


# The Bluetooth services the inbound agent authorizes for a phone: A2DP (audio
# sink and source) and AVRCP (remote control), the 0x110a..0x110f assigned
# numbers. The phone plays audio over these; anything else it asks for is
# declined, so a paired phone cannot reach other profiles on the hub.
_ALLOWED_SERVICE_UUIDS = frozenset(
    f"0000110{suffix}-0000-1000-8000-00805f9b34fb" for suffix in "abcdef"
)

# The DisplayYesNo capability makes BlueZ ask us to confirm a passkey both
# devices display (numeric comparison), which is how Android pairs. The old
# NoInputNoOutput agent could not answer that and Android pairing failed.
AGENT_CAPABILITY = "DisplayYesNo"

# How long the confirmation prompt waits for the operator before declining.
CONFIRM_TIMEOUT = 30.0

# bluetoothctl's agent prompts, after ANSI is stripped. The passkey is shown
# zero-padded to six digits; the service is a full 128-bit UUID.
_CONFIRM_PROMPT = re.compile(r"Confirm passkey (\d+)")
_SERVICE_PROMPT = re.compile(
    r"Authorize service ([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12})"
)


def _service_allowed(uuid: str) -> bool:
    """True if uuid is an A2DP or AVRCP service the inbound agent authorizes."""
    return uuid.lower() in _ALLOWED_SERVICE_UUIDS


def _parse_confirmation_prompt(line: str) -> Optional[int]:
    """The passkey from a bluetoothctl "Confirm passkey" prompt, or None."""
    match = _CONFIRM_PROMPT.search(_strip_ansi(line))
    return int(match.group(1)) if match else None


def _parse_service_prompt(line: str) -> Optional[str]:
    """The UUID from a bluetoothctl "Authorize service" prompt, or None."""
    match = _SERVICE_PROMPT.search(_strip_ansi(line))
    return match.group(1) if match else None


def _handle_agent_line(
    line: str, *, write: Callable[[str], None], confirm: Callable[[int], bool]
) -> None:
    """Answer one line of bluetoothctl agent output.

    A passkey confirmation is handed to `confirm`, which shows the code and reads
    the operator's yes or no. A service authorization is answered from the audio
    allow-list. Any other line is ignored. The answer ("yes" or "no") is written
    back with a trailing newline, as bluetoothctl's prompt expects.
    """
    passkey = _parse_confirmation_prompt(line)
    if passkey is not None:
        write("yes\n" if confirm(passkey) else "no\n")
        return
    uuid = _parse_service_prompt(line)
    if uuid is not None:
        write("yes\n" if _service_allowed(uuid) else "no\n")


def _ask_operator(
    passkey: int, *, timeout: float = CONFIRM_TIMEOUT, stream=None
) -> bool:  # pragma: no cover - interactive, exercised on the hub
    """Show the passkey and read a yes/no from the keyboard; default no.

    Pairing is supervised: the operator checks that the phone shows the same code
    before accepting. If nobody answers within the timeout, the pairing is
    declined, so a stray pairing attempt cannot be accepted by inaction.
    """
    stream = sys.stdin if stream is None else stream
    print(f"\nThe phone should show this code: {passkey:06d}")
    print(
        f"Does it match? [y/N] (no answer within {int(timeout)} s declines) ",
        end="",
        flush=True,
    )
    ready, _, _ = select.select([stream], [], [], timeout)
    if not ready:
        print("\nNo confirmation; declining this pairing.")
        return False
    return stream.readline().strip().lower() in ("y", "yes")


def _agent_loop(
    proc: "subprocess.Popen", *, confirm: Callable[[int], bool]
) -> None:  # pragma: no cover - drives a live bluetoothctl
    """Answer agent prompts from proc until its output ends.

    bluetoothctl's agent questions end with "(yes/no):" and no newline (they are
    prompts waiting for input), so reading whole lines would block on the newline
    that never comes. Output is read a character at a time and dispatched on
    either a newline or that prompt suffix, so a question is answered as soon as
    it is asked.
    """
    def write(text: str) -> None:
        assert proc.stdin is not None
        proc.stdin.write(text)
        proc.stdin.flush()

    assert proc.stdout is not None
    buffer = ""
    while True:
        char = proc.stdout.read(1)
        if char == "":
            break
        buffer += char
        if char == "\n" or _strip_ansi(buffer).rstrip().endswith("(yes/no):"):
            _handle_agent_line(buffer, write=write, confirm=confirm)
            buffer = ""


def _spawn_agent(controller: str) -> "subprocess.Popen":  # pragma: no cover - needs BlueZ
    """Start a supervised pairing agent the CLI controls.

    An interactive bluetoothctl registers the DisplayYesNo agent as the default,
    and a reader thread answers its prompts: the operator confirms the passkey
    (numeric comparison) and the agent authorizes the phone's A2DP and AVRCP
    services. The process is the window's agent handle; terminating it ends the
    reader thread with its output.
    """
    proc = subprocess.Popen(
        ["bluetoothctl"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    assert proc.stdin is not None
    proc.stdin.write(f"agent {AGENT_CAPABILITY}\n")
    proc.stdin.write("default-agent\n")
    proc.stdin.flush()
    threading.Thread(
        target=_agent_loop, args=(proc,), kwargs={"confirm": _ask_operator}, daemon=True
    ).start()
    return proc


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


_DEVICE_LINE = re.compile(
    r"Device\s+((?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2})\s+(.*)"
)


def _named_devices(output: str) -> list[tuple[str, str]]:
    """The (address, name) of discovered devices that advertise a name.

    bluetoothctl colours its output and may prefix a device line with a status
    tag ([NEW], [CHG]) or a prompt, so the "Device <addr> <name>" is matched
    anywhere on the ANSI-stripped line rather than as the first token (issue #40:
    the colour broke a first-token check and the scan found "no named devices").
    An unnamed device is listed with the address repeated as its name, in colon
    or dash form; those are dropped so the operator only picks from real names.
    Each address is reported once, in the order first seen.
    """
    found: list[tuple[str, str]] = []
    seen: set[str] = set()
    for line in output.splitlines():
        match = _DEVICE_LINE.search(_strip_ansi(line))
        if match is None:
            continue
        address, name = match.group(1), match.group(2).strip()
        if not name:
            continue
        # Normalize away both separators so the address-as-name forms
        # (AA:BB:.. and AA-BB-..) both compare equal to the address.
        normalized_name = name.replace(":", "").replace("-", "").upper()
        if normalized_name == address.replace(":", "").upper():
            continue
        if address in seen:
            continue
        seen.add(address)
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
