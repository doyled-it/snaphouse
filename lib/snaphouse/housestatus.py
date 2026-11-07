"""Aggregate every machine's status into one same-origin file on the hub.

Android Chrome often cannot resolve .local mDNS names, so the room-picker page
cannot fetch each machine's status.json from the browser; a failed fetch would
show a permanent false "needs attention" banner. Instead the hub runs this from a
60-second timer: it fetches each machine's status over the LAN with a short
timeout and writes /var/lib/snaphouse/public/house-status.json, which nginx
serves same-origin. The page reads only that one file.

The document shape, read by the page:

    {
      "checked_at": "<ISO 8601 with offset>",
      "machines": [
        {"name": "house",  "status": {<the machine's status.json> | null}},
        ...
      ]
    }

A machine that could not be reached (timeout, connection refused, non-200, bad
JSON) has a null status, which the page's healthBanner reads as unreachable.

Stdlib only (urllib), like status.py, so no venv dependency is pulled in for it.
The fetch and the clock are injected so the aggregation logic is unit tested with
no network (tests/python/test_housestatus.py).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

# The list of machines to poll and the output file, both overridable on the CLI.
# The targets file is written from the inventory by the room_picker role.
DEFAULT_TARGETS = Path("/etc/snaphouse/house-status-targets.json")
DEFAULT_OUT = Path("/var/lib/snaphouse/public/house-status.json")

# A short timeout so one slow or absent machine never holds up the whole poll
# past the timer's 60-second cadence.
DEFAULT_TIMEOUT = 3.0

# A status file is tiny; cap the read so a misbehaving or hostile peer cannot
# stream an unbounded body into the poll.
MAX_STATUS_BYTES = 64 * 1024

# The pinned status schema (docs/cross-repo-features.md). Only these fields are
# copied into house-status.json, so a peer cannot get arbitrary content
# republished same-origin through the hub, and the page sees a predictable shape.
STATUS_FIELDS = (
    "host",
    "repo",
    "commit",
    "main_commit",
    "last_good",
    "verify",
    "checked_at",
)

Fetch = Callable[[str], Optional[dict]]
Now = Callable[[], datetime]


def select_status(parsed: object) -> Optional[dict]:
    """Keep only the pinned status fields from a fetched body.

    A peer is one of our own machines, but the hub republishes what it fetches
    on a page anyone on the LAN can open, so only the known schema fields are
    passed through. A body that is not an object, or carries none of the fields,
    is rejected (None), which the page reads as unreachable.
    """
    if not isinstance(parsed, dict):
        return None
    selected = {key: parsed[key] for key in STATUS_FIELDS if key in parsed}
    return selected or None


def aggregate(targets: list[dict], fetch: Fetch, now: Now) -> dict:
    """Build the house-status document: one entry per target, in order.

    `targets` is a list of {"name", "url"}. For each, `fetch(url)` returns the
    machine's parsed status.json, or None when it could not be reached. A fetch
    that raises is treated as unreachable too, so one broken endpoint never sinks
    the whole poll. `now()` supplies the timezone-aware stamp.
    """
    machines = []
    for target in targets:
        try:
            status = fetch(target["url"])
        except Exception:
            status = None
        machines.append({"name": target["name"], "status": status})
    return {"checked_at": now().isoformat(), "machines": machines}


def write_house_status(path: Path, document: dict) -> None:
    """Write the document to path atomically and world-readable.

    The same pattern as status.py: serialize, write a temp file in the same
    directory, fsync, widen to 0644 so nginx (www-data) can serve it, then
    os.replace over the destination so a polling reader never sees a partial
    file.
    """
    path = Path(path)
    payload = json.dumps(document, indent=2) + "\n"
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f"{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
            os.fchmod(handle.fileno(), 0o644)
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A redirect handler that refuses every redirect.

    The poll fetches fixed status URLs; a status file never redirects. Refusing
    redirects stops a hostile or misconfigured peer from bouncing the hub to a
    loopback or internal address (SSRF) whose response would then be republished
    same-origin on the page.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


# One opener, reused, that follows no redirects.
_OPENER = urllib.request.build_opener(_NoRedirect)


def http_fetch(url: str, *, timeout: float = DEFAULT_TIMEOUT) -> Optional[dict]:
    """Fetch and parse one machine's status.json, or None if anything goes wrong.

    Every failure mode (timeout, refused, redirect, non-200, oversize, non-JSON)
    collapses to None, which the page reads as unreachable. The body is capped at
    MAX_STATUS_BYTES and only the pinned status fields are kept. Stdlib urllib
    keeps this venv-free.
    """
    try:
        request = urllib.request.Request(url, method="GET")
        with _OPENER.open(request, timeout=timeout) as response:
            if getattr(response, "status", 200) != 200:
                return None
            raw = response.read(MAX_STATUS_BYTES + 1)
        if len(raw) > MAX_STATUS_BYTES:
            return None
        return select_status(json.loads(raw.decode("utf-8")))
    except Exception:
        return None


def _load_targets(path: Path) -> list[dict]:
    data = json.loads(Path(path).read_text())
    if not isinstance(data, list):
        raise ValueError(f"{path}: expected a JSON list of targets")
    return data


def main(argv: Optional[list[str]] = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    parser = argparse.ArgumentParser(
        prog="snaphouse.housestatus",
        description="Poll every machine's status and write house-status.json (hub).",
    )
    parser.add_argument("--targets", default=str(DEFAULT_TARGETS))
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    args = parser.parse_args(argv)

    targets = _load_targets(Path(args.targets))
    document = aggregate(
        targets,
        lambda url: http_fetch(url, timeout=args.timeout),
        lambda: datetime.now().astimezone(),
    )
    write_house_status(Path(args.out), document)
    return 0


if __name__ == "__main__":
    sys.exit(main())
