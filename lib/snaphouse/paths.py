"""Paths shared by more than one hub module. Stdlib only.

The frames republish root is written by the house-status timer, served by nginx
(`location /frames/` in the room-picker conf) and read straight off disk by
artadmin to capture an art pin's piece. Keeping the layout here stops the three
from drifting; `tests/python/test_frames_root.py` pins all of them to it.
"""

from __future__ import annotations

from pathlib import Path

DEFAULT_PUBLIC_ROOT = Path("/var/lib/snaphouse/public")

# The timer-owned subdir of the public root, and the frames dir inside it:
# <public>/frame/frames/<id>/{art-history.json,frame.png,frame.json}.
FRAME_SUBDIR = "frame"
FRAMES_DIRNAME = "frames"


def frames_root(public_root: Path) -> Path:
    return Path(public_root) / FRAME_SUBDIR / FRAMES_DIRNAME


DEFAULT_FRAMES_ROOT = frames_root(DEFAULT_PUBLIC_ROOT)
