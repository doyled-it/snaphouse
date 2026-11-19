"""Ente sync runner: fake CLI runner, mirror layout and status record."""

import io
import json
import os
import stat
import subprocess
import zipfile
from pathlib import Path

import pytest

from snaphouse import enterun

SECRET = "secret@example.test"
ACCOUNT = {
    "id": "1",
    "name": "house",
    "email": "person@example.test",
    "host": "api.example.test",
    "apps": ["photos"],
    "selected": True,
}


def fmeta(fid, *, name="a.jpg", type_="image", updated=10, hash_="h1"):
    return {"id": fid, "name": name, "type": type_, "updatedAt": updated, "hash": hash_}


def album(aid, name="Album One", visibility="visible", updated=7):
    return {
        "id": aid,
        "name": name,
        "ownerId": "9",
        "type": "normal",
        "updatedAt": updated,
        "visibility": visibility,
    }


def still_zip(members):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in members.items():
            z.writestr(name, data)
    return buf.getvalue()


class FakeCli:
    """Serves canned CLI output and writes bytes to the --output path."""

    def __init__(self, *, accounts=None, albums=None, files=None, payloads=None):
        self.accounts = [ACCOUNT] if accounts is None else accounts
        self.albums = albums if albums is not None else [album("101")]
        self.files = files if files is not None else {"101": [fmeta("5001")]}
        self.payloads = payloads or {}
        self.fail = set()  # keys: "account", "albums", "files:<aid>", "dl:<fid>"
        self.calls = []
        self.envs = []
        self.outputs = []

    def __call__(self, argv, **kw):
        self.calls.append(list(argv))
        self.envs.append(kw.get("env"))
        rest = argv[3:]
        if rest[:2] == ["account", "list"]:
            return self._reply("account", self.accounts)
        if rest[:3] == ["photos", "album", "list"]:
            return self._reply("albums", self.albums)
        if rest[:3] == ["photos", "file", "list"]:
            aid = rest[rest.index("--album") + 1]
            return self._reply(f"files:{aid}", self.files.get(aid, []))
        if rest[:3] == ["photos", "file", "download"]:
            fid = rest[3]
            out = Path(rest[rest.index("--output") + 1])
            self.outputs.append(out)
            assert not out.exists()
            if f"dl:{fid}" in self.fail:
                out.write_bytes(b"partial")
                return subprocess.CompletedProcess(argv, 1, "", f"boom {SECRET}")
            out.write_bytes(self.payloads.get(fid, b"JPEGDATA"))
            return subprocess.CompletedProcess(argv, 0, "{}", "")
        raise AssertionError(f"unexpected argv {argv}")

    def _reply(self, key, data):
        if key in self.fail:
            return subprocess.CompletedProcess([], 1, "", f"error for {SECRET}")
        return subprocess.CompletedProcess([], 0, json.dumps(data), "")


@pytest.fixture
def env(tmp_path):
    return {
        "cli": Path("/opt/ente"),
        "root": tmp_path / "mirror",
        "home": tmp_path / "home",
        "key": tmp_path / "vault.key",
        "status": tmp_path / "status.json",
    }


def run(env, cli, now=1_000_000.0):
    return enterun.run_export(
        env["cli"],
        env["root"],
        env["home"],
        env["key"],
        env["status"],
        runner=cli,
        clock=lambda: now,
    )


def status(env):
    return json.loads(env["status"].read_text())


def record(env, aid="101"):
    return json.loads((env["root"] / aid / "album.json").read_text())


@pytest.mark.parametrize("accounts", [[], [{"id": "1", "apps": ["locker"]}]])
def test_not_logged_in_writes_not_set_up_and_exits_zero(env, accounts):
    cli = FakeCli(accounts=accounts)
    assert run(env, cli) == 0
    assert status(env)["state"] == "not_set_up"
    assert len(cli.calls) == 1


def test_every_cli_call_has_no_input_json_and_env(env):
    cli = FakeCli()
    run(env, cli)
    assert cli.calls
    for argv in cli.calls:
        assert argv[:3] == ["/opt/ente", "--no-input", "--json"]
    for e in cli.envs:
        assert e["ENTE_CLI_HOME"] == str(env["home"])
        assert e["ENTE_CLI_VAULT_KEY"] == str(env["key"])


def test_file_list_asks_for_image_and_livephoto_only_with_all(env):
    cli = FakeCli()
    run(env, cli)
    assert [
        "/opt/ente", "--no-input", "--json", "photos", "file", "list",
        "--album", "101", "--type", "image", "--type", "livephoto", "--all",
    ] in cli.calls
    assert ["/opt/ente", "--no-input", "--json", "account", "list"] in cli.calls
    assert ["/opt/ente", "--no-input", "--json", "photos", "album", "list"] in cli.calls


