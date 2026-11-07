"""Fails if any old machine hostname survives the 2026-11-06 rename.

The three machines were renamed (to house, office, nightstand). This test
greps every git-tracked file, except the historical plans under
docs/superpowers/plans, and fails if an old hostname literal still appears.
The needles are assembled from fragments so this test file never matches
itself.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PLANS_PREFIX = "docs/superpowers/plans/"

# Assembled from fragments so this file is not itself a match.
OLD_HOSTNAMES = ["zima" + "blade", "pi-" + "office", "pi-" + "bedroom"]


def tracked_files() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return [line for line in out.stdout.splitlines() if line]


def test_no_old_hostname_literals_remain():
    offenders = {}
    for rel in tracked_files():
        if rel.startswith(PLANS_PREFIX):
            continue
        path = REPO_ROOT / rel
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, FileNotFoundError, IsADirectoryError):
            continue
        hits = [name for name in OLD_HOSTNAMES if name in text]
        if hits:
            offenders[rel] = hits
    assert not offenders, f"old hostname literals still present: {offenders}"
