"""Fast frame mirror republish while a change is in flight (hub, stdlib only).

The house-status timer republishes each frame's mirror once a minute. When the page
sends Next, Back or Show, artadmin rewrites ``art-control.json``; a systemd path
unit then starts this one-shot. It reads the latest command and polls the targeted
frame (every frame when the command names none) every 2 s, republishing
``frame.json`` each time and ``frame.png`` only when ``updated_at`` changed, until
the frame's ``applied_seq`` reaches the command's seq or 90 s pass. A newer command
read while it runs restarts the 90 s window for that command.

It reuses the house-status poller's hardened fetch and write helpers (no redirects,
size caps, shape checks, atomic writes, last good copy kept), and runs as the same
user. ``art-control.json`` is also rewritten by settings changes, so the last seq
handled is kept in a small state file and an unchanged seq exits at once.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.parse
from pathlib import Path
from typing import Callable, Optional

from snaphouse import housestatus, paths

DEFAULT_CONTROL = paths.DEFAULT_PUBLIC_ROOT / "art" / "art-control.json"
DEFAULT_TARGETS = housestatus.DEFAULT_TARGETS
DEFAULT_STATE = Path("/var/lib/snaphouse/frame-follow/state.json")

WINDOW_S = 90.0
INTERVAL_S = 2.0
MAX_CONTROL_BYTES = 256 * 1024

FetchMeta = Callable[[str], Optional[dict]]
FetchImage = Callable[[str], Optional[bytes]]


def _log(message: str) -> None:
    print(f"frame-follow: {message}", file=sys.stderr)


def read_command(control_path: Path) -> Optional[tuple[int, Optional[str]]]:
    """The latest command as (seq, frame id or None for every frame), or None.

    None for a missing or malformed file, no command, a seq that is not a
    non-negative int (a bool is not), or a ``frame`` that is not a valid frame id.
    """
    try:
        raw = Path(control_path).read_bytes()
        if len(raw) > MAX_CONTROL_BYTES:
            return None
        document = json.loads(raw.decode("utf-8"))
    except (OSError, ValueError):
        return None
    command = document.get("command") if isinstance(document, dict) else None
    if not isinstance(command, dict):
        return None
    seq = command.get("seq")
    if isinstance(seq, bool) or not isinstance(seq, int) or seq < 0:
        return None
    frame = command.get("frame")
    if frame is not None and housestatus._valid_frame_id(frame) is None:
        return None
    return seq, frame


def _handled_seq(state_path: Path) -> Optional[int]:
    try:
        value = json.loads(Path(state_path).read_text()).get("seq")
    except (OSError, ValueError, AttributeError):
        return None
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _remember(state_path: Path, seq: int) -> None:
    try:
        state_path = Path(state_path)
        state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"seq": seq}) + "\n")
        tmp.replace(state_path)
    except OSError as exc:
        _log(f"cannot record the handled seq: {exc}")


def _bases(targets_path: Path, frame: Optional[str]) -> dict[str, str]:
    """Frame id -> base URL for the frames this command targets."""
    try:
        _targets, frames = housestatus._load_config(Path(targets_path))
    except (OSError, ValueError) as exc:
        _log(f"cannot read the targets file: {exc}")
        return {}
    bases: dict[str, str] = {}
    for entry in frames:
        if not isinstance(entry, dict):
            continue
        frame_id = housestatus._valid_frame_id(entry.get("id"))
        base = housestatus._frame_base(entry.get("base"))
        if frame_id is None or base is None:
            continue
        if frame is None or frame == frame_id:
            bases[frame_id] = base
    return bases


def _poll_one(
    base: str,
    out_dir: Path,
    last_updated: Optional[str],
    fetch_meta: FetchMeta,
    fetch_image: FetchImage,
) -> tuple[Optional[dict], Optional[str]]:
    """Republish one frame once; return (its validated meta or None, updated_at seen).

    A failed fetch returns (None, last_updated) and leaves the last good files. The
    image is fetched and written before the json, so a page that sees a new
    ``updated_at`` finds the matching image.
    """
    meta = fetch_meta(urllib.parse.urljoin(base, "frame.json"))
    if meta is None:
        return None, last_updated
    seen = last_updated
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        if meta["updated_at"] != last_updated:
            data = fetch_image(urllib.parse.urljoin(base, "frame.png"))
            if data is not None:
                housestatus.write_frame_image(out_dir / "frame.png", data)
                seen = meta["updated_at"]
        housestatus.write_frame_meta(out_dir / "frame.json", meta)
    except Exception as exc:
        _log(f"{out_dir.name}: republish failed: {exc}")
    return meta, seen


def run(
    control_path: Path,
    targets_path: Path,
    frames_root: Path,
    state_path: Path,
    *,
    now: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    fetch_meta: Optional[FetchMeta] = None,
    fetch_image: Optional[FetchImage] = None,
    window_s: float = WINDOW_S,
    interval_s: float = INTERVAL_S,
) -> int:
    """Follow the latest command until it is confirmed or the window ends. Exit 0."""
    if fetch_meta is None:
        def fetch_meta(url: str) -> Optional[dict]:
            return housestatus.http_fetch(
                url, timeout=housestatus.DEFAULT_TIMEOUT, select=housestatus.select_frame_meta
            )
    if fetch_image is None:
        def fetch_image(url: str) -> Optional[bytes]:
            return housestatus.fetch_frame_image(url, timeout=housestatus.DEFAULT_TIMEOUT)

    command = read_command(control_path)
    if command is None or _handled_seq(state_path) == command[0]:
        return 0
    seq, frame = command
    started = now()
    remaining = _bases(targets_path, frame)
    last_updated: dict[str, Optional[str]] = {}

    while True:
        for frame_id in list(remaining):
            meta, seen = _poll_one(
                remaining[frame_id],
                Path(frames_root) / frame_id,
                last_updated.get(frame_id),
                fetch_meta,
                fetch_image,
            )
            last_updated[frame_id] = seen
            if meta is not None and meta.get("applied_seq", -1) >= seq:
                del remaining[frame_id]
        if not remaining or now() - started >= window_s:
            break
        sleep(interval_s)
        latest = read_command(control_path)
        if latest is not None and latest[0] != seq:
            seq, frame = latest
            started = now()
            remaining = _bases(targets_path, frame)

    _remember(state_path, seq)
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="snaphouse.framefollow",
        description="Republish a frame mirror fast while a command is in flight (hub).",
    )
    parser.add_argument("--control", default=str(DEFAULT_CONTROL))
    parser.add_argument("--targets", default=str(DEFAULT_TARGETS))
    parser.add_argument("--frames-root", default=str(housestatus.DEFAULT_FRAMES_ROOT))
    parser.add_argument("--state", default=str(DEFAULT_STATE))
    args = parser.parse_args(argv)
    try:
        return run(Path(args.control), Path(args.targets), Path(args.frames_root), Path(args.state))
    except Exception as exc:  # never fail the unit over feedback
        _log(f"stopped: {exc}")
        return 0


if __name__ == "__main__":
    sys.exit(main())
