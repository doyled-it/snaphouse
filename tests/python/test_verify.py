"""Tests for snaphouse.verify.

`snaphouse verify` runs every executable in /etc/snaphouse/verify.d (sorted),
each with a 60 second timeout, and reports PASS or FAIL per check. It exits 0
only when every check passes. These tests drive that behaviour against a
temporary verify dir of shell checks, so no real device services are involved.
"""

from __future__ import annotations

from pathlib import Path

from snaphouse import verify


def make_check(directory: Path, name: str, body: str, *, executable: bool = True) -> Path:
    """Write a small shell check into directory and return its path."""
    path = directory / name
    path.write_text("#!/usr/bin/env bash\n" + body + "\n")
    path.chmod(0o755 if executable else 0o644)
    return path


def test_passing_check_reports_pass_with_no_detail(tmp_path):
    make_check(tmp_path, "a.sh", "exit 0")
    assert verify.run_checks(tmp_path) == [("a.sh", True, "")]


def test_failing_check_reports_last_stderr_line(tmp_path):
    make_check(tmp_path, "a.sh", "echo first line >&2; echo 'the real cause' >&2; exit 1")
    assert verify.run_checks(tmp_path) == [("a.sh", False, "the real cause")]


def test_failing_check_with_no_stderr_has_empty_detail(tmp_path):
    make_check(tmp_path, "a.sh", "exit 3")
    assert verify.run_checks(tmp_path) == [("a.sh", False, "")]


def test_checks_run_in_sorted_order(tmp_path):
    make_check(tmp_path, "b.sh", "exit 0")
    make_check(tmp_path, "a.sh", "exit 0")
    make_check(tmp_path, "c.sh", "exit 0")
    names = [name for name, _, _ in verify.run_checks(tmp_path)]
    assert names == ["a.sh", "b.sh", "c.sh"]


def test_non_executable_files_are_ignored(tmp_path):
    make_check(tmp_path, "run.sh", "exit 0")
    make_check(tmp_path, "notes.txt", "exit 1", executable=False)
    names = [name for name, _, _ in verify.run_checks(tmp_path)]
    assert names == ["run.sh"]


def test_timeout_is_a_failure(tmp_path):
    make_check(tmp_path, "slow.sh", "sleep 5")
    name, ok, detail = verify.run_checks(tmp_path, timeout=0.2)[0]
    assert name == "slow.sh"
    assert ok is False
    assert "timed out" in detail


def test_missing_dir_has_no_checks(tmp_path):
    assert verify.run_checks(tmp_path / "does-not-exist") == []


def test_main_reports_every_check_and_exits_zero_when_all_pass(tmp_path, capsys, monkeypatch):
    make_check(tmp_path, "a.sh", "exit 0")
    make_check(tmp_path, "b.sh", "exit 0")
    monkeypatch.setenv("SNAPHOUSE_VERIFY_DIR", str(tmp_path))
    rc = verify.main([])
    out = capsys.readouterr().out
    assert rc == 0
    assert out.splitlines() == ["PASS a.sh", "PASS b.sh"]


def test_main_exits_one_when_any_check_fails(tmp_path, capsys, monkeypatch):
    make_check(tmp_path, "a.sh", "exit 0")
    make_check(tmp_path, "b.sh", "echo broken >&2; exit 1")
    monkeypatch.setenv("SNAPHOUSE_VERIFY_DIR", str(tmp_path))
    rc = verify.main([])
    out = capsys.readouterr().out
    assert rc == 1
    assert out.splitlines() == ["PASS a.sh", "FAIL b.sh: broken"]
