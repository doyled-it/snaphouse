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

The same timer also republishes the nightstand frame's art-history.json
same-origin (the "Art history file" contract in docs/cross-repo-features.md): it
is fetched from the nightstand host with the same hardening, validated to the
pinned fields, and written to /var/lib/snaphouse/public/art-history.json. That
fetch is best-effort and runs after the health write, so a missing or malformed
frame never sinks the poll; the last good copy stays in place.

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
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

# The list of machines to poll and the output file, both overridable on the CLI.
# The targets file is written from the inventory by the room_picker role.
DEFAULT_TARGETS = Path("/etc/snaphouse/house-status-targets.json")
DEFAULT_OUT = Path("/var/lib/snaphouse/public/house-status.json")

# The frame's art history, produced by nightstand and republished same-origin so
# the room-picker page can show what is on the frame without resolving another
# machine's .local name. The URL is fixed (the pinned contract in
# docs/cross-repo-features.md); both are overridable on the CLI.
DEFAULT_ART_HISTORY_URL = "http://nightstand.local/nightstand/art-history.json"
DEFAULT_ART_HISTORY_OUT = Path("/var/lib/snaphouse/public/art-history.json")

# The history is bounded by the producer; the hub enforces the same cap so a
# runaway or hostile body cannot be republished without bound.
MAX_ART_HISTORY = 50

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


def _safe_file_name(value: object) -> Optional[str]:
    """Return a bare art-share file name, or None if it is unusable.

    The page builds an /art/<file> URL from this, so a value with a path
    separator or a leading dot (a traversal or hidden-file attempt) is rejected
    rather than republished.
    """
    if not isinstance(value, str) or not value:
        return None
    if "/" in value or "\\" in value or value.startswith("."):
        return None
    return value


def _https_or_none(value: object) -> Optional[str]:
    """Keep an https URL, drop anything else (http, javascript:, malformed)."""
    if not isinstance(value, str):
        return None
    try:
        return value if urllib.parse.urlparse(value).scheme == "https" else None
    except ValueError:
        return None


def _select_piece(raw: object) -> Optional[dict]:
    """One art piece, validated to the pinned contract, or None if unusable.

    file, title, and shown_at are required; artist is optional (null when absent
    or not a non-empty string); source_url is kept only when it is https. Any
    field outside the contract is dropped, so a peer cannot smuggle extra content
    onto the page.
    """
    if not isinstance(raw, dict):
        return None
    file = _safe_file_name(raw.get("file"))
    if file is None:
        return None
    title = raw.get("title")
    if not isinstance(title, str) or not title:
        return None
    shown_at = raw.get("shown_at")
    if not isinstance(shown_at, str) or not shown_at:
        return None
    artist = raw.get("artist")
    if not isinstance(artist, str) or not artist:
        artist = None
    return {
        "file": file,
        "title": title,
        "artist": artist,
        "source_url": _https_or_none(raw.get("source_url")),
        "shown_at": shown_at,
    }


def select_art_history(parsed: object) -> Optional[dict]:
    """Validate a fetched art-history body to {current, history} or None.

    None means the body is the wrong shape (not an object, or no history list),
    which the caller treats as a failed fetch and keeps the last good file.
    Malformed individual pieces are dropped, history is capped at MAX_ART_HISTORY
    newest-first, and a malformed current collapses to null. An empty but
    well-formed body is kept as {current: null, history: []}, which the page
    hides cleanly.
    """
    if not isinstance(parsed, dict):
        return None
    history_raw = parsed.get("history")
    if not isinstance(history_raw, list):
        return None
    history = [piece for piece in (_select_piece(item) for item in history_raw) if piece][
        :MAX_ART_HISTORY
    ]
    return {"current": _select_piece(parsed.get("current")), "history": history}


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


def _write_public_json(path: Path, document: dict) -> None:
    """Write a document to a public web-root path atomically and world-readable.

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


def write_house_status(path: Path, document: dict) -> None:
    """Write house-status.json atomically and world-readable."""
    _write_public_json(path, document)


def write_art_history(path: Path, document: dict) -> None:
    """Write art-history.json atomically and world-readable."""
    _write_public_json(path, document)


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


def http_fetch(
    url: str,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    select: Callable[[object], Optional[dict]] = select_status,
) -> Optional[dict]:
    """Fetch a JSON file and return the selector's result, or None on any trouble.

    Every failure mode (timeout, refused, redirect, non-200, oversize, non-JSON)
    collapses to None. The body is capped at MAX_STATUS_BYTES and passed to
    `select`, which keeps only the pinned fields for that document; the default
    selects the machine status schema, and the art-history poll passes
    select_art_history. Stdlib urllib keeps this venv-free.
    """
    try:
        request = urllib.request.Request(url, method="GET")
        with _OPENER.open(request, timeout=timeout) as response:
            if getattr(response, "status", 200) != 200:
                return None
            raw = response.read(MAX_STATUS_BYTES + 1)
        if len(raw) > MAX_STATUS_BYTES:
            return None
        return select(json.loads(raw.decode("utf-8")))
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
    parser.add_argument("--art-history-url", default=DEFAULT_ART_HISTORY_URL)
    parser.add_argument("--art-history-out", default=str(DEFAULT_ART_HISTORY_OUT))
    args = parser.parse_args(argv)

    targets = _load_targets(Path(args.targets))
    document = aggregate(
        targets,
        lambda url: http_fetch(url, timeout=args.timeout),
        lambda: datetime.now().astimezone(),
    )
    write_house_status(Path(args.out), document)

    # Republish the frame's art history same-origin. It is best-effort and comes
    # after the health write that the page depends on: a failed or malformed
    # fetch leaves the last good art-history.json in place and never sinks the
    # poll. Any error here is swallowed so the timer still exits 0.
    try:
        art = http_fetch(
            args.art_history_url, timeout=args.timeout, select=select_art_history
        )
        if art is not None:
            write_art_history(Path(args.art_history_out), art)
    except Exception:
        pass

    return 0


if __name__ == "__main__":
    sys.exit(main())
