"""Issue #72: a self-update fallback must leave exactly the checks the applied
checkout ships, never a check only the failed commit installed.

The incident: a commit added /etc/snaphouse/verify.d/artadmin.sh, its first
verify failed on a startup race, and the self-update rolled back to the last
good commit. But the Ansible copy that installs a check never removes one, so
artadmin.sh stayed behind. The last good commit then failed verify on a check it
never shipped, status read verify=fail, and every timer run rolled back again.

These tests drive the whole update algorithm against a local git setup, an
ansible-playbook shim that installs the checkout's checks the way a real apply
does (additively, never removing), and verify checks that pass or fail on their
own. The first test reproduces the leftover-check failure end to end; the rest
pin the pruning helpers against the real repo and the shipped-marker convention.
"""

from __future__ import annotations

import json
import socket
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from snaphouse import update, verify

REPO_ROOT = Path(__file__).resolve().parents[2]
MARKER = verify.MANAGED_MARKER


def git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def rev(ref: str, *, cwd: Path) -> str:
    out = subprocess.run(
        ["git", "rev-parse", ref], cwd=cwd, check=True, capture_output=True, text=True
    )
    return out.stdout.strip()


def read_json(path: Path) -> dict:
    return json.loads(path.read_text())


def _configure(repo: Path) -> None:
    git("config", "user.email", "ci@snaphouse.invalid", cwd=repo)
    git("config", "user.name", "snaphouse ci", cwd=repo)


def _check(name: str, *, passes: bool) -> str:
    """A verify check script carrying the managed marker, passing or failing."""
    body = "exit 0\n" if passes else f'echo "{name} is unhappy" >&2\nexit 1\n'
    return f"#!/usr/bin/env bash\n# {MARKER}\nset -euo pipefail\n{body}"


def _hosts_yaml(host: str, roles: list[str]) -> str:
    listed = "".join(f"      - {role}\n" for role in roles)
    return "---\nsnaphouse_hosts:\n" f"  {host}:\n    roles:\n{listed}"


def _write_role(repo: Path, name: str, check: str, *, passes: bool) -> None:
    """A role that installs one verify check, declared the way the real roles do."""
    tasks = repo / "roles" / name / "tasks"
    tasks.mkdir(parents=True, exist_ok=True)
    (tasks / "main.yml").write_text(
        "---\n"
        f"- name: Install the {name} verify check\n"
        "  ansible.builtin.copy:\n"
        f"    src: verify.d/{check}\n"
        f"    dest: /etc/snaphouse/verify.d/{check}\n"
        "    mode: \"0755\"\n"
    )
    files = repo / "roles" / name / "files" / "verify.d"
    files.mkdir(parents=True, exist_ok=True)
    script = files / check
    script.write_text(_check(check, passes=passes))
    script.chmod(0o755)


@pytest.fixture
def world(tmp_path, monkeypatch):
    host = socket.gethostname()

    origin = tmp_path / "origin.git"
    subprocess.run(
        ["git", "init", "--bare", "-b", "main", str(origin)],
        check=True,
        capture_output=True,
    )

    dev = tmp_path / "dev"
    subprocess.run(["git", "clone", str(origin), str(dev)], check=True, capture_output=True)
    _configure(dev)

    repo = tmp_path / "repo"

    bindir = tmp_path / "bin"
    bindir.mkdir()
    # The shim stands in for `ansible-playbook`. A real apply copies each role's
    # verify check into the verify dir and never removes one, so the shim copies
    # every check the checkout declares and leaves whatever is already there.
    verifydir = tmp_path / "verify.d"
    verifydir.mkdir()
    shim = bindir / "ansible-playbook"
    shim.write_text(
        "#!/usr/bin/env bash\n"
        'find "$SNAPHOUSE_REPO/roles" -path "*/files/verify.d/*" -type f '
        f'-exec cp {{}} "{verifydir}/" \\; 2>/dev/null || true\n'
        "exit 0\n"
    )
    shim.chmod(0o755)

    state = tmp_path / "state.json"
    status = tmp_path / "public" / "status.json"

    monkeypatch.setenv("PATH", f"{bindir}:{__import__('os').environ['PATH']}")
    monkeypatch.setenv("SNAPHOUSE_REPO", str(repo))
    monkeypatch.setenv("SNAPHOUSE_STATE", str(state))
    monkeypatch.setenv("SNAPHOUSE_STATUS", str(status))
    monkeypatch.setenv("SNAPHOUSE_VERIFY_DIR", str(verifydir))

    return SimpleNamespace(
        tmp=tmp_path,
        host=host,
        origin=origin,
        dev=dev,
        repo=repo,
        verifydir=verifydir,
        state=state,
        status=status,
    )


