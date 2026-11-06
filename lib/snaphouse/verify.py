"""snaphouse verify: run this machine's on-device health checks.

Each role drops an executable check into /etc/snaphouse/verify.d (for example the
web role's web.sh, which confirms nginx serves the status file). `snaphouse
verify` runs every executable in that directory in sorted order, each with a
short timeout, and prints one line per check:

    PASS <name>
    FAIL <name>: <last line the check wrote to stderr>

It exits 0 only when every check passes. The self-update wrapper calls
run_checks directly to decide whether an applied commit is healthy; a human runs
`snaphouse verify` to see the same result.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

# The directory every role installs its checks into. Overridable with
# SNAPHOUSE_VERIFY_DIR so the tests can point at a temporary directory.
DEFAULT_VERIFY_DIR = Path("/etc/snaphouse/verify.d")

# Each check gets this many seconds before it is treated as a failure. A wedged
# check must not hang the whole update.
DEFAULT_TIMEOUT = 60.0

# One result per check: its file name, whether it passed, and a short detail
# (the last stderr line, or a note) shown only on failure.
CheckResult = tuple[str, bool, str]


def verify_dir() -> Path:
    """The directory to read checks from (SNAPHOUSE_VERIFY_DIR, else the default)."""
    return Path(os.environ.get("SNAPHOUSE_VERIFY_DIR", str(DEFAULT_VERIFY_DIR)))


def _checks(directory: Path) -> list[Path]:
    """The executable check files in directory, sorted by name."""
    if not directory.is_dir():
        return []
    return sorted(
        entry
        for entry in directory.iterdir()
        if entry.is_file() and os.access(entry, os.X_OK)
    )


def _last_stderr_line(stderr: str) -> str:
    lines = stderr.strip().splitlines()
    return lines[-1].strip() if lines else ""


def run_checks(directory: Path, timeout: float = DEFAULT_TIMEOUT) -> list[CheckResult]:
    """Run every executable check in directory and return a result per check.

    A check passes when it exits 0. On a non-zero exit the detail is the last
    line it wrote to stderr; on a timeout it is a note that the check timed out.
    """
    results: list[CheckResult] = []
    for check in _checks(directory):
        try:
            completed = subprocess.run(
                [str(check)],
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            results.append((check.name, False, f"timed out after {timeout:g}s"))
            continue
        if completed.returncode == 0:
            results.append((check.name, True, ""))
        else:
            results.append((check.name, False, _last_stderr_line(completed.stderr)))
    return results


def format_line(name: str, ok: bool, detail: str) -> str:
    """The single line printed for one check result."""
    if ok:
        return f"PASS {name}"
    return f"FAIL {name}: {detail}" if detail else f"FAIL {name}"


def all_pass(results: list[CheckResult]) -> bool:
    """True when every check passed (vacuously true with no checks)."""
    return all(ok for _, ok, _ in results)


def main(argv: list[str] | None = None) -> int:
    results = run_checks(verify_dir())
    for name, ok, detail in results:
        print(format_line(name, ok, detail))
    return 0 if all_pass(results) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
