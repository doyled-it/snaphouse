"""The Bluetooth verify check waits, bounded, for adapters to finish closing (#216)."""

from __future__ import annotations

from pathlib import Path

from snaphouse.btsettle import open_flags, settle

REPO = Path(__file__).resolve().parents[2]
VERIFY = REPO / "roles/bluetooth/files/verify.d/bluetooth.sh"

CLOSED = "Powered: yes\n\tDiscoverable: no\n\tPairable: no\n"
PAIRABLE = "Powered: yes\n\tDiscoverable: no\n\tPairable: yes\n"


class Clock:
    """A fake clock whose sleep advances time, so tests take no real time."""

    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


def run(show, addrs=("a",), **kw):
    clock = Clock()
    result = settle(addrs, show, clock.sleep, clock.monotonic, **kw)
    return result, clock


def test_never_pairable_passes_without_waiting():
    result, clock = run(lambda addr: CLOSED)
    assert result == {}
    assert clock.sleeps == []


def test_pairable_then_closed_within_the_window_passes():
    reads = iter([PAIRABLE, PAIRABLE, PAIRABLE, CLOSED])
    result, clock = run(lambda addr: next(reads))
    assert result == {}
    assert clock.now == 3.0


def test_pairable_for_the_whole_window_fails_and_stays_bounded():
    result, clock = run(lambda addr: PAIRABLE)
    assert result == {"a": {"pairable"}}
    assert 9.0 <= clock.now <= 10.0


def test_window_is_shared_across_adapters():
    result, clock = run(lambda addr: PAIRABLE, addrs=("a", "b", "c"))
    assert set(result) == {"a", "b", "c"}
    assert clock.now <= 10.0


def test_only_the_adapter_still_open_is_reported():
    state = {"a": iter([PAIRABLE, CLOSED]), "b": iter([PAIRABLE] * 99)}
    result, _ = run(lambda addr: next(state[addr]), addrs=("a", "b"))
    assert result == {"b": {"pairable"}}


def test_discoverable_is_also_waited_on():
    assert open_flags("Discoverable: yes\n\tPairable: yes\n") == {
        "discoverable",
        "pairable",
    }
    reads = iter(["Discoverable: yes\n", CLOSED])
    result, _ = run(lambda addr: next(reads))
    assert result == {}


def test_verify_check_uses_the_settle_window_and_keeps_its_markers():
    text = VERIFY.read_text()
    assert "snaphouse-managed-check" in text.splitlines()[1]
    assert "from snaphouse.btsettle import settle" in text
    assert "settle(addrs, show)" in text
