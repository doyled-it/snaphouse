"""Tests for snaphouse.update, the self-update wrapper.

These exercise the whole algorithm against a real but local git setup and shims,
with no network and no real ansible or systemd:

  - a bare "origin" repo plus a "dev" clone used to author and push commits to
    main, and a "device" clone (SNAPHOUSE_REPO) that update fetches into;
  - an ansible-playbook shim on PATH that logs each call and passes or fails
    based on a marker committed in the working tree;
  - a verify.d check that passes or fails from a second marker in the tree.

Together they let a commit be "good", "fails verify", or "fails apply", so the
tests can watch update promote a good commit to last_good, roll a bad one back,
record the failure, skip retrying it, pick up a newer commit, and survive a
fetch failure.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from snaphouse import update


def git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def rev(ref: str, *, cwd: Path) -> str:
    out = subprocess.run(
        ["git", "rev-parse", ref], cwd=cwd, check=True, capture_output=True, text=True
    )
    return out.stdout.strip()


def read_json(path: Path) -> dict:
    return json.loads(path.read_text())


def count_lines(path: Path) -> int:
    if not path.exists():
        return 0
    return len([line for line in path.read_text().splitlines() if line])


def _configure(repo: Path) -> None:
    git("config", "user.email", "ci@snaphouse.invalid", cwd=repo)
    git("config", "user.name", "snaphouse ci", cwd=repo)


@pytest.fixture
def world(tmp_path, monkeypatch):
    origin = tmp_path / "origin.git"
    subprocess.run(
        ["git", "init", "--bare", "-b", "main", str(origin)], check=True, capture_output=True
    )

    dev = tmp_path / "dev"
    subprocess.run(["git", "clone", str(origin), str(dev)], check=True, capture_output=True)
    _configure(dev)
    (dev / "apply").write_text("ok")
    (dev / "verify").write_text("ok")
    git("add", "-A", cwd=dev)
    git("commit", "-m", "v0", cwd=dev)
    git("push", "origin", "main", cwd=dev)

    repo = tmp_path / "repo"
    subprocess.run(["git", "clone", str(origin), str(repo)], check=True, capture_output=True)
    _configure(repo)

    bindir = tmp_path / "bin"
    bindir.mkdir()
    applylog = tmp_path / "apply.log"
    shim = bindir / "ansible-playbook"
    shim.write_text(
        "#!/usr/bin/env bash\n"
        f'echo apply >> "{applylog}"\n'
        'marker="$(cat "$SNAPHOUSE_REPO/apply" 2>/dev/null || echo ok)"\n'
        '[ "$marker" = ok ]\n'
    )
    shim.chmod(0o755)

    verifydir = tmp_path / "verify.d"
    verifydir.mkdir()
    check = verifydir / "marker.sh"
    check.write_text(
        "#!/usr/bin/env bash\n"
        'marker="$(cat "$SNAPHOUSE_REPO/verify" 2>/dev/null || echo ok)"\n'
        'if [ "$marker" != ok ]; then\n'
        '  echo "verify marker is $marker" >&2\n'
        "  exit 1\n"
        "fi\n"
    )
    check.chmod(0o755)

    state = tmp_path / "state.json"
    status = tmp_path / "public" / "status.json"

    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("SNAPHOUSE_REPO", str(repo))
    monkeypatch.setenv("SNAPHOUSE_STATE", str(state))
    monkeypatch.setenv("SNAPHOUSE_STATUS", str(status))
    monkeypatch.setenv("SNAPHOUSE_VERIFY_DIR", str(verifydir))

    return SimpleNamespace(
        tmp=tmp_path,
        origin=origin,
        dev=dev,
        repo=repo,
        state=state,
        status=status,
        applylog=applylog,
    )


def push_commit(world, *, msg: str, apply: str = "ok", verify: str = "ok") -> str:
    """Author a commit on main with the given markers and return its sha."""
    (world.dev / "apply").write_text(apply)
    (world.dev / "verify").write_text(verify)
    # The message is written into a tracked file too, so every commit differs
    # from the one before even when its markers are unchanged.
    (world.dev / "rev").write_text(msg)
    git("add", "-A", cwd=world.dev)
    git("commit", "-m", msg, cwd=world.dev)
    git("push", "origin", "main", cwd=world.dev)
    return rev("HEAD", cwd=world.dev)


def test_good_new_commit_becomes_last_good(world):
    v1 = push_commit(world, msg="v1")
    assert update.main([]) == 0

    state = read_json(world.state)
    assert state["last_good"] == v1
    assert state["failed"] is None
    assert rev("HEAD", cwd=world.repo) == v1

    status = read_json(world.status)
    assert status["host"] == socket.gethostname()
    assert status["commit"] == v1
    assert status["main_commit"] == v1
    assert status["last_good"] == v1
    assert status["verify"] == "pass"


def test_verify_failing_commit_is_rolled_back_recorded_and_not_retried(world):
    v1 = push_commit(world, msg="v1")
    assert update.main([]) == 0  # establishes last_good = v1

    v2 = push_commit(world, msg="v2", verify="fail")
    assert update.main([]) == 0  # fallback to v1 succeeds

    state = read_json(world.state)
    assert state["last_good"] == v1
    assert state["failed"] == v2
    assert rev("HEAD", cwd=world.repo) == v1

    status = read_json(world.status)
    assert status["commit"] == v1
    assert status["main_commit"] == v2
    assert status["last_good"] == v1
    assert status["verify"] == "pass"

    # main has not moved, so the failed commit must not be applied again.
    applies_before = count_lines(world.applylog)
    assert update.main([]) == 0
    assert count_lines(world.applylog) == applies_before
    assert rev("HEAD", cwd=world.repo) == v1
    assert read_json(world.state)["failed"] == v2


def test_newer_commit_after_a_failure_is_tried(world):
    push_commit(world, msg="v1")
    update.main([])
    push_commit(world, msg="v2", verify="fail")
    update.main([])  # rolls back to v1, records v2 as failed

    v3 = push_commit(world, msg="v3")
    assert update.main([]) == 0

    state = read_json(world.state)
    assert state["last_good"] == v3
    assert state["failed"] is None
    assert rev("HEAD", cwd=world.repo) == v3
    assert read_json(world.status)["verify"] == "pass"


def test_apply_failure_is_rolled_back(world):
    v1 = push_commit(world, msg="v1")
    update.main([])

    v2 = push_commit(world, msg="v2", apply="fail")
    assert update.main([]) == 0  # fallback to v1 succeeds

    state = read_json(world.state)
    assert state["last_good"] == v1
    assert state["failed"] == v2
    assert rev("HEAD", cwd=world.repo) == v1


def test_fetch_failure_changes_nothing_but_writes_status(world):
    v1 = push_commit(world, msg="v1")
    update.main([])

    state_before = read_json(world.state)
    head_before = rev("HEAD", cwd=world.repo)
    applies_before = count_lines(world.applylog)

    git("remote", "set-url", "origin", str(world.tmp / "gone.git"), cwd=world.repo)
    world.status.unlink()  # prove the run rewrites it

    assert update.main([]) == 0
    assert read_json(world.state) == state_before
    assert rev("HEAD", cwd=world.repo) == head_before
    assert count_lines(world.applylog) == applies_before  # no apply ran

    status = read_json(world.status)
    assert status["commit"] == head_before
    assert status["main_commit"] == v1


def test_force_reapplies_head_when_nothing_changed(world):
    v1 = push_commit(world, msg="v1")
    update.main([])  # last_good = HEAD = main = v1

    applies_before = count_lines(world.applylog)
    assert update.main([]) == 0  # no change: short-circuits, no apply
    assert count_lines(world.applylog) == applies_before

    assert update.main(["--force"]) == 0  # force applies the current commit
    assert count_lines(world.applylog) == applies_before + 1
    assert rev("HEAD", cwd=world.repo) == v1
    assert read_json(world.state)["last_good"] == v1


def test_first_apply_passes_a_check_that_needs_the_status_file(world):
    """A verify check that needs the published status file (the web check reads
    nginx's /status.json) must pass on the very first apply, when no status file
    has been written yet. update writes a provisional status before verify runs,
    so the check has a real file to read; the run's real publish then overwrites
    it with the true result.
    """
    # A check that mirrors the web check: it fails unless a status file with a
    # host field already exists. On a fresh machine nothing has published one.
    check = world.tmp / "verify.d" / "needs_status.sh"
    check.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'test -f "$SNAPHOUSE_STATUS"\n'
        'grep -q \'"host"\' "$SNAPHOUSE_STATUS"\n'
    )
    check.chmod(0o755)

    v1 = push_commit(world, msg="v1")
    assert not world.status.exists()  # truly fresh: no status file yet

    assert update.main(["--force"]) == 0

    state = read_json(world.state)
    assert state["last_good"] == v1
    assert state["failed"] is None

    status = read_json(world.status)
    assert status["commit"] == v1
    assert status["verify"] == "pass"


def test_existing_status_is_untouched_by_the_provisional_write(world):
    """On an established machine a status file already exists. The provisional
    write must be a no-op there and must never clobber the real status before
    verify runs. A capture check copies the file mid-verify so the content it saw
    can be checked; it must still be the pre-existing document.
    """
    world.status.parent.mkdir(parents=True, exist_ok=True)
    sentinel = '{"host": "sentinel", "commit": "preexisting"}\n'
    world.status.write_text(sentinel)

    capture = world.tmp / "verify.d" / "capture.sh"
    capture.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'cp "$SNAPHOUSE_STATUS" "$SNAPHOUSE_STATUS.at_verify"\n'
    )
    capture.chmod(0o755)

    v1 = push_commit(world, msg="v1")
    assert update.main(["--force"]) == 0

    # Mid-verify the file still held the pre-existing content: the provisional
    # write saw an existing file and did nothing.
    at_verify = Path(str(world.status) + ".at_verify").read_text()
    assert at_verify == sentinel

    # The run's own publish then wrote the real result over it.
    status = read_json(world.status)
    assert status["commit"] == v1
    assert status["verify"] == "pass"


def test_failure_with_no_last_good_exits_nonzero(world):
    v1 = push_commit(world, msg="v1", verify="fail")
    assert update.main([]) != 0

    state = read_json(world.state)
    assert state["failed"] == v1
    assert state["last_good"] is None
    assert rev("HEAD", cwd=world.repo) == v1

    status = read_json(world.status)
    assert status["commit"] == v1
    assert status["verify"] == "fail"
