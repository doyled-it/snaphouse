"""Tests for the Bluetooth devices list (lib/snaphouse/pairdevices.py).

The list answers "which phones are paired to the hub's receive adapter, are they
connected, and when were they last". bluetoothctl is injected as a fake session
and the clock is injected, so nothing touches BlueZ or the real state directory.
Addresses here are placeholders; the point of several tests is that no address
ever leaves the module (ids are keyed hashes, logs carry none).
"""

from __future__ import annotations

import json
import logging
import re
import stat
from datetime import datetime, timedelta, timezone

import pytest

from snaphouse import pairdevices

CONTROLLER = "AA:BB:CC:DD:EE:FF"
PIXEL = "11:22:33:44:55:01"
IPHONE = "11:22:33:44:55:02"
NAMELESS = "11:22:33:44:55:03"
MAC = re.compile(r"(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}")


class FakeBluez:
    """A scripted bluetoothctl: paired devices with a connected flag, and a log."""

    def __init__(self, devices):
        # address -> {"name": str, "connected": bool}
        self.devices = {a: dict(d) for a, d in devices.items()}
        self.sessions: list[list[str]] = []
        self.fail = False

    def __call__(self, commands, timeout=None):
        self.sessions.append(list(commands))
        if self.fail:
            return "Controller 00:00:00:00:00:00 not available\n"
        out = [f"Controller {CONTROLLER} (public)\n"]
        for command in commands:
            if command == "devices Paired":
                for address, dev in self.devices.items():
                    out.append(f"\x1b[0;94mDevice\x1b[0m {address} {dev['name'] or address}\n")
            elif command.startswith("info "):
                address = command.split()[1]
                dev = self.devices.get(address)
                if dev is None:
                    out.append(f"Device {address} not available\n")
                    continue
                out.append(f"Device {address} (public)\n")
                out.append(f"\tName: {dev['name']}\n\tPaired: yes\n")
                out.append(f"\tConnected: {'yes' if dev['connected'] else 'no'}\n")
            elif command.startswith("disconnect "):
                self.devices[command.split()[1]]["connected"] = False
            elif command.startswith("remove "):
                self.devices.pop(command.split()[1], None)
        return "".join(out)

    def commands(self):
        return [c for s in self.sessions for c in s]


class Clock:
    def __init__(self):
        self.now = datetime(2026, 11, 12, 20, 0, 0, tzinfo=timezone.utc)

    def __call__(self):
        return self.now

    def advance(self, **kw):
        self.now += timedelta(**kw)


@pytest.fixture
def bluez():
    return FakeBluez(
        {
            PIXEL: {"name": "Pixel 7", "connected": True},
            IPHONE: {"name": "Alex's iPhone", "connected": False},
        }
    )


def make_book(bluez, tmp_path, clock=None, **kw):
    return pairdevices.DeviceBook(
        CONTROLLER,
        state_dir=tmp_path,
        session=bluez,
        wall_now=clock or Clock(),
        **kw,
    )


def by_name(devices):
    return {d["name"]: d for d in devices}


def test_lists_paired_devices_connected_first_then_by_name(bluez, tmp_path):
    bluez.devices[NAMELESS] = {"name": "Zed Buds", "connected": True}
    devices = make_book(bluez, tmp_path).devices()
    assert [d["name"] for d in devices] == ["Pixel 7", "Zed Buds", "Alex's iPhone"]


def test_each_device_has_exactly_the_contract_fields(bluez, tmp_path):
    devices = make_book(bluez, tmp_path).devices()
    for device in devices:
        assert set(device) == {"id", "name", "connected", "last_connected"}
    assert by_name(devices)["Pixel 7"]["connected"] is True
    assert by_name(devices)["Alex's iPhone"]["connected"] is False


def test_the_listing_selects_the_receive_adapter(bluez, tmp_path):
    make_book(bluez, tmp_path).devices()
    assert all(s[0] == f"select {CONTROLLER}" for s in bluez.sessions)


