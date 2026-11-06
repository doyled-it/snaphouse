"""Resolve a Bluetooth adapter by its address.

The hciN index the kernel gives a controller is assigned at probe time and can
swap across reboots, so nothing in snaphouse addresses an adapter by index.
Instead each adapter is named in local.yml by its own address (the stable
identifier), and this module maps that address to the current hciN by reading
/sys/class/bluetooth/hci*/address.

A missing adapter raises AdapterNotFound, whose message names only the last two
octets of the address. The address identifies the household, so a failing verify
check or a stray log line must not carry the whole thing.

Stdlib only: this runs from the plain system interpreter inside the verify
check, before (and independently of) the on-device venv.
"""

from __future__ import annotations

from pathlib import Path

DEFAULT_SYS_ROOT = Path("/sys/class/bluetooth")


class AdapterNotFound(Exception):
    """No Bluetooth controller with the requested address is present."""


def _redacted(mac: str) -> str:
    """The address reduced to its last two octets, for a safe error message."""
    octets = [part for part in mac.split(":") if part]
    tail = ":".join(octets[-2:]) if octets else mac
    return tail.upper()


def resolve_adapter(mac: str, sys_root: Path = DEFAULT_SYS_ROOT) -> str:
    """Return the hciN whose address is mac, read from sys_root.

    Matching is case-insensitive. Entries without a readable address file are
    skipped (a controller can appear mid-enumeration). Raises AdapterNotFound,
    naming only the last two octets, when no controller matches.
    """
    target = mac.strip().lower()
    try:
        entries = sorted(sys_root.iterdir())
    except (FileNotFoundError, NotADirectoryError):
        entries = []

    for entry in entries:
        try:
            address = (entry / "address").read_text().strip().lower()
        except (FileNotFoundError, NotADirectoryError, OSError):
            continue
        if address == target:
            return entry.name

    raise AdapterNotFound(
        f"no Bluetooth adapter ending in :{_redacted(mac)} is present"
    )
