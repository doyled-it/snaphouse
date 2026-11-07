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

from snaphouse.adapters import (
    AdapterNotFound,
    _default_tool_runner,
    resolve_adapter,
)


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
    # No BlueZ tool output either, so resolution has nowhere else to look.
    with pytest.raises(AdapterNotFound) as excinfo:
        resolve_adapter(missing, sys_root=root, tool_runner=lambda: None)

    message = str(excinfo.value)
    # The last two octets identify which adapter is missing without leaking the
    # whole address.
    assert "EE:FF" in message
    assert missing not in message
    for leaked in ("AA", "BB", "CC", "DD"):
        assert leaked not in message


# ---------------------------------------------------------------------------
# BlueZ-tool fallback.
#
# On some kernels (seen with CSR8510 dongles on Linux 6.1) the controller is
# present and up, but /sys/class/bluetooth/hciN/address is never exposed, so
# the sysfs lookup finds nothing. Resolution then falls back to a BlueZ tool.
# The fixtures below are the real output shapes of btmgmt and hciconfig, with
# placeholder addresses (a real household address never lives in the repo).
# ---------------------------------------------------------------------------

RECEIVE = "00:11:22:33:44:55"
SEND = "AA:BB:CC:DD:EE:FF"

BTMGMT_INFO = (
    "Index list with 2 items\n"
    "hci1:\tPrimary controller\n"
    f"\taddr {SEND} version 6 manufacturer 10 class 0x0c0104\n"
    "\tsupported settings: powered connectable discoverable bondable ssp br/edr le \n"
    "\tcurrent settings: powered ssp br/edr le secure-conn \n"
    "\tname testhub\n"
    "\tshort name \n"
    "hci0:\tPrimary controller\n"
    f"\taddr {RECEIVE} version 6 manufacturer 10 class 0x0c0104\n"
    "\tsupported settings: powered connectable discoverable bondable ssp br/edr le \n"
    "\tcurrent settings: powered ssp br/edr le secure-conn \n"
    "\tname testhub #1\n"
    "\tshort name \n"
)

HCICONFIG = (
    "hci1:\tType: Primary  Bus: USB\n"
    f"\tBD Address: {SEND}  ACL MTU: 310:10  SCO MTU: 64:8\n"
    "\tUP RUNNING \n"
    "\tRX bytes:1593 acl:0 sco:0 events:87 errors:0\n"
    "\tTX bytes:4234 acl:0 sco:0 commands:87 errors:0\n"
    "\n"
    "hci0:\tType: Primary  Bus: USB\n"
    f"\tBD Address: {RECEIVE}  ACL MTU: 310:10  SCO MTU: 64:8\n"
    "\tUP RUNNING \n"
    "\tRX bytes:1593 acl:0 sco:0 events:87 errors:0\n"
    "\tTX bytes:4234 acl:0 sco:0 commands:87 errors:0\n"
)


def _boom():
    raise AssertionError("tool fallback must not run when sysfs resolves the address")


def test_sysfs_match_wins_without_consulting_a_tool(tmp_path):
    root = tmp_path / "bluetooth"
    root.mkdir()
    write_tree(root, {"hci0": RECEIVE, "hci1": SEND})

    # The sysfs attribute is present, so the BlueZ-tool fallback must not run.
    assert resolve_adapter(RECEIVE, sys_root=root, tool_runner=_boom) == "hci0"
    assert resolve_adapter(SEND, sys_root=root, tool_runner=_boom) == "hci1"


def test_falls_back_to_btmgmt_when_sysfs_omits_the_address(tmp_path):
    # The controllers exist under sysfs but carry no address file (the
    # CSR8510-on-6.1 case), so the sysfs lookup matches nothing.
    root = tmp_path / "bluetooth"
    root.mkdir()
    (root / "hci0").mkdir()
    (root / "hci1").mkdir()

    assert resolve_adapter(RECEIVE, sys_root=root, tool_runner=lambda: BTMGMT_INFO) == "hci0"
    assert resolve_adapter(SEND, sys_root=root, tool_runner=lambda: BTMGMT_INFO) == "hci1"


def test_falls_back_to_hciconfig_output(tmp_path):
    root = tmp_path / "bluetooth"
    root.mkdir()  # no entries at all: sysfs offers nothing

    assert resolve_adapter(RECEIVE, sys_root=root, tool_runner=lambda: HCICONFIG) == "hci0"
    assert resolve_adapter(SEND, sys_root=root, tool_runner=lambda: HCICONFIG) == "hci1"


def test_raises_redacted_when_neither_sysfs_nor_tool_has_it(tmp_path):
    root = tmp_path / "bluetooth"
    root.mkdir()

    with pytest.raises(AdapterNotFound) as excinfo:
        resolve_adapter(SEND, sys_root=root, tool_runner=lambda: None)

    message = str(excinfo.value)
    # Only the last two octets, so a failing check never leaks the address.
    assert "EE:FF" in message
    assert SEND not in message
    for leaked in ("AA", "BB", "CC", "DD"):
        assert leaked not in message


def test_default_tool_runner_prefers_btmgmt_then_falls_back_to_hciconfig():
    calls = []

    class _Proc:
        def __init__(self, stdout):
            self.returncode = 0
            self.stdout = stdout

    def run(argv, **kwargs):
        calls.append(argv[0])
        return _Proc(BTMGMT_INFO if "btmgmt" in argv[0] else HCICONFIG)

    # Both tools present: btmgmt is used and hciconfig is never run.
    both = {"btmgmt": "/usr/bin/btmgmt", "hciconfig": "/usr/bin/hciconfig"}
    assert _default_tool_runner(which=both.get, run=run) == BTMGMT_INFO
    assert calls == ["/usr/bin/btmgmt"]

    # btmgmt missing: the runner skips it and uses hciconfig instead.
    calls.clear()
    only_hciconfig = {"hciconfig": "/usr/bin/hciconfig"}
    assert _default_tool_runner(which=only_hciconfig.get, run=run) == HCICONFIG
    assert calls == ["/usr/bin/hciconfig"]

    # Neither tool present: no output, and nothing is run.
    calls.clear()
    assert _default_tool_runner(which=lambda _name: None, run=run) is None
    assert calls == []
