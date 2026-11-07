"""Resolve a Bluetooth adapter by its address.

The hciN index the kernel gives a controller is assigned at probe time and can
swap across reboots, so nothing in snaphouse addresses an adapter by index.
Instead each adapter is named in local.yml by its own address (the stable
identifier), and this module maps that address to the current hciN.

The address comes from /sys/class/bluetooth/hci*/address when the kernel exposes
it. Some controllers do not expose that attribute (seen with CSR8510 dongles on
Linux 6.1: the adapter is present and up, but the sysfs address file never
appears), so resolution then falls back to a BlueZ tool that reports the
mapping: btmgmt first (stable, machine-readable), hciconfig second, each skipped
if it is not installed. The sysfs path is always tried first, so the common case
never shells out.

A missing adapter raises AdapterNotFound, whose message names only the last two
octets of the address. The address identifies the household, so a failing verify
check or a stray log line must not carry the whole thing.

Stdlib only: this runs from the plain system interpreter inside the verify
check, before (and independently of) the on-device venv.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from typing import Callable, Optional

DEFAULT_SYS_ROOT = Path("/sys/class/bluetooth")

# How long to wait on a BlueZ tool before giving up on it.
TOOL_TIMEOUT = 10.0

# BlueZ tools tried, in order, when sysfs has no address. btmgmt is preferred
# for its stable machine-readable output; hciconfig is the fallback. The
# bluetooth role installs both. A tool that is not present is skipped.
_BLUEZ_TOOLS: tuple[tuple[str, ...], ...] = (("btmgmt", "info"), ("hciconfig",))

# An address is six colon-separated hex octets.
_MAC = r"[0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5}"
# A controller header line in either tool's output: "hciN:\t...".
_HCI_HEADER = re.compile(r"^(hci\d+):")
# The address line under a header, from btmgmt ("addr XX:..") or hciconfig
# ("BD Address: XX:.."). Matched on the stripped line.
_ADDR_LINE = re.compile(rf"^(?:addr\s+|BD Address:\s*)({_MAC})")


class AdapterNotFound(Exception):
    """No Bluetooth controller with the requested address is present."""


def _redacted(mac: str) -> str:
    """The address reduced to its last two octets, for a safe error message."""
    octets = [part for part in mac.split(":") if part]
    tail = ":".join(octets[-2:]) if octets else mac
    return tail.upper()


def _resolve_via_sysfs(target: str, sys_root: Path) -> Optional[str]:
    """Return the hciN under sys_root whose address file equals target, or None.

    Matching is case-insensitive (target is already lowered). Entries without a
    readable address file are skipped (a controller can appear mid-enumeration,
    or never expose the attribute at all).
    """
    try:
        entries = sorted(sys_root.iterdir())
    except (FileNotFoundError, NotADirectoryError):
        return None

    for entry in entries:
        try:
            address = (entry / "address").read_text().strip().lower()
        except (FileNotFoundError, NotADirectoryError, OSError):
            continue
        if address == target:
            return entry.name
    return None


def _parse_tool_addresses(text: str) -> dict[str, str]:
    """Map address (lowercased) to hciN from btmgmt or hciconfig output.

    A line beginning "hciN:" names the current controller; the first address
    line that follows it (btmgmt's "addr ..." or hciconfig's "BD Address: ...")
    is that controller's address. Only the first address per controller is used.
    """
    mapping: dict[str, str] = {}
    current: Optional[str] = None
    for line in text.splitlines():
        header = _HCI_HEADER.match(line)
        if header:
            current = header.group(1)
            continue
        if current is None:
            continue
        addr = _ADDR_LINE.match(line.strip())
        if addr:
            mapping.setdefault(addr.group(1).lower(), current)
            # The address for this controller is recorded; ignore the rest of
            # its block until the next header.
            current = None
    return mapping


def _default_tool_runner(
    which: Callable[[str], Optional[str]] = shutil.which,
    run: Callable[..., "subprocess.CompletedProcess"] = subprocess.run,
) -> Optional[str]:
    """Return the output of the first available BlueZ tool, or None.

    Tools are tried in _BLUEZ_TOOLS order; one that is not installed, fails to
    run, or exits non-zero is skipped in favour of the next.
    """
    for argv in _BLUEZ_TOOLS:
        exe = which(argv[0])
        if exe is None:
            continue
        try:
            proc = run(
                [exe, *argv[1:]],
                capture_output=True,
                text=True,
                timeout=TOOL_TIMEOUT,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if proc.returncode == 0 and proc.stdout and proc.stdout.strip():
            return proc.stdout
    return None


def resolve_adapter(
    mac: str,
    sys_root: Path = DEFAULT_SYS_ROOT,
    *,
    tool_runner: Optional[Callable[[], Optional[str]]] = None,
) -> str:
    """Return the hciN whose address is mac.

    Tries /sys/class/bluetooth first, then a BlueZ tool when sysfs has no match
    (for controllers that never expose the sysfs address attribute). Matching is
    case-insensitive. Raises AdapterNotFound, naming only the last two octets,
    when no controller matches.
    """
    target = mac.strip().lower()

    hci = _resolve_via_sysfs(target, sys_root)
    if hci is not None:
        return hci

    runner = _default_tool_runner if tool_runner is None else tool_runner
    text = runner()
    if text:
        hci = _parse_tool_addresses(text).get(target)
        if hci is not None:
            return hci

    raise AdapterNotFound(
        f"no Bluetooth adapter ending in :{_redacted(mac)} is present"
    )