def test_ids_are_opaque_stable_and_never_the_address(bluez, tmp_path):
    first = by_name(make_book(bluez, tmp_path).devices())
    second = by_name(make_book(bluez, tmp_path).devices())  # a fresh book, same state dir
    assert first["Pixel 7"]["id"] == second["Pixel 7"]["id"]
    assert first["Pixel 7"]["id"] != first["Alex's iPhone"]["id"]
    for device in first.values():
        assert re.fullmatch(r"[0-9a-f]{16,64}", device["id"])
        assert PIXEL.replace(":", "").lower() not in device["id"]
    blob = json.dumps(first)
    assert not MAC.search(blob)


def test_ids_differ_between_hosts_because_the_secret_is_per_host(bluez, tmp_path):
    a = by_name(make_book(bluez, tmp_path / "a").devices())
    b = by_name(make_book(bluez, tmp_path / "b").devices())
    assert a["Pixel 7"]["id"] != b["Pixel 7"]["id"]


def test_the_secret_and_state_are_root_only(bluez, tmp_path):
    make_book(bluez, tmp_path).devices()
    for path in tmp_path.iterdir():
        assert stat.S_IMODE(path.stat().st_mode) & 0o077 == 0, path.name
    assert stat.S_IMODE(tmp_path.stat().st_mode) & 0o077 == 0


def test_a_nameless_device_never_shows_its_address(bluez, tmp_path):
    bluez.devices[NAMELESS] = {"name": "", "connected": False}
    devices = make_book(bluez, tmp_path).devices()
    assert "Unnamed device" in [d["name"] for d in devices]
    assert not MAC.search(json.dumps(devices))


def test_last_connected_is_null_until_first_seen_connected(bluez, tmp_path):
    devices = by_name(make_book(bluez, tmp_path).devices())
    assert devices["Alex's iPhone"]["last_connected"] is None
    assert devices["Pixel 7"]["last_connected"] == "2026-11-12T20:00:00Z"


def test_last_connected_keeps_the_last_time_seen_connected(bluez, tmp_path):
    clock = Clock()
    book = make_book(bluez, tmp_path, clock)
    book.devices()
    clock.advance(minutes=5)
    bluez.devices[PIXEL]["connected"] = False
    devices = by_name(book.devices())
    assert devices["Pixel 7"]["connected"] is False
    assert devices["Pixel 7"]["last_connected"] == "2026-11-12T20:00:00Z"


def test_last_connected_persists_across_a_restart(bluez, tmp_path):
    clock = Clock()
    make_book(bluez, tmp_path, clock).devices()
    clock.advance(hours=1)
    bluez.devices[PIXEL]["connected"] = False
    again = by_name(make_book(bluez, tmp_path, clock).devices())
    assert again["Pixel 7"]["last_connected"] == "2026-11-12T20:00:00Z"
    stored = (tmp_path / "last-connected.json").read_text()
    assert not MAC.search(stored)


def test_sample_records_connection_without_a_request(bluez, tmp_path):
    clock = Clock()
    book = make_book(bluez, tmp_path, clock)
    book.sample()
    clock.advance(minutes=1)
    bluez.devices[PIXEL]["connected"] = False
    devices = by_name(book.devices())
    assert devices["Pixel 7"]["last_connected"] == "2026-11-12T20:00:00Z"


def test_a_device_no_longer_paired_is_dropped_from_the_persisted_times(bluez, tmp_path):
    book = make_book(bluez, tmp_path)
    book.devices()
    del bluez.devices[PIXEL]
    book.devices()
    stored = json.loads((tmp_path / "last-connected.json").read_text())
    assert len(stored) == 0


def test_a_device_named_not_available_still_lists(bluez, tmp_path):
    # Only the controller select line can mean the adapter is gone. A phone whose
    # name contains the phrase must not turn the whole list into a 503.
    bluez.devices[NAMELESS] = {"name": "not available", "connected": False}
    names = [d["name"] for d in make_book(bluez, tmp_path).devices()]
    assert "not available" in names