def _push(world, *, roles: list[str], extra_role=None, msg: str) -> str:
    """Author a commit: set this host's roles and (optionally) add a role."""
    (world.dev / "vars").mkdir(exist_ok=True)
    (world.dev / "vars" / "hosts.yml").write_text(_hosts_yaml(world.host, roles))
    if extra_role is not None:
        name, check, passes = extra_role
        _write_role(world.dev, name, check, passes=passes)
    git("add", "-A", cwd=world.dev)
    git("commit", "-m", msg, cwd=world.dev)
    git("push", "origin", "main", cwd=world.dev)
    return rev("HEAD", cwd=world.dev)


def test_fallback_removes_a_check_only_the_failed_commit_shipped(world):
    # The good commit ships one role with one passing check.
    _write_role(world.dev, "base", "base.sh", passes=True)
    good = _push(world, roles=["base"], msg="good: base only")
    subprocess.run(
        ["git", "clone", str(world.origin), str(world.repo)],
        check=True,
        capture_output=True,
    )
    _configure(world.repo)

    assert update.main([]) == 0  # base becomes last_good
    assert read_json(world.state)["last_good"] == good
    assert (world.verifydir / "base.sh").exists()

    # The bad commit adds a role whose check fails (the startup-race artadmin).
    bad = _push(
        world,
        roles=["base", "artadmin"],
        extra_role=("artadmin", "artadmin.sh", False),
        msg="bad: adds a failing artadmin check",
    )

    rc = update.main([])

    # The machine must end on the good commit, healthy, with the failed commit's
    # check gone. Before the fix it stayed installed and verify kept failing.
    assert rev("HEAD", cwd=world.repo) == good
    state = read_json(world.state)
    assert state["last_good"] == good
    assert state["failed"] == bad
    assert not (world.verifydir / "artadmin.sh").exists()
    assert (world.verifydir / "base.sh").exists()
    assert read_json(world.status)["verify"] == "pass"
    assert rc == 0


def test_expected_checks_reads_the_real_host_inventory():
    office = update.expected_checks(REPO_ROOT, "office")
    assert office == {"web.sh", "selfupdate.sh", "snapclient.sh"}
    assert "artadmin.sh" not in office

    house = update.expected_checks(REPO_ROOT, "house")
    assert {"artadmin.sh", "art_share.sh", "bt_speaker.sh"} <= house


def test_prune_removes_marked_unexpected_and_keeps_hand_placed(tmp_path, monkeypatch):
    verifydir = tmp_path / "verify.d"
    verifydir.mkdir()
    # Expected for office, keep it.
    (verifydir / "web.sh").write_text(_check("web.sh", passes=True))
    # Managed but not shipped to office: a leftover, prune it.
    (verifydir / "artadmin.sh").write_text(_check("artadmin.sh", passes=True))
    # A file a human dropped in by hand, no marker: never touch it.
    (verifydir / "mine.sh").write_text("#!/usr/bin/env bash\nexit 0\n")
    monkeypatch.setenv("SNAPHOUSE_VERIFY_DIR", str(verifydir))

    removed = update.prune_verify_dir(REPO_ROOT, "office")

    assert removed == ["artadmin.sh"]
    assert not (verifydir / "artadmin.sh").exists()
    assert (verifydir / "web.sh").exists()
    assert (verifydir / "mine.sh").exists()


def test_every_shipped_check_carries_the_marker():
    checks = sorted((REPO_ROOT / "roles").glob("*/files/verify.d/*"))
    templates = sorted((REPO_ROOT / "roles").glob("*/templates/*verify*.j2"))
    assert checks, "no shipped checks found"
    for path in checks + templates:
        assert MARKER in path.read_text(), f"{path} is missing the managed marker"