def test_first_run_downloads_files_and_writes_album_json(env):
    cli = FakeCli(files={"101": [fmeta("5001", name="Holiday.PNG")]})
    assert run(env, cli) == 0
    assert (env["root"] / "101" / "5001.png").read_bytes() == b"JPEGDATA"
    assert record(env) == {
        "title": "Album One",
        "updated_at": 7,
        "files": {"5001": {"updated_at": 10, "hash": "h1", "ext": ".png"}},
    }
    assert status(env) == {
        "state": "ok",
        "last_success": "1970-01-12T13:46:40Z",
        "error": None,
    }


def test_image_without_known_extension_falls_back_to_jpg(env):
    cli = FakeCli(files={"101": [fmeta("5001", name="noext")]})
    run(env, cli)
    assert (env["root"] / "101" / "5001.jpg").exists()


def test_unchanged_file_is_not_downloaded_again(env):
    run(env, FakeCli())
    cli = FakeCli()
    run(env, cli)
    assert not [c for c in cli.calls if "download" in c]


@pytest.mark.parametrize("change", [{"updated": 11}, {"hash_": "h2"}])
def test_changed_updated_at_or_hash_redownloads(env, change):
    run(env, FakeCli())
    cli = FakeCli(files={"101": [fmeta("5001", **change)]}, payloads={"5001": b"NEW"})
    run(env, cli)
    assert (env["root"] / "101" / "5001.jpg").read_bytes() == b"NEW"
    assert len([c for c in cli.calls if "download" in c]) == 1


def test_file_no_longer_listed_is_deleted_and_record_updated(env):
    run(env, FakeCli(files={"101": [fmeta("5001"), fmeta("5002")]}))
    run(env, FakeCli(files={"101": [fmeta("5002")]}))
    assert not (env["root"] / "101" / "5001.jpg").exists()
    assert (env["root"] / "101" / "5002.jpg").exists()
    assert list(record(env)["files"]) == ["5002"]


def test_unshared_album_directory_removed(env):
    run(env, FakeCli(albums=[album("101"), album("202")], files={"101": [], "202": []}))
    assert (env["root"] / "202").is_dir()
    run(env, FakeCli(albums=[album("101")], files={"101": []}))
    assert not (env["root"] / "202").exists()
    assert (env["root"] / "101").is_dir()


def test_hidden_album_skipped(env):
    cli = FakeCli(albums=[album("101", visibility="Hidden")])
    run(env, cli)
    assert not (env["root"] / "101").exists()
    assert not [c for c in cli.calls if "file" in c]


def test_failed_album_list_deletes_nothing_and_sets_failing(env):
    run(env, FakeCli(files={"101": [fmeta("5001")]}))
    cli = FakeCli()
    cli.fail.add("albums")
    assert run(env, cli, now=2_000_000.0) == 1
    assert (env["root"] / "101" / "5001.jpg").exists()
    st = status(env)
    assert st["state"] == "failing"
    assert st["last_success"] == "1970-01-12T13:46:40Z"


def test_failed_file_list_keeps_that_albums_files_but_syncs_the_others(env):
    run(
        env,
        FakeCli(
            albums=[album("101"), album("202")],
            files={"101": [fmeta("5001")], "202": [fmeta("6001")]},
        ),
    )
    cli = FakeCli(
        albums=[album("101"), album("202")],
        files={"101": [], "202": [fmeta("6001"), fmeta("6002")]},
    )
    cli.fail.add("files:101")
    assert run(env, cli) == 1
    assert (env["root"] / "101" / "5001.jpg").exists()
    assert list(record(env, "101")["files"]) == ["5001"]
    assert (env["root"] / "202" / "6002.jpg").exists()
    assert status(env)["state"] == "failing"


def test_failed_download_leaves_no_partial_file_and_is_retried_next_run(env):
    cli = FakeCli(files={"101": [fmeta("5001"), fmeta("5002")]})
    cli.fail.add("dl:5001")
    assert run(env, cli) == 1
    names = sorted(p.name for p in (env["root"] / "101").iterdir())
    assert names == ["5002.jpg", "album.json"]
    assert list(record(env)["files"]) == ["5002"]
    retry = FakeCli(files={"101": [fmeta("5001"), fmeta("5002")]})
    assert run(env, retry) == 0
    downloads = [c for c in retry.calls if "download" in c]
    assert [c[6] for c in downloads] == ["5001"]
    assert sorted(record(env)["files"]) == ["5001", "5002"]


def test_download_uses_temp_name_then_renames(env):
    cli = FakeCli()
    run(env, cli)
    (out,) = cli.outputs
    assert out.parent == env["root"] / "101"
    assert out.name != "5001.jpg"
    assert not out.exists()
    assert (env["root"] / "101" / "5001.jpg").exists()


