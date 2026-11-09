"""Argument parsing, host validation, and hostname setting in bootstrap.sh.

bats is not installed, so these drive bootstrap.sh's shell functions directly:
each test sources the script in a bash subprocess (the script guards its main
flow with a BASH_SOURCE check, so sourcing defines the functions without running
apt-get, git, or the apply) and calls one function against a temp copy of the
repo's real vars/hosts.yml. hostnamectl is stubbed on PATH so no test touches the
machine's actual hostname.

Covered: a valid --host key, an invalid key (clear error that lists the valid
keys), and no argument (unchanged behavior). set_hostname is checked for
idempotency against a temp /etc/hosts.
"""

from __future__ import annotations

import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
BOOTSTRAP = REPO_ROOT / "bootstrap.sh"
HOSTS_FILE = REPO_ROOT / "vars" / "hosts.yml"

EXPECTED_HOST_KEYS = {"house", "office", "nightstand"}


@pytest.fixture()
def hosts_copy(tmp_path: Path) -> Path:
    """A temp copy of the repo's real hosts.yml, so the keys stay in sync."""
    dest = tmp_path / "hosts.yml"
    shutil.copy(HOSTS_FILE, dest)
    return dest


def _hostnamectl_stub(tmp_path: Path) -> Path:
    """A bin dir holding a hostnamectl stub that records its calls.

    The stub answers `--static` with the name of the last `set-hostname`, or
    'stub-initial' before any set, so set_hostname can read the current name.
    """
    bindir = tmp_path / "bin"
    bindir.mkdir()
    state = tmp_path / "hostname.state"
    calls = tmp_path / "hostnamectl.calls"
    stub = bindir / "hostnamectl"
    stub.write_text(
        textwrap.dedent(
            f"""\
            #!/usr/bin/env bash
            echo "$@" >> "{calls}"
            if [ "$1" = "set-hostname" ]; then
                printf '%s' "$2" > "{state}"
                exit 0
            fi
            if [ "$1" = "--static" ]; then
                if [ -f "{state}" ]; then cat "{state}"; else echo "stub-initial"; fi
                exit 0
            fi
            exit 0
            """
        )
    )
    stub.chmod(0o755)
    return bindir


def _run(snippet: str, *, args=(), env=None, cwd: Path | None = None):
    full = f'source "{BOOTSTRAP}"\n{snippet}\n'
    cmd = ["bash", "-c", full, "bootstrap-test", *args]
    return subprocess.run(
        cmd, capture_output=True, text=True, env=env, cwd=cwd, check=False
    )


def test_parse_and_validate_accepts_a_valid_host(hosts_copy: Path):
    result = _run(
        f'parse_args "$@"\nvalidate_host "$target_host" "{hosts_copy}"',
        args=["--host", "office"],
    )
    assert result.returncode == 0, result.stderr


def test_validate_rejects_an_unknown_host_and_lists_the_valid_ones(hosts_copy: Path):
    result = _run(f'validate_host "nope" "{hosts_copy}"')
    assert result.returncode != 0
    assert "nope" in result.stderr
    for key in EXPECTED_HOST_KEYS:
        assert key in result.stderr, f"error should list valid host {key!r}"


def test_no_argument_leaves_the_target_host_empty():
    result = _run('parse_args "$@"\nprintf "[%s]" "$target_host"')
    assert result.returncode == 0, result.stderr
    assert result.stdout == "[]"


def _self_update_fixtures(tmp_path: Path, *, apply_rc: int):
    """A systemctl stub that records its calls and a fake repo whose
    bin/snaphouse exits with apply_rc. Returns (env, calls_file, repo_dir)."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    calls = tmp_path / "systemctl.calls"
    systemctl = bindir / "systemctl"
    systemctl.write_text(
        textwrap.dedent(
            f"""\
            #!/usr/bin/env bash
            echo "$@" >> "{calls}"
            exit 0
            """
        )
    )
    systemctl.chmod(0o755)

    repo_dir = tmp_path / "repo"
    (repo_dir / "bin").mkdir(parents=True)
    snaphouse = repo_dir / "bin" / "snaphouse"
    snaphouse.write_text(
        textwrap.dedent(
            f"""\
            #!/usr/bin/env bash
            echo "fake snaphouse $*" >&2
            exit {apply_rc}
            """
        )
    )
    snaphouse.chmod(0o755)

    env = {"PATH": f"{bindir}:/usr/bin:/bin"}
    return env, calls, repo_dir


def test_first_apply_failure_still_enables_timer_and_exits_nonzero(tmp_path: Path):
    env, calls, repo_dir = _self_update_fixtures(tmp_path, apply_rc=1)
    result = _run(f'repo_dir="{repo_dir}"\nstart_self_update', env=env)

    assert result.returncode != 0, result.stdout
    assert calls.exists(), "systemctl was never called"
    assert "enable --now snaphouse-update.timer" in calls.read_text()


def test_first_apply_success_enables_timer_and_exits_zero(tmp_path: Path):
    env, calls, repo_dir = _self_update_fixtures(tmp_path, apply_rc=0)
    result = _run(f'repo_dir="{repo_dir}"\nstart_self_update', env=env)

    assert result.returncode == 0, result.stderr
    assert "enable --now snaphouse-update.timer" in calls.read_text()


def test_set_hostname_is_idempotent_in_etc_hosts(tmp_path: Path):
    bindir = _hostnamectl_stub(tmp_path)
    etc_hosts = tmp_path / "hosts"
    etc_hosts.write_text("127.0.0.1\tlocalhost\n127.0.1.1\tcasaos\n")
    env = {
        "PATH": f"{bindir}:/usr/bin:/bin",
        "ETC_HOSTS": str(etc_hosts),
    }
    snippet = 'set_hostname house\nset_hostname house'
    result = _run(snippet, env=env)
    assert result.returncode == 0, result.stderr

    lines = etc_hosts.read_text().splitlines()
    mapped = [ln for ln in lines if ln.startswith("127.0.1.1")]
    assert mapped == ["127.0.1.1\thouse"], lines
    # The loopback line is untouched.
    assert "127.0.0.1\tlocalhost" in lines
