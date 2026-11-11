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
pinned fields, and written to /var/lib/snaphouse/public/art-history.json. It
likewise republishes the frame mirror (the "Frame mirror" contract): frame.png,
the image the frame last drew, with a 2 MB cap and a PNG-signature check, and
frame.json ({updated_at, screen}) validated to its two fields, both into a
timer-owned subdir of the public root (/var/lib/snaphouse/public/frame). Each of
these three is best-effort and runs after the health write, so a missing or
malformed frame never sinks the poll; the last good copy stays in place.

The same timer also republishes every inventory frame (the hosts whose roles
include nightstand_bootstrap, named in the targets file's "frames" list) under
/var/lib/snaphouse/public/frame/frames/<id>/, one subdir per frame id, with the
same hardening. The single paths above keep serving the nightstand's copy for
this release. A frame id is validated before any path is built from it, and a
frame that is offline or not yet installed is skipped without disturbing the
others (tests/python/test_housestatus.py).

Stdlib only (urllib), like status.py, so no venv dependency is pulled in for it.
The fetch and the clock are injected so the aggregation logic is unit tested with
no network (tests/python/test_housestatus.py).
"""

from __future__ import annotations

import argparse
import json
import os
import re
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

# The frame mirror, also produced by nightstand and republished same-origin so
# the room-picker page can show exactly what the e-ink frame last drew without
# the browser resolving the frame's .local name. frame.png is the image last
# pushed to the panel (a fixed 800x480 PNG); frame.json is {updated_at, screen}.
# The URLs are the pinned "Frame mirror" contract in docs/cross-repo-features.md;
# all four are overridable on the CLI. Both files live in a subdir the timer
# (root) owns, so republishing never chowns the shared public root.
DEFAULT_FRAME_IMAGE_URL = "http://nightstand.local/nightstand/frame.png"
DEFAULT_FRAME_IMAGE_OUT = Path("/var/lib/snaphouse/public/frame/frame.png")
DEFAULT_FRAME_META_URL = "http://nightstand.local/nightstand/frame.json"
DEFAULT_FRAME_META_OUT = Path("/var/lib/snaphouse/public/frame/frame.json")

# The system now has several frames (nightstand, office), each an inventory host
# whose roles include nightstand_bootstrap. The targets file lists them under
# "frames" as {"id", "base"}; the timer republishes each frame's art history and
# mirror under this root, one subdir per id (/frames/<id>/art-history.json,
# frame.png, frame.json). It sits inside the same timer-owned frame subdir as the
# single-frame mirror, so per-frame writes never chown the shared public root.
# The old single paths above keep serving the nightstand's copy for one release.
DEFAULT_FRAMES_ROOT = Path("/var/lib/snaphouse/public/frame/frames")

# A frame id is an inventory key: a lowercase letter, then up to 31 lowercase
# letters, digits, or hyphens. The id is validated against this before any path
# is built from it, so a malformed or hostile id can never write outside the
# frames root (no separators, no "..", no leading dot are possible).
FRAME_ID_RE = re.compile(r"^[a-z][a-z0-9-]{0,31}$")

# The mirror is a small 800x480 PNG; cap the read well above that so a runaway or
# hostile body can never be republished without bound.
MAX_FRAME_IMAGE_BYTES = 2 * 1024 * 1024

# The 8-byte PNG file signature. A body that does not begin with it is not a PNG
# and is rejected rather than republished at the same-origin frame.png path, so
# the page is never handed some other content type under that name.
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

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


def _safe_piece_id(value: object, file: str) -> Optional[str]:
    """A piece id relative to the art folder (at most one folder deep), or None.

    Newer art-history entries carry this id and the page deletes by it; the hub
    only republishes a value it can trust. A non-string, an empty string, a value
    with a backslash, an empty or dot-leading segment (a traversal or hidden-file
    attempt), or one more than a single folder deep is rejected (None). A first
    segment of ``Collection`` (case-insensitive) is rejected too: the hub's scan
    resolves ``Collection/x.jpg`` back to the top-level ``x.jpg``, so a delete by
    that id would hit the wrong piece. The id must also end in the entry's own
    ``file``, or the delete would act on a different piece than the one shown.
    Older entries have no id, so None is normal and the page falls back to
    ``file``.
    """
    if not isinstance(value, str) or not value or "\\" in value:
        return None
    parts = value.split("/")
    if len(parts) > 2:
        return None
    if any(not part or part.startswith(".") for part in parts):
        return None
    if parts[0].lower() == "collection":
        return None
    if parts[-1] != file:
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
    or not a non-empty string); source_url is kept only when it is https; id is
    kept only when it is a safe piece id (null otherwise, so the page falls back
    to `file`). Any field outside the contract is dropped, so a peer cannot
    smuggle extra content onto the page.
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
        "id": _safe_piece_id(raw.get("id"), file),
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


def select_frame_meta(parsed: object) -> Optional[dict]:
    """Validate a fetched frame.json body to {updated_at, screen} or None.

    None means the body is the wrong shape (not an object, or a missing or empty
    updated_at or screen), which the caller treats as a failed fetch and keeps the
    last good file. Only the two pinned fields are kept, so a peer cannot smuggle
    extra content onto the page through the same-origin mirror.
    """
    if not isinstance(parsed, dict):
        return None
    updated_at = parsed.get("updated_at")
    if not isinstance(updated_at, str) or not updated_at:
        return None
    screen = parsed.get("screen")
    if not isinstance(screen, str) or not screen:
        return None
    return {"updated_at": updated_at, "screen": screen}


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


def _write_public_bytes(path: Path, data: bytes) -> None:
    """Write bytes to a public web-root path atomically and world-readable.

    The same pattern as status.py: write a temp file in the same directory, fsync,
    widen to 0644 so nginx (www-data) can serve it, then os.replace over the
    destination so a polling reader never sees a partial file.
    """
    path = Path(path)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f"{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
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


def _write_public_json(path: Path, document: dict) -> None:
    """Serialize a document and write it atomically and world-readable."""
    _write_public_bytes(Path(path), (json.dumps(document, indent=2) + "\n").encode("utf-8"))


def write_house_status(path: Path, document: dict) -> None:
    """Write house-status.json atomically and world-readable."""
    _write_public_json(path, document)


def write_art_history(path: Path, document: dict) -> None:
    """Write art-history.json atomically and world-readable."""
    _write_public_json(path, document)


def write_frame_image(path: Path, data: bytes) -> None:
    """Write frame.png (the mirror image bytes) atomically and world-readable."""
    _write_public_bytes(path, data)


def write_frame_meta(path: Path, document: dict) -> None:
    """Write frame.json atomically and world-readable."""
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


def publish_art_history(url: str, out: Path, *, timeout: float = DEFAULT_TIMEOUT) -> None:
    """Fetch the frame's art history and republish it same-origin, best-effort.

    http_fetch already collapses every fetch failure (timeout, refused, redirect,
    non-200, oversize, non-JSON) and a malformed body to None, in which case the
    last good art-history.json is left untouched and nothing is written. The only
    errors that reach the guard here are a failing write_art_history (permissions,
    a full disk, a missing directory) or an unexpected bug; those are exactly what
    an operator needs to see, so the reason is printed once to stderr, which
    journald captures. Either way this returns normally and never raises, so the
    status aggregation that already wrote house-status.json always completes.
    """
    try:
        art = http_fetch(url, timeout=timeout, select=select_art_history)
        if art is not None:
            write_art_history(Path(out), art)
    except Exception as exc:
        print(f"art-history: keeping last good copy, republish failed: {exc}", file=sys.stderr)


def fetch_frame_image(url: str, *, timeout: float = DEFAULT_TIMEOUT) -> Optional[bytes]:
    """Fetch the frame's mirror PNG, or None on any trouble.

    Every failure mode (timeout, refused, redirect, non-200, oversize, a body that
    is not a PNG) collapses to None, so the caller keeps the last good mirror. The
    body is capped at MAX_FRAME_IMAGE_BYTES and must start with the PNG signature;
    the hub republishes the bytes as-is without decoding them. The same no-redirect
    opener as the JSON fetch is reused, so a hostile peer cannot bounce the hub to
    an internal address (SSRF).
    """
    try:
        request = urllib.request.Request(url, method="GET")
        with _OPENER.open(request, timeout=timeout) as response:
            if getattr(response, "status", 200) != 200:
                return None
            raw = response.read(MAX_FRAME_IMAGE_BYTES + 1)
        if len(raw) > MAX_FRAME_IMAGE_BYTES or not raw.startswith(PNG_SIGNATURE):
            return None
        return raw
    except Exception:
        return None


def publish_frame_image(url: str, out: Path, *, timeout: float = DEFAULT_TIMEOUT) -> None:
    """Fetch the frame's mirror PNG and republish it same-origin, best-effort.

    fetch_frame_image collapses every fetch failure and a non-PNG or oversize body
    to None, in which case the last good frame.png is left untouched. Only a
    failing write (permissions, a full disk, a missing directory) or an unexpected
    bug reaches the guard; the reason is printed once to stderr for journald. This
    returns normally and never raises, so the status aggregation always completes.
    """
    try:
        data = fetch_frame_image(url, timeout=timeout)
        if data is not None:
            write_frame_image(Path(out), data)
    except Exception as exc:
        print(f"frame.png: keeping last good copy, republish failed: {exc}", file=sys.stderr)


def publish_frame_meta(url: str, out: Path, *, timeout: float = DEFAULT_TIMEOUT) -> None:
    """Fetch the frame's metadata and republish it same-origin, best-effort.

    http_fetch collapses every fetch failure and a malformed body to None (the last
    good frame.json then stays in place). Only a failing write reaches the guard;
    its reason is printed once to stderr for journald. Returns normally, never
    raises, so the status aggregation always completes.
    """
    try:
        meta = http_fetch(url, timeout=timeout, select=select_frame_meta)
        if meta is not None:
            write_frame_meta(Path(out), meta)
    except Exception as exc:
        print(f"frame.json: keeping last good copy, republish failed: {exc}", file=sys.stderr)


def _valid_frame_id(value: object) -> Optional[str]:
    """Return the frame id if it matches the inventory-key pattern, else None.

    This runs before any path is built from the id (Review Focus 5), so a value
    with a path separator, a leading dot, ``..``, an uppercase letter, or an
    over-long body is rejected and never names a directory under the frames root.
    """
    if isinstance(value, str) and FRAME_ID_RE.fullmatch(value):
        return value
    return None


def _frame_base(value: object) -> Optional[str]:
    """Return an http(s) base URL ending in '/', or None if it is unusable.

    The base is only ever used to build the outbound fetch URL (never a local
    path), but it is still checked so a frame entry with a non-http scheme or no
    host is skipped rather than fetched. A base missing its trailing slash is
    normalised so urljoin appends the file name instead of replacing a segment.
    """
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = urllib.parse.urlparse(value)
    except ValueError:
        return None
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return None
    return value if value.endswith("/") else value + "/"


def publish_frame(base: str, out_dir: Path, *, timeout: float = DEFAULT_TIMEOUT) -> None:
    """Republish one frame's art history and mirror into out_dir, best-effort.

    Fetches art-history.json, frame.png, and frame.json from the frame's base URL
    and writes them under out_dir with the same hardening as the single-frame
    republish (no redirects, size caps, a PNG-signature or JSON-shape check, an
    atomic write, the last good copy kept on any failure). The directory is
    created first (root owns the timer-owned frame subdir, so this never chowns
    the shared public root). Any failure is logged and swallowed, so one frame
    never sinks the poll.
    """
    out_dir = Path(out_dir)
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        print(f"frames: cannot prepare {out_dir}: {exc}", file=sys.stderr)
        return
    publish_art_history(
        urllib.parse.urljoin(base, "art-history.json"), out_dir / "art-history.json", timeout=timeout
    )
    publish_frame_image(
        urllib.parse.urljoin(base, "frame.png"), out_dir / "frame.png", timeout=timeout
    )
    publish_frame_meta(
        urllib.parse.urljoin(base, "frame.json"), out_dir / "frame.json", timeout=timeout
    )


def publish_frames(frames: list, frames_root: Path, *, timeout: float = DEFAULT_TIMEOUT) -> None:
    """Republish every inventory frame under frames_root/<id>/, best-effort.

    Each frame is {"id", "base"}. The id is validated against the inventory-key
    pattern before any path is built (Review Focus 5), and a frame whose fetches
    all fail (offline, or not yet installed, like the office before its Inky
    arrives) simply leaves nothing written and never disturbs the others or the
    poll (Review Focus 1). A malformed entry is skipped with a line to stderr.
    """
    frames_root = Path(frames_root)
    for frame in frames:
        if not isinstance(frame, dict):
            print(f"frames: skipping a non-object frame entry: {frame!r}", file=sys.stderr)
            continue
        frame_id = _valid_frame_id(frame.get("id"))
        base = _frame_base(frame.get("base"))
        if frame_id is None or base is None:
            print(
                f"frames: skipping a frame with an invalid id or base: {frame!r}",
                file=sys.stderr,
            )
            continue
        try:
            publish_frame(base, frames_root / frame_id, timeout=timeout)
        except Exception as exc:
            print(f"frames: {frame_id}: republish failed: {exc}", file=sys.stderr)


def _load_config(path: Path) -> tuple[list[dict], list[dict]]:
    """Return (targets, frames) from the house-status targets file.

    The file is either the old bare JSON list of targets (no frames), or the new
    object {"targets": [...], "frames": [...]}. Accepting both keeps a hub updated
    before its inventory is re-rendered, or a frame page loaded on the old
    contract, working through the rollout (Review Focus 3).
    """
    data = json.loads(Path(path).read_text())
    if isinstance(data, list):
        return data, []
    if isinstance(data, dict):
        targets = data.get("targets")
        if not isinstance(targets, list):
            raise ValueError(f"{path}: 'targets' must be a JSON list")
        frames = data.get("frames")
        return targets, frames if isinstance(frames, list) else []
    raise ValueError(f"{path}: expected a JSON list or object of targets")


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
    parser.add_argument("--frame-image-url", default=DEFAULT_FRAME_IMAGE_URL)
    parser.add_argument("--frame-image-out", default=str(DEFAULT_FRAME_IMAGE_OUT))
    parser.add_argument("--frame-meta-url", default=DEFAULT_FRAME_META_URL)
    parser.add_argument("--frame-meta-out", default=str(DEFAULT_FRAME_META_OUT))
    parser.add_argument("--frames-root", default=str(DEFAULT_FRAMES_ROOT))
    args = parser.parse_args(argv)

    targets, frames = _load_config(Path(args.targets))
    document = aggregate(
        targets,
        lambda url: http_fetch(url, timeout=args.timeout),
        lambda: datetime.now().astimezone(),
    )
    write_house_status(Path(args.out), document)

    # Republish the frame's art history and live mirror same-origin, best-effort
    # and after the health write the page depends on, so none of them can ever sink
    # the status poll. Each keeps its own last good copy on any failure.
    publish_art_history(
        args.art_history_url, Path(args.art_history_out), timeout=args.timeout
    )
    publish_frame_image(
        args.frame_image_url, Path(args.frame_image_out), timeout=args.timeout
    )
    publish_frame_meta(
        args.frame_meta_url, Path(args.frame_meta_out), timeout=args.timeout
    )

    # Republish every inventory frame under /frames/<id>/, same hardening, also
    # best-effort and after the health write. The single paths above still carry
    # the nightstand's copy for this release; a frame that is offline or not yet
    # installed is skipped without disturbing the others.
    publish_frames(frames, Path(args.frames_root), timeout=args.timeout)

    return 0


if __name__ == "__main__":
    sys.exit(main())
