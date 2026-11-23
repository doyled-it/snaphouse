"""Tests for snaphouse.framefollow: the fast frame mirror republish.

When the page sends Next, Back or Show, a path unit starts this one-shot. It
republishes the targeted frame's frame.json every 2 s (and frame.png only when
updated_at changed) until the frame's applied_seq reaches the command's seq or 90 s
pass, and remembers the last seq it handled so a settings-only rewrite of the
control file is a no-op. All time and network are injected.
"""

from __future__ import annotations

import json
from pathlib import Path

from snaphouse import framefollow

PNG = b"\x89PNG\r\n\x1a\nbytes"


class World:
    """A fake clock, a fake network of frames, and the files the follower uses."""

    def __init__(self, tmp_path: Path, frames=("nightstand", "office")):
        self.t = 1000.0
        self.control = tmp_path / "art-control.json"
        self.targets = tmp_path / "targets.json"
        self.root = tmp_path / "frames"
        self.state = tmp_path / "state" / "frame-follow.json"
        self.meta: dict[str, dict | None] = {}
        self.image_fetches: list[str] = []
        self.meta_fetches: list[str] = []
        self.on_sleep = None
        self.targets.write_text(
            json.dumps(
                {
                    "targets": [],
                    "frames": [
                        {"id": f, "base": f"http://{f}.local/nightstand/"} for f in frames
                    ],
                }
            )
        )
        for f in frames:
            self.meta[f] = {"updated_at": "t0", "screen": "art"}

    def command(self, seq, frame=None):
        self.control.write_text(
            json.dumps({"settings": {}, "command": {"seq": seq, "action": "next", "frame": frame}})
        )

    def now(self):
        return self.t

    def sleep(self, seconds):
        self.t += seconds
        if self.on_sleep:
            self.on_sleep(self)

    def fetch_meta(self, url):
        self.meta_fetches.append(url)
        frame = url.split("//")[1].split(".")[0]
        return self.meta.get(frame)

    def fetch_image(self, url):
        self.image_fetches.append(url)
        return PNG

    def run(self, **kwargs):
        return framefollow.run(
            self.control,
            self.targets,
            self.root,
            self.state,
            now=self.now,
            sleep=self.sleep,
            fetch_meta=self.fetch_meta,
            fetch_image=self.fetch_image,
            **kwargs,
        )

    def frames_polled(self):
        return sorted({u.split("//")[1].split(".")[0] for u in self.meta_fetches})


def test_stops_when_applied_seq_reaches_the_command(tmp_path):
    w = World(tmp_path)
    w.command(7, "nightstand")

    def confirm(world):
        if world.t >= 1006:
            world.meta["nightstand"] = {"updated_at": "t1", "screen": "art", "applied_seq": 7}

    w.on_sleep = confirm
    assert w.run() == 0
    assert w.t < 1020
    written = json.loads((w.root / "nightstand" / "frame.json").read_text())
    assert written["applied_seq"] == 7


def test_stops_at_ninety_seconds_without_confirmation(tmp_path):
    w = World(tmp_path)
    w.command(7, "nightstand")
    assert w.run() == 0
    assert 1090 <= w.t <= 1092


def test_polls_only_the_targeted_frame(tmp_path):
    w = World(tmp_path)
    w.command(7, "office")
    w.run(window_s=6)
    assert w.frames_polled() == ["office"]
    assert not (w.root / "nightstand").exists()


def test_a_null_frame_polls_every_frame(tmp_path):
    w = World(tmp_path)
    w.command(7, None)
    w.run(window_s=6)
    assert w.frames_polled() == ["nightstand", "office"]


def test_png_is_fetched_only_when_updated_at_changes(tmp_path):
    w = World(tmp_path)
    w.command(7, "nightstand")
    w.run(window_s=10)  # five polls, updated_at never changes
    assert len(w.image_fetches) == 1
    w.state.unlink()
    w.command(8, "nightstand")
    w.image_fetches.clear()

    def change(world):
        world.meta["nightstand"] = {"updated_at": "t2", "screen": "art"}

    w.on_sleep = change
    w.run(window_s=10)
    assert len(w.image_fetches) == 2  # once at the start, once for the new updated_at


def test_pending_is_republished_without_a_new_image(tmp_path):
    w = World(tmp_path)
    w.command(7, "nightstand")
    pending = {
        "seq": 7, "id": "a.jpg", "since": "s", "state": "changing", "reason": None,
    }

    def announce(world):
        world.meta["nightstand"] = {"updated_at": "t0", "screen": "art", "pending": pending}

    w.on_sleep = announce
    w.run(window_s=6)
    written = json.loads((w.root / "nightstand" / "frame.json").read_text())
    assert written["pending"]["state"] == "changing"
    assert len(w.image_fetches) == 1


def test_a_new_command_extends_the_window(tmp_path):
    w = World(tmp_path)
    w.command(7, "nightstand")

    def second(world):
        if 1050 <= world.t < 1052:
            world.command(8, "nightstand")

    w.on_sleep = second
    w.run()
    assert w.t >= 1050 + 90  # 90 s from the second command, not from the first


def test_missing_or_malformed_control_exits_zero_without_polling(tmp_path):
    w = World(tmp_path)
    assert w.run() == 0  # no control file
    w.control.write_text("{not json")
    assert w.run() == 0
    w.control.write_text(json.dumps({"command": None}))
    assert w.run() == 0
    w.control.write_text(json.dumps({"command": {"seq": True, "frame": None}}))
    assert w.run() == 0
    assert w.meta_fetches == []


def test_unchanged_seq_since_the_last_run_exits_at_once(tmp_path):
    w = World(tmp_path)
    w.command(7, "nightstand")
    w.run(window_s=4)
    w.meta_fetches.clear()
    t_before = w.t

    assert w.run() == 0  # a settings-only rewrite leaves the same command seq

    assert w.meta_fetches == [] and w.t == t_before
    assert json.loads(w.state.read_text())["seq"] == 7


def test_a_higher_seq_after_a_handled_one_runs_again(tmp_path):
    w = World(tmp_path)
    w.command(7, "nightstand")
    w.run(window_s=4)
    w.command(8, "nightstand")
    w.meta_fetches.clear()
    w.run(window_s=4)
    assert w.meta_fetches


def test_an_unreachable_frame_keeps_the_last_good_copy_and_continues(tmp_path):
    w = World(tmp_path)
    w.command(7, "nightstand")
    (w.root / "nightstand").mkdir(parents=True)
    (w.root / "nightstand" / "frame.json").write_text('{"updated_at": "old", "screen": "art"}')
    w.meta["nightstand"] = None
    assert w.run(window_s=6) == 0
    assert json.loads((w.root / "nightstand" / "frame.json").read_text())["updated_at"] == "old"


def test_an_invalid_frame_id_in_the_command_or_targets_is_skipped(tmp_path):
    w = World(tmp_path, frames=("nightstand", "../evil"))
    w.command(7, None)
    w.run(window_s=4)
    assert w.frames_polled() == ["nightstand"]
    w.state.unlink()
    w.command(8, "../evil")
    w.meta_fetches.clear()
    w.run(window_s=4)
    assert w.meta_fetches == []