def test_livephoto_zip_keeps_only_the_still_and_never_reads_the_video(env):
    video = b"VIDEOBYTES" * 10
    payload = still_zip({"a.jpg": b"STILL", "a.MP": video})
    cli = FakeCli(
        files={"101": [fmeta("5001", name="a.zip", type_="livephoto")]},
        payloads={"5001": payload},
    )
    assert run(env, cli) == 0
    d = env["root"] / "101"
    assert sorted(p.name for p in d.iterdir()) == ["5001.jpg", "album.json"]
    assert (d / "5001.jpg").read_bytes() == b"STILL"
    assert record(env)["files"]["5001"]["ext"] == ".jpg"


def test_livephoto_heic_still_gets_a_lowercase_heic_extension(env):
    payload = still_zip({"IMG_1.HEIC": b"HEICBYTES", "IMG_1.MOV": b"v"})
    cli = FakeCli(
        files={"101": [fmeta("5001", name="x", type_="livephoto")]},
        payloads={"5001": payload},
    )
    run(env, cli)
    assert (env["root"] / "101" / "5001.heic").read_bytes() == b"HEICBYTES"
    assert record(env)["files"]["5001"]["ext"] == ".heic"


def test_hostile_zip_member_name_is_refused(env):
    payload = still_zip({"../x.jpg": b"EVIL"})
    cli = FakeCli(
        files={"101": [fmeta("5001", type_="livephoto")]}, payloads={"5001": payload}
    )
    run(env, cli)
    assert not (env["root"] / "x.jpg").exists()
    assert not (env["root"] / "101" / "x.jpg").exists()
    assert "5001" not in record(env)["files"]


def test_zip_without_image_member_is_skipped_not_fatal(env):
    payload = still_zip({"a.MP": b"video"})
    cli = FakeCli(
        files={"101": [fmeta("5001", type_="livephoto"), fmeta("5002")]},
        payloads={"5001": payload},
    )
    assert run(env, cli) == 0
    d = env["root"] / "101"
    assert sorted(p.name for p in d.iterdir()) == ["5002.jpg", "album.json"]
    assert list(record(env)["files"]) == ["5002"]
    assert status(env)["state"] == "ok"


def test_extension_change_removes_the_old_extension_file(env):
    run(env, FakeCli(files={"101": [fmeta("5001", name="a.jpg")]}))
    cli = FakeCli(files={"101": [fmeta("5001", name="a.png", hash_="h2")]})
    run(env, cli)
    d = env["root"] / "101"
    assert sorted(p.name for p in d.iterdir()) == ["5001.png", "album.json"]
    assert record(env)["files"]["5001"]["ext"] == ".png"


def test_album_json_written_with_completed_files_when_a_download_fails(env):
    run(env, FakeCli(files={"101": [fmeta("5001")]}))
    cli = FakeCli(files={"101": [fmeta("5001"), fmeta("5002"), fmeta("5003")]})
    cli.fail.add("dl:5002")
    run(env, cli)
    assert sorted(record(env)["files"]) == ["5001", "5003"]


def test_album_json_written_even_if_a_download_raises(env):
    run(env, FakeCli(files={"101": [fmeta("5001")]}))
    cli = FakeCli(files={"101": [fmeta("5001"), fmeta("5002"), fmeta("5003")]})
    inner = cli.__call__

    def boom(argv, **kw):
        if argv[6:7] == ["5003"]:
            raise KeyboardInterrupt
        return inner(argv, **kw)

    with pytest.raises(KeyboardInterrupt):
        run(env, boom)
    assert sorted(record(env)["files"]) == ["5001", "5002"]


def test_failed_file_list_writes_no_album_json_for_a_new_album(env):
    cli = FakeCli()
    cli.fail.add("files:101")
    run(env, cli)
    assert not (env["root"] / "101" / "album.json").exists()


def test_stale_temp_files_swept_at_start(env):
    d = env["root"] / "101"
    d.mkdir(parents=True)
    stale = d / ".tmp-5001.part"
    stale.write_bytes(b"x")
    keep = d / "5009.jpg"
    keep.write_bytes(b"x")
    run(env, FakeCli(files={"101": []}))
    assert not stale.exists()
    assert keep.exists()


def test_status_error_is_plain_words_and_never_contains_cli_output(env):
    cli = FakeCli()
    cli.fail.add("dl:5001")
    run(env, cli)
    text = env["status"].read_text()
    assert SECRET not in text
    assert "boom" not in text
    err = status(env)["error"]
    assert isinstance(err, str) and err and err.endswith(".")

    cli2 = FakeCli()
    cli2.fail.add("albums")
    run(env, cli2)
    assert SECRET not in env["status"].read_text()


