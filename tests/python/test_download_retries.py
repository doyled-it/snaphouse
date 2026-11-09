"""Every release download must use a longer timeout and retry on failure.

Both release downloads pull a GitHub release asset: the pinned uv tarball in the
common role and the pinned snapserver/snapclient .deb in the snapcast_pkg role.
GitHub release assets can be slow to start, and the ansible.builtin.get_url
default is a 10 s timeout with no retries, so a slow start fails the whole apply
(and CI's container-apply, art-share, and nightstand-bootstrap jobs with it).

These checks guard that every get_url task in those two role files raises the
timeout and retries, so a regression back to the bare defaults fails here. They
only read the repo; they do not reach the network.
"""

from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]

# The two role task files that download a GitHub release asset with get_url.
DOWNLOAD_TASK_FILES = (
    REPO_ROOT / "roles" / "common" / "tasks" / "main.yml",
    REPO_ROOT / "roles" / "snapcast_pkg" / "tasks" / "main.yml",
)


def iter_tasks(node):
    """Yield every task mapping in a tasks file, descending into block/always."""
    if isinstance(node, list):
        for item in node:
            yield from iter_tasks(item)
    elif isinstance(node, dict):
        yield node
        for key in ("block", "always", "rescue"):
            if key in node:
                yield from iter_tasks(node[key])


def get_url_tasks():
    found = []
    for path in DOWNLOAD_TASK_FILES:
        with path.open() as handle:
            tasks = yaml.safe_load(handle)
        for task in iter_tasks(tasks):
            if "ansible.builtin.get_url" in task:
                found.append((path, task))
    return found


def test_both_role_files_have_a_get_url_download():
    tasks = get_url_tasks()
    files_with_get_url = {path for path, _ in tasks}
    assert files_with_get_url == set(DOWNLOAD_TASK_FILES), (
        "expected a get_url download in both the common and snapcast_pkg roles; "
        f"found in {sorted(str(p) for p in files_with_get_url)}"
    )


def test_every_get_url_download_uses_a_longer_timeout_and_retries():
    for path, task in get_url_tasks():
        name = task.get("name", "<unnamed>")
        get_url = task["ansible.builtin.get_url"]
        assert get_url.get("timeout") == 60, (
            f"{path.relative_to(REPO_ROOT)}: get_url task {name!r} must set "
            "timeout: 60 so a slow GitHub release start does not fail the apply"
        )
        assert "register" in task, (
            f"{path.relative_to(REPO_ROOT)}: get_url task {name!r} must register "
            "its result so it can be retried"
        )
        register = task["register"]
        assert task.get("retries") == 5, (
            f"{path.relative_to(REPO_ROOT)}: get_url task {name!r} must set "
            "retries: 5"
        )
        assert task.get("delay") == 10, (
            f"{path.relative_to(REPO_ROOT)}: get_url task {name!r} must set "
            "delay: 10 between retries"
        )
        assert task.get("until") == f"{register} is succeeded", (
            f"{path.relative_to(REPO_ROOT)}: get_url task {name!r} must set "
            f"until: {register} is succeeded so it retries until it works"
        )
