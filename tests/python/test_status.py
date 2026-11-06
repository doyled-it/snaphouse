"""Tests for snaphouse.status.write_status.

The status file is the one cross-repo health signal every machine publishes. Its
schema is pinned in docs/cross-repo-features.md and read by the room-picker page
and by nightstand. These tests hold the writer to that contract: the exact set of
keys with string values, repo fixed to "snaphouse", an ISO 8601 checked_at that
carries a UTC offset, and an atomic temp-file-plus-rename write so a polling
reader never sees a half-written document.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from snaphouse import status as status_mod
from snaphouse.status import write_status

# The pinned contract keys, from docs/cross-repo-features.md "Pinned contracts".
CONTRACT_KEYS = {
    "host",
    "repo",
    "commit",
    "main_commit",
    "last_good",
    "verify",
    "checked_at",
}

PACIFIC = timezone(timedelta(hours=-8))


def sample(path: Path, **overrides) -> dict:
    """Write a status file with sensible defaults; return the arguments used."""
    args = dict(
        host="pi-office",
        commit="a" * 40,
        main_commit="b" * 40,
        last_good="c" * 40,
        verify="pass",
        now=datetime(2026, 11, 6, 21, 15, 0, tzinfo=PACIFIC),
    )
    args.update(overrides)
    write_status(path, **args)
    return args


def read(path: Path) -> dict:
    return json.loads(path.read_text())


def test_has_exactly_the_contract_keys(tmp_path):
    path = tmp_path / "status.json"
    sample(path)
    assert set(read(path)) == CONTRACT_KEYS


def test_all_values_are_strings(tmp_path):
    path = tmp_path / "status.json"
    sample(path)
    assert all(isinstance(value, str) for value in read(path).values())


def test_written_file_is_readable_by_group_and_other(tmp_path):
    # nginx (www-data) must be able to serve status.json; mkstemp's default 0600
    # would hide it, so the writer widens the mode.
    path = tmp_path / "status.json"
    sample(path)
    mode = path.stat().st_mode
    assert mode & 0o044 == 0o044, oct(mode)


def test_repo_is_always_snaphouse(tmp_path):
    path = tmp_path / "status.json"
    sample(path)
    assert read(path)["repo"] == "snaphouse"


def test_values_are_written_verbatim(tmp_path):
    path = tmp_path / "status.json"
    args = sample(path)
    data = read(path)
    assert data["host"] == "pi-office"
    assert data["commit"] == args["commit"]
    assert data["main_commit"] == args["main_commit"]
    assert data["last_good"] == args["last_good"]


@pytest.mark.parametrize("verify", ["pass", "fail"])
def test_verify_value_is_recorded(tmp_path, verify):
    path = tmp_path / "status.json"
    sample(path, verify=verify)
    assert read(path)["verify"] == verify


def test_checked_at_is_iso8601_with_offset(tmp_path):
    path = tmp_path / "status.json"
    sample(path)
    checked_at = read(path)["checked_at"]
    parsed = datetime.fromisoformat(checked_at)
    assert parsed.utcoffset() is not None
    assert parsed.utcoffset() == timedelta(hours=-8)
    assert checked_at.endswith("-08:00")


def test_rejects_naive_datetime(tmp_path):
    path = tmp_path / "status.json"
    with pytest.raises(ValueError):
        write_status(
            path,
            host="pi-office",
            commit="a" * 40,
            main_commit="b" * 40,
            last_good="c" * 40,
            verify="pass",
            now=datetime(2026, 11, 6, 21, 15, 0),
        )
    assert not path.exists()


def test_rejects_unknown_verify_value(tmp_path):
    path = tmp_path / "status.json"
    with pytest.raises(ValueError):
        sample(path, verify="maybe")
    assert not path.exists()


def test_no_leftover_temp_files(tmp_path):
    path = tmp_path / "status.json"
    sample(path)
    assert [entry.name for entry in tmp_path.iterdir()] == ["status.json"]


def test_write_is_atomic_rename_within_the_same_dir(tmp_path, monkeypatch):
    """The write renames a complete temp file over the destination.

    A reader that polls the file must see either the previous complete document
    or the new complete document, never a partial write. We prove it by spying on
    os.replace: when it is called the temp source already holds the full new JSON,
    it lives in the same directory as the destination (so the rename is atomic),
    and the destination still holds the previous complete document.
    """
    import snaphouse.status as status_mod

    path = tmp_path / "status.json"
    sample(path)
    before = path.read_text()

    real_replace = status_mod.os.replace
    seen: dict[str, Path] = {}

    def spy_replace(src, dst, *args, **kwargs):
        src_path, dst_path = Path(src), Path(dst)
        seen["src"], seen["dst"] = src_path, dst_path
        assert src_path.parent == dst_path.parent
        assert src_path != dst_path
        temp_document = json.loads(src_path.read_text())
        assert set(temp_document) == CONTRACT_KEYS
        assert path.read_text() == before
        return real_replace(src, dst, *args, **kwargs)

    monkeypatch.setattr(status_mod.os, "replace", spy_replace)
    sample(path, commit="d" * 40)

    assert seen["dst"] == path
    assert read(path)["commit"] == "d" * 40


def test_overwriting_leaves_one_complete_file(tmp_path):
    path = tmp_path / "status.json"
    sample(path, commit="a" * 40)
    sample(path, commit="e" * 40)
    assert [entry.name for entry in tmp_path.iterdir()] == ["status.json"]
    assert read(path)["commit"] == "e" * 40


def test_cli_prints_the_status_file(tmp_path, capsys, monkeypatch):
    path = tmp_path / "status.json"
    sample(path)
    monkeypatch.setenv("SNAPHOUSE_STATUS", str(path))
    rc = status_mod.main([])
    out = capsys.readouterr().out
    assert rc == 0
    assert json.loads(out) == read(path)


def test_cli_reports_a_missing_status_file(tmp_path, capsys, monkeypatch):
    path = tmp_path / "status.json"
    monkeypatch.setenv("SNAPHOUSE_STATUS", str(path))
    rc = status_mod.main([])
    captured = capsys.readouterr()
    assert rc == 1
    assert captured.out == ""
    assert str(path) in captured.err
