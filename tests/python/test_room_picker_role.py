"""Tests for the room_picker role's nginx template.

The hub republishes the frame mirror (frame.png and frame.json) into a
timer-owned subdir of the public web root; nginx serves each from an exact-match
location so it outranks the `location /` prefix. These pin the two locations, the
subdir root they serve from, and the Cache-Control header that makes a phone
revalidate the mirror instead of showing a stale `<img>` from heuristic
freshness. They read the template text directly (the frame locations are static,
no Jinja), the way test_artadmin_role.py pins role intent without an apply.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
TEMPLATE = REPO / "roles/room_picker/templates/room_picker.conf.j2"

FRAME_SUBDIR = "/var/lib/snaphouse/public/frame"


def _location_block(conf: str, name: str) -> str:
    """The body of the exact-match `location = <name>` block."""
    match = re.search(
        r"location\s*=\s*" + re.escape(name) + r"\s*\{(.*?)\}",
        conf,
        re.DOTALL,
    )
    assert match is not None, f"no exact-match location for {name}"
    return match.group(1)


def test_frame_png_location_serves_the_subdir_with_no_cache():
    block = _location_block(TEMPLATE.read_text(), "/frame.png")
    assert f"root {FRAME_SUBDIR};" in block
    assert "default_type image/png;" in block
    assert 'add_header Cache-Control "no-cache";' in block


def test_frame_json_location_serves_the_subdir_with_no_cache():
    block = _location_block(TEMPLATE.read_text(), "/frame.json")
    assert f"root {FRAME_SUBDIR};" in block
    assert "default_type application/json;" in block
    assert 'add_header Cache-Control "no-cache";' in block