def test_a_device_gone_between_the_list_and_info_does_not_fail_the_listing(bluez, tmp_path):
    real = bluez.__call__

    def racing(commands, timeout=None):
        out = real(commands, timeout)
        if "devices Paired" in commands:
            bluez.devices.pop(IPHONE, None)  # removed before the info call
        return out

    book = pairdevices.DeviceBook(CONTROLLER, state_dir=tmp_path, session=racing, wall_now=Clock())
    assert [d["name"] for d in book.devices()] == ["Pixel 7", "Alex's iPhone"]


def test_a_failed_listing_raises_unavailable_and_keeps_the_stored_times(bluez, tmp_path):
    book = make_book(bluez, tmp_path)
    book.devices()
    bluez.fail = True
    with pytest.raises(pairdevices.Unavailable):
        book.devices()
    stored = json.loads((tmp_path / "last-connected.json").read_text())
    assert len(stored) == 1


def test_disconnect_drops_the_connection_and_keeps_the_pairing(bluez, tmp_path):
    book = make_book(bluez, tmp_path)
    pixel = by_name(book.devices())["Pixel 7"]
    updated = book.disconnect(pixel["id"])
    assert updated["name"] == "Pixel 7"
    assert updated["connected"] is False
    assert updated["last_connected"] == "2026-11-12T20:00:00Z"
    assert f"disconnect {PIXEL}" in bluez.commands()
    assert PIXEL in bluez.devices  # still paired


def test_disconnect_of_a_device_that_is_not_connected_conflicts(bluez, tmp_path):
    book = make_book(bluez, tmp_path)
    iphone = by_name(book.devices())["Alex's iPhone"]
    with pytest.raises(pairdevices.NotConnected):
        book.disconnect(iphone["id"])
    assert not any(c.startswith("disconnect ") for c in bluez.commands())


def test_disconnect_of_an_unknown_id_is_unknown(bluez, tmp_path):
    with pytest.raises(pairdevices.UnknownDevice):
        make_book(bluez, tmp_path).disconnect("0" * 32)


def test_forget_removes_the_pairing_and_its_stored_time(bluez, tmp_path):
    book = make_book(bluez, tmp_path)
    pixel = by_name(book.devices())["Pixel 7"]
    book.forget(pixel["id"])
    assert f"remove {PIXEL}" in bluez.commands()
    assert PIXEL not in bluez.devices
    assert "Pixel 7" not in by_name(book.devices())
    stored = json.loads((tmp_path / "last-connected.json").read_text())
    assert pixel["id"] not in stored


def test_forget_of_an_unknown_id_is_unknown_and_removes_nothing(bluez, tmp_path):
    book = make_book(bluez, tmp_path)
    book.devices()
    with pytest.raises(pairdevices.UnknownDevice):
        book.forget("f" * 32)
    assert not any(c.startswith("remove ") for c in bluez.commands())


def test_malformed_ids_are_rejected(bluez, tmp_path):
    book = make_book(bluez, tmp_path)
    for bad in (None, 5, "", "x" * 200, "not hex!", "a" * 16):
        with pytest.raises(pairdevices.BadId):
            book.forget(bad)


def test_nothing_the_book_logs_carries_an_address(bluez, tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    book = make_book(bluez, tmp_path)
    pixel = by_name(book.devices())["Pixel 7"]
    book.disconnect(pixel["id"])
    book.forget(pixel["id"])
    bluez.fail = True
    with pytest.raises(pairdevices.Unavailable):
        book.devices()
    assert not MAC.search(caplog.text)
    assert "Pixel 7" not in caplog.text


def test_the_device_listing_asks_bluez_for_devices_paired_and_never_paired_devices(bluez, tmp_path):
    # BlueZ 5.66 removed `paired-devices` (it prints nothing), so the listing must
    # send `devices Paired` (issue #136). Pin both sides: the new command is sent,
    # and the removed one is never sent again.
    make_book(bluez, tmp_path).devices()
    sent = [command for session in bluez.sessions for command in session]
    assert "devices Paired" in sent
    assert "paired-devices" not in sent
