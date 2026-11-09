"""snaphouse update: fetch main, apply it, verify, and fall back on failure.

This is the self-update wrapper the systemd timer runs every 15 minutes. It
keeps the machine on the newest commit of main that passes `verify` on this
machine, and never gets stuck on a broken commit:

    fetch origin main            (on network failure: write status, exit 0)
    target = origin/main
    if not --force and the target is the running good commit, or a commit we
        already know fails here, there is nothing to do: write status, exit 0
    check out target; apply site.yml; verify
    if apply and verify both pass: target becomes last_good, clear failed
    else: record target as failed, check out last_good, apply and verify it
    write status for whatever is running now; exit non-zero only if the machine
        is left in a failed state

State lives in /var/lib/snaphouse/state.json as {"last_good", "failed"}. The
published status file is written through snaphouse.status so the room picker and
nightstand see this machine's health. Paths default to the on-device locations
and are overridable with SNAPHOUSE_REPO / SNAPHOUSE_STATE / SNAPHOUSE_STATUS so
the tests can drive the whole algorithm against a temporary git repo.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from snaphouse import verify
from snaphouse.status import status_path, write_status

DEFAULT_REPO = Path("/opt/snaphouse")
DEFAULT_STATE = Path("/var/lib/snaphouse/state.json")


def _repo() -> Path:
    return Path(os.environ.get("SNAPHOUSE_REPO", str(DEFAULT_REPO)))


def _state_path() -> Path:
    return Path(os.environ.get("SNAPHOUSE_STATE", str(DEFAULT_STATE)))


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    """Run a git command in repo and raise if it fails."""
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )


def _git_ok(repo: Path, *args: str) -> bool:
    """Run a git command in repo and return whether it succeeded."""
    return (
        subprocess.run(
            ["git", "-C", str(repo), *args], capture_output=True, text=True
        ).returncode
        == 0
    )


def _rev(repo: Path, ref: str) -> str:
    return _git(repo, "rev-parse", ref).stdout.strip()


def _rev_or(repo: Path, ref: str, fallback: str) -> str:
    try:
        return _rev(repo, ref)
    except subprocess.CalledProcessError:
        return fallback


def _read_state(path: Path) -> dict:
    try:
        data = json.loads(path.read_text())
    except FileNotFoundError:
        return {"last_good": None, "failed": None}
    return {"last_good": data.get("last_good"), "failed": data.get("failed")}


def _write_state(path: Path, last_good: str | None, failed: str | None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({"last_good": last_good, "failed": failed}, indent=2) + "\n"
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(payload)
    os.replace(tmp, path)


def _apply(repo: Path) -> bool:
    """Apply site.yml to this machine the way the device does, in repo."""
    completed = subprocess.run(
        ["ansible-playbook", "site.yml", "-c", "local", "-i", "localhost,"],
        cwd=str(repo),
    )
    return completed.returncode == 0


def _ensure_status_exists(repo: Path, *, main_commit: str, last_good: str | None) -> None:
    """Publish a provisional status file before verify when none exists yet.

    On a brand-new machine the first update applies site.yml and then runs verify
    before any status file has been published. The web check confirms nginx serves
    a valid /status.json, so on that first run it would fail for the file's absence
    rather than for a real fault, the run would be recorded as failed, and
    bootstrap would stop before enabling the self-update timer (issue #35).

    Writing a provisional document here breaks that chicken-and-egg: the file is
    marked verify="fail" (honest: this machine has not passed verify yet) and
    carries the running commit and the pinned schema, so the web check has a real
    file to serve. The run's own _publish overwrites it with the true result a
    moment later. An established machine already has a status file, so this is a
    no-op there and does not fight the real write.
    """
    path = status_path()
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    write_status(
        path,
        host=socket.gethostname(),
        commit=_rev(repo, "HEAD"),
        main_commit=main_commit,
        last_good=last_good or "",
        verify="fail",
        now=datetime.now().astimezone(),
    )


def _verify_passes(repo: Path, *, main_commit: str, last_good: str | None) -> bool:
    """Run the on-device checks, print each result, and return whether all pass.

    A provisional status file is published first when none exists, so a check
    that reads the published status (the web check) does not fail merely because
    this is the machine's first apply.
    """
    _ensure_status_exists(repo, main_commit=main_commit, last_good=last_good)
    results = verify.run_checks(verify.verify_dir())
    for name, ok, detail in results:
        print(verify.format_line(name, ok, detail))
    return verify.all_pass(results)


def _publish(repo: Path, *, main_commit: str, last_good: str | None, result: str) -> None:
    path = status_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    write_status(
        path,
        host=socket.gethostname(),
        commit=_rev(repo, "HEAD"),
        main_commit=main_commit,
        last_good=last_good or "",
        verify=result,
        now=datetime.now().astimezone(),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="snaphouse update",
        description="Fetch main, apply it, verify, and fall back to the last good commit.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="apply the current target even when nothing has changed.",
    )
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)

    repo = _repo()
    state = _read_state(_state_path())
    last_good = state["last_good"]
    failed = state["failed"]

    # A failed fetch (no network, origin unreachable) must change nothing. Report
    # the commit that is already running, using the last origin/main we saw.
    if not _git_ok(repo, "fetch", "origin", "main"):
        head = _rev(repo, "HEAD")
        main_commit = _rev_or(repo, "origin/main", head)
        _publish(
            repo,
            main_commit=main_commit,
            last_good=last_good,
            result="pass"
            if _verify_passes(repo, main_commit=main_commit, last_good=last_good)
            else "fail",
        )
        return 0

    target = _rev(repo, "origin/main")
    head = _rev(repo, "HEAD")

    # Nothing to do when the target is already the running good commit, or a
    # commit we have already seen fail here. --force overrides this.
    if not args.force and (target == head == last_good or target == failed):
        _publish(
            repo,
            main_commit=target,
            last_good=last_good,
            result="pass"
            if _verify_passes(repo, main_commit=target, last_good=last_good)
            else "fail",
        )
        return 0

    # Try the target: check it out, apply it, and verify it.
    _git(repo, "checkout", "--quiet", target)
    if _apply(repo) and _verify_passes(repo, main_commit=target, last_good=last_good):
        _write_state(_state_path(), last_good=target, failed=None)
        _publish(repo, main_commit=target, last_good=target, result="pass")
        return 0

    # The target is bad. Record it so we do not retry it until main moves.
    _write_state(_state_path(), last_good=last_good, failed=target)

    # With no earlier good commit there is nothing to fall back to; stay put and
    # report the failure.
    if last_good is None or last_good == target:
        _publish(repo, main_commit=target, last_good=last_good, result="fail")
        return 1

    # Fall back to the last good commit and apply it.
    _git(repo, "checkout", "--quiet", last_good)
    recovered = _apply(repo) and _verify_passes(
        repo, main_commit=target, last_good=last_good
    )
    _publish(
        repo,
        main_commit=target,
        last_good=last_good,
        result="pass" if recovered else "fail",
    )
    return 0 if recovered else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
