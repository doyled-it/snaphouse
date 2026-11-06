"""Tests for snaphouse.adapters, which resolves a Bluetooth adapter by address.

The hciN index a controller gets is assigned at probe time and can swap across
reboots, so the system always resolves an adapter by its own address (the stable
identifier written in local.yml), never by index. These tests drive that against
a fake /sys/class/bluetooth tree so no real hardware is needed, and they pin the
redaction rule: an unknown address is reported by its last two octets only, so a
failing check never leaks the household's addresses into logs.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from snaphouse.adapters import AdapterNotFound, resolve_adapter


def write_tree(root: Path, mapping: dict[str, str]) -> None:
    """Write a fake /sys/class/bluetooth: one hciN/address file per entry."""
    for name, address in mapping.items():
        entry = root / name
        entry.mkdir(exist_ok=True)
        (entry / "address").write_text(address + "\n")


def test_resolves_by_address_not_by_index(tmp_path):
    root = tmp_path / "bluetooth"
    root.mkdir()
    receive = "00:11:22:33:44:55"
    send = "AA:BB:CC:DD:EE:FF"
    write_tree(root, {"hci0": receive, "hci1": send})

    assert resolve_adapter(receive, sys_root=root) == "hci0"
    assert resolve_adapter(send, sys_root=root) == "hci1"

    # A reboot renumbered the two dongles: the same addresses now sit on the
    # other indices. Resolution must follow the address, not the index.
    write_tree(root, {"hci0": send, "hci1": receive})

    assert resolve_adapter(receive, sys_root=root) == "hci1"
    assert resolve_adapter(send, sys_root=root) == "hci0"


def test_unknown_address_raises_with_only_the_last_two_octets(tmp_path):
    root = tmp_path / "bluetooth"
    root.mkdir()
    write_tree(root, {"hci0": "00:11:22:33:44:55"})

    missing = "AA:BB:CC:DD:EE:FF"
    with pytest.raises(AdapterNotFound) as excinfo:
        resolve_adapter(missing, sys_root=root)

    message = str(excinfo.value)
    # The last two octets identify which adapter is missing without leaking the
    # whole address.
    assert "EE:FF" in message
    assert missing not in message
    for leaked in ("AA", "BB", "CC", "DD"):
        assert leaked not in message