def test_diff_album_cases():
    rec = {
        "1": {"updated_at": 5, "hash": "a", "ext": ".jpg"},
        "2": {"updated_at": 5, "hash": "a", "ext": ".jpg"},
        "3": {"updated_at": 5, "hash": "a", "ext": ".jpg"},
    }
    remote = [
        fmeta("1", updated=5, hash_="a"),
        fmeta("2", updated=6, hash_="a"),
        fmeta("4"),
        fmeta("bad id!"),
        {"name": "no id"},
    ]
    download, delete = enterun.diff_album(rec, remote)
    assert [f["id"] for f in download] == ["2", "4"]
    assert delete == ["3"]
    assert enterun.diff_album(rec, [fmeta("2", updated=5, hash_="b")])[0][0]["id"] == "2"


def test_pick_image_member_cases():
    pick = enterun.pick_image_member
    assert pick(["a.MP", "a.jpg"]) == "a.jpg"
    assert pick(["a.mp", "B.HEIC"]) == "B.HEIC"
    assert pick(["a.MP"]) is None
    assert pick([]) is None
    for bad in ["../x.jpg", "/x.jpg", "d/x.jpg", "d\\x.jpg", "..jpg", "x..jpg"]:
        assert pick([bad]) is None
    assert pick(["../x.jpg", "ok.png"]) == "ok.png"


def test_is_logged_in_cases():
    ok = enterun.is_logged_in
    assert ok([ACCOUNT])
    assert ok([{"app": "Photos"}])
    assert ok([{"id": "1", "apps": ["Locker", "PHOTOS"]}])
    assert not ok([])
    assert not ok(None)
    assert not ok({"apps": ["photos"]})
    assert not ok([{"apps": ["locker"]}])
    assert not ok([{"apps": []}])
    assert not ok(["photos"])


def test_extract_still_streams_only_the_image_and_respects_the_cap(tmp_path):
    z = tmp_path / "a.zip"
    z.write_bytes(still_zip({"a.MP": b"V" * 50, "a.jpg": b"S" * 20}))
    dest = tmp_path / "out.jpg"
    assert enterun.extract_still(z, dest, max_bytes=100)
    assert dest.read_bytes() == b"S" * 20
    dest2 = tmp_path / "out2.jpg"
    assert not enterun.extract_still(z, dest2, max_bytes=10)
    assert not dest2.exists()
    nz = tmp_path / "n.zip"
    nz.write_bytes(still_zip({"a.MP": b"V"}))
    assert not enterun.extract_still(nz, tmp_path / "o3.jpg", max_bytes=100)
    junk = tmp_path / "junk.zip"
    junk.write_bytes(b"not a zip")
    assert not enterun.extract_still(junk, tmp_path / "o4.jpg", max_bytes=100)


def test_read_status_missing_is_unknown(tmp_path):
    got = enterun.read_status(tmp_path / "none.json", now=1.0)
    assert got == {"state": "unknown", "last_success": None, "error": None}


@pytest.mark.parametrize("text", ["{nope", "[1]", '{"state": 5}', ""])
def test_read_status_garbage_is_unknown(tmp_path, text):
    p = tmp_path / "s.json"
    p.write_text(text)
    assert enterun.read_status(p, now=1.0)["state"] == "unknown"


def test_read_status_stale_ok_becomes_failing(tmp_path):
    p = tmp_path / "s.json"
    enterun.write_status(p, "ok", last_success="1970-01-12T13:46:40Z", error=None)
    fresh = enterun.read_status(p, now=1_000_000.0 + 60)
    assert fresh["state"] == "ok"
    stale = enterun.read_status(p, now=1_000_000.0 + 3601)
    assert stale["state"] == "failing"
    assert stale["error"] == "Ente sync has not run recently."
    assert stale["last_success"] == "1970-01-12T13:46:40Z"


def test_write_status_is_atomic_and_0644(tmp_path):
    p = tmp_path / "sub" / "s.json"
    enterun.write_status(p, "failing", last_success=None, error="Nope.")
    assert stat.S_IMODE(os.stat(p).st_mode) == 0o644
    assert json.loads(p.read_text()) == {
        "state": "failing",
        "last_success": None,
        "error": "Nope.",
    }
    assert [x.name for x in p.parent.iterdir()] == ["s.json"]


def test_main_runs_export_with_argv(env, monkeypatch):
    seen = {}

    def fake(cli, root, home, key, status, **kw):
        seen["args"] = (cli, root, home, key, status)
        return 0

    monkeypatch.setattr(enterun, "run_export", fake)
    argv = [
        "--cli", "/opt/ente", "--mirror-root", "/m", "--home", "/h",
        "--vault-key", "/k", "--status", "/s",
    ]
    assert enterun.main(argv) == 0
    assert seen["args"] == tuple(Path(x) for x in ["/opt/ente", "/m", "/h", "/k", "/s"])
