"""Guard: one frames republish root shared by the timer, nginx and artadmin.

The house-status timer writes each frame's art-history.json under the frames
root, nginx serves /frames/ from the parent of that root's last segment, and
artadmin reads the same file straight off disk to capture an art pin's piece.
Each used to hard-code its own path and artadmin drifted (issue #124). All
three now derive from snaphouse.paths.
"""

from __future__ import annotations

import re
from pathlib import Path

from snaphouse import artadmin, housestatus, paths

REPO = Path(__file__).resolve().parents[2]
NGINX_TEMPLATE = REPO / "roles/room_picker/templates/room_picker.conf.j2"


def test_housestatus_default_frames_root_is_the_shared_constant():
    assert housestatus.DEFAULT_FRAMES_ROOT == paths.DEFAULT_FRAMES_ROOT


def test_frames_root_sits_under_the_public_root():
    assert paths.DEFAULT_FRAMES_ROOT == paths.frames_root(paths.DEFAULT_PUBLIC_ROOT)
    assert paths.DEFAULT_FRAMES_ROOT.name == "frames"


def test_nginx_frames_location_root_matches_the_shared_constant():
    conf = NGINX_TEMPLATE.read_text()
    match = re.search(r"location /frames/ \{\s*root\s+([^;]+);", conf)
    assert match, "no 'location /frames/' block with a root"
    # nginx maps /frames/<id>/x to <root>/frames/<id>/x, so the root is the
    # frames root's parent.
    assert Path(match.group(1).strip()) == paths.DEFAULT_FRAMES_ROOT.parent


def test_artadmin_art_history_path_matches_what_the_timer_writes(tmp_path):
    public = tmp_path / "public"
    got = artadmin.frame_art_history_paths(public, ["nightstand", "office"])
    assert got == {
        fid: paths.frames_root(public) / fid / "art-history.json"
        for fid in ("nightstand", "office")
    }
    # The timer publishes <frames root>/<id>/art-history.json for the same root.
    assert got["office"] == housestatus_frame_dir(public, "office") / "art-history.json"


def housestatus_frame_dir(public: Path, frame_id: str) -> Path:
    return paths.frames_root(public) / frame_id
