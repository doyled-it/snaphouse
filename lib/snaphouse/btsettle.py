"""Let an adapter finish closing before the Bluetooth verify check fails it.

During a self-update the `bluetooth` role rewrites /etc/bluetooth/main.conf and
`snaphouse-bt-lockdown.service` turns pairable off a few seconds later (issue
#216). A verify run that lands in that gap reads an adapter still open and fails
a healthy apply, which sends the hub back to the previous commit. The check
therefore re-reads an open adapter once a second for a short, bounded time and
fails only the adapters still open when that time is up. An adapter that stays
open for the whole window still fails.
"""

from __future__ import annotations

import time
from typing import Callable, Iterable

SETTLE_S = 10.0
STEP_S = 1.0


def open_flags(shown: str) -> set[str]:
    """Return which open states (`discoverable`, `pairable`) a `show` reports."""
    flags = set()
    if "Discoverable: yes" in shown:
        flags.add("discoverable")
    if "Pairable: yes" in shown:
        flags.add("pairable")
    return flags


def settle(
    addrs: Iterable[str],
    show: Callable[[str], str],
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
    window_s: float = SETTLE_S,
    step_s: float = STEP_S,
) -> dict[str, set[str]]:
    """Poll each adapter until closed or the shared window ends.

    Returns the open flags of every adapter still open at the end (empty when
    all are closed). The window is shared, so the worst case is `window_s` plus
    one read per adapter, however many adapters the host has.
    """
    deadline = monotonic() + window_s
    pending = list(addrs)
    still_open: dict[str, set[str]] = {}
    while True:
        still_open = {}
        for addr in pending:
            flags = open_flags(show(addr))
            if flags:
                still_open[addr] = flags
        pending = list(still_open)
        if not pending or monotonic() + step_s > deadline:
            return still_open
        sleep(step_s)
