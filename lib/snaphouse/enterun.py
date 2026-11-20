"""Sync Ente Photos albums into the read-only export mirror.

The Ente CLI is called only through an injected runner. Each shared album is
listed, diffed against its `album.json` record, and new or changed pictures are
downloaded under a temporary name then renamed into `<fileId>.<ext>`. A Live
Photo arrives as a ZIP: only its still image is kept and the video is never
read. The status record says in plain words how the last run went; raw CLI
output and account details never reach it.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from snaphouse.enteexport import ENTE_EXTENSIONS, ENTE_ID_RE

MAX_IMAGE_BYTES = 64 * 1024 * 1024
CLI_TIMEOUT = 900
STALE_AFTER = 3600.0
_TMP_PREFIX = ".tmp-"
_ALBUM_JSON = "album.json"
_ISO = "%Y-%m-%dT%H:%M:%SZ"
# The Ente CLI writes pictures 0600; the art service reads them as group `ente`.
PICTURE_MODE = 0o640

STALE_MESSAGE = "Ente sync has not run recently."
ERR_KEY = "Could not read the Ente key."
ERR_ACCOUNT = "Could not check the Ente account."
ERR_ALBUMS = "Could not list the shared albums."
ERR_FILES = "Could not list the pictures in some albums."
ERR_DOWNLOAD = "Could not download some pictures."


def _is_id(value: object) -> bool:
    return isinstance(value, str) and ENTE_ID_RE.fullmatch(value) is not None


# Status record -------------------------------------------------------------


def read_status(path: Path, *, now: float, stale_after: float = STALE_AFTER) -> dict:
    unknown = {"state": "unknown", "last_success": None, "error": None}
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return unknown
    if not isinstance(data, dict) or data.get("state") not in (
        "ok", "failing", "not_set_up", "unknown",
    ):
        return unknown
    last = data.get("last_success")
    error = data.get("error")
    result = {
        "state": data["state"],
        "last_success": last if isinstance(last, str) else None,
        "error": error if isinstance(error, str) else None,
    }
    if result["state"] == "ok":
        try:
            when = datetime.strptime(result["last_success"], _ISO)
        except (TypeError, ValueError):
            return unknown
        if now - when.replace(tzinfo=timezone.utc).timestamp() > stale_after:
            result["state"] = "failing"
            result["error"] = STALE_MESSAGE
    return result


def _atomic_write(path: Path, text: str, mode: int = 0o644) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=_TMP_PREFIX, suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.chmod(tmp_name, mode)
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def write_status(
    path: Path, state: str, *, last_success: str | None, error: str | None
) -> None:
    body = {"state": state, "last_success": last_success, "error": error}
    _atomic_write(path, json.dumps(body, indent=2) + "\n")


# Pure helpers ----------------------------------------------------------------


def _mentions_photos(value: object) -> bool:
    if isinstance(value, str):
        return value.casefold() == "photos"
    if isinstance(value, list):
        return any(isinstance(v, str) and v.casefold() == "photos" for v in value)
    return False


def is_logged_in(accounts: object) -> bool:
    if not isinstance(accounts, list):
        return False
    return any(
        isinstance(entry, dict) and any(_mentions_photos(v) for v in entry.values())
        for entry in accounts
    )


def wanted_albums(albums: list[dict]) -> list[dict]:
    wanted = []
    for entry in albums:
        if not isinstance(entry, dict) or not _is_id(entry.get("id")):
            continue
        visibility = entry.get("visibility")
        if isinstance(visibility, str) and visibility.casefold() == "hidden":
            continue
        wanted.append(entry)
    return wanted


def diff_album(
    record_files: dict[str, dict], remote: list[dict]
) -> tuple[list[dict], list[str]]:
    to_download = []
    remote_ids = set()
    for entry in remote:
        if not isinstance(entry, dict) or not _is_id(entry.get("id")):
            continue
        fid = entry["id"]
        remote_ids.add(fid)
        known = record_files.get(fid)
        if (
            not isinstance(known, dict)
            or known.get("updated_at") != entry.get("updatedAt")
            or known.get("hash") != entry.get("hash")
        ):
            to_download.append(entry)
    to_delete = [fid for fid in record_files if fid not in remote_ids]
    return to_download, to_delete


def pick_image_member(names: list[str]) -> str | None:
    for name in names:
        if not isinstance(name, str) or not name:
            continue
        if "/" in name or "\\" in name or ".." in name:
            continue
        if Path(name).suffix.lower() in ENTE_EXTENSIONS:
            return name
    return None


def extract_still(zip_path: Path, dest: Path, *, max_bytes: int) -> bool:
    try:
        with zipfile.ZipFile(zip_path) as archive:
            member = pick_image_member(archive.namelist())
            if member is None:
                return False
            written = 0
            try:
                with archive.open(member) as src, open(dest, "wb") as out:
                    while True:
                        chunk = src.read(65536)
                        if not chunk:
                            break
                        written += len(chunk)
                        if written > max_bytes:
                            raise ValueError("over the size cap")
                        out.write(chunk)
            except BaseException:
                Path(dest).unlink(missing_ok=True)
                raise
    except (OSError, ValueError, zipfile.BadZipFile, RuntimeError, NotImplementedError):
        return False
    return True


# The run -------------------------------------------------------------------


class _Cli:
    def __init__(self, cli: Path, home: Path, vault_key: str, runner) -> None:
        self.cli = str(cli)
        self.runner = runner
        self.env = dict(os.environ)
        self.env["ENTE_CLI_HOME"] = str(home)
        self.env["ENTE_CLI_VAULT_KEY"] = vault_key

    def call(self, *args: str):
        """Return the completed process, or None when it could not run."""
        argv = [self.cli, "--no-input", "--json", *args]
        try:
            done = self.runner(
                argv, capture_output=True, text=True, env=self.env, timeout=CLI_TIMEOUT
            )
        except (OSError, subprocess.SubprocessError):
            return None
        return done if done.returncode == 0 else None

    def json(self, *args: str):
        done = self.call(*args)
        if done is None:
            return None
        try:
            return json.loads(done.stdout)
        except (TypeError, ValueError):
            return None


def _sweep_temps(root: Path) -> None:
    try:
        children = list(root.iterdir())
    except OSError:
        return
    for child in children:
        if child.is_symlink() or not child.is_dir():
            continue
        try:
            for entry in child.iterdir():
                if entry.name.startswith(_TMP_PREFIX) and entry.is_file():
                    entry.unlink(missing_ok=True)
        except OSError:
            continue


def _read_files_record(album_dir: Path) -> dict:
    try:
        data = json.loads((album_dir / _ALBUM_JSON).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    files = data.get("files") if isinstance(data, dict) else None
    return dict(files) if isinstance(files, dict) else {}


def _int(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _fetch_one(cli: _Cli, album_id: str, album_dir: Path, entry: dict) -> str | None:
    """Download one picture into `<fileId>.<ext>` and return the ext.

    Raises `_Failed` when the CLI failed and `_Skip` for a picture that is not
    an error (a Live Photo ZIP with no usable still image).
    """
    fid = entry["id"]
    part = album_dir / f"{_TMP_PREFIX}{fid}.part"
    still = album_dir / f"{_TMP_PREFIX}{fid}.still"
    try:
        done = cli.call(
            "photos", "file", "download", fid,
            "--album", album_id, "--output", str(part),
        )
        if done is None or not part.is_file():
            raise _Failed
        if entry.get("type") == "livephoto":
            try:
                with zipfile.ZipFile(part) as archive:
                    member = pick_image_member(archive.namelist())
            except (OSError, zipfile.BadZipFile):
                raise _Skip from None
            ext = Path(member).suffix.lower() if member else ""
            if ext not in ENTE_EXTENSIONS:
                raise _Skip
            if not extract_still(part, still, max_bytes=MAX_IMAGE_BYTES):
                raise _Skip
            final_src = still
        else:
            name = entry.get("name")
            ext = Path(name).suffix.lower() if isinstance(name, str) else ""
            if ext not in ENTE_EXTENSIONS:
                ext = ".jpg"
            final_src = part
        os.chmod(final_src, PICTURE_MODE)
        os.replace(final_src, album_dir / f"{fid}{ext}")
        return ext
    finally:
        part.unlink(missing_ok=True)
        still.unlink(missing_ok=True)


class _Failed(Exception):
    pass


class _Skip(Exception):
    pass


def _sync_album(cli: _Cli, root: Path, album: dict) -> str | None:
    """Sync one album. Return None, or "files" or "download" for what failed."""
    album_id = album["id"]
    remote = cli.json(
        "photos", "file", "list", "--album", album_id,
        "--type", "image", "--type", "livephoto", "--all",
    )
    if not isinstance(remote, list):
        return "files"
    album_dir = root / album_id
    album_dir.mkdir(parents=True, exist_ok=True)
    files = _read_files_record(album_dir)
    to_download, to_delete = diff_album(files, remote)
    ok = True
    try:
        for entry in to_download:
            try:
                ext = _fetch_one(cli, album_id, album_dir, entry)
            except _Failed:
                ok = False
                continue
            except _Skip:
                continue
            old = files.get(entry["id"])
            old_ext = old.get("ext") if isinstance(old, dict) else None
            if isinstance(old_ext, str) and old_ext != ext:
                (album_dir / f"{entry['id']}{old_ext}").unlink(missing_ok=True)
            files[entry["id"]] = {
                "updated_at": entry.get("updatedAt"),
                "hash": entry.get("hash"),
                "ext": ext,
            }
        for fid in to_delete:
            old = files.pop(fid, None)
            if _is_id(fid) and isinstance(old, dict) and isinstance(old.get("ext"), str):
                (album_dir / f"{fid}{old['ext']}").unlink(missing_ok=True)
    finally:
        title = album.get("name")
        body = {
            "title": title if isinstance(title, str) else "",
            "updated_at": _int(album.get("updatedAt")),
            "files": files,
        }
        _atomic_write(album_dir / _ALBUM_JSON, json.dumps(body, indent=2) + "\n")
    return None if ok else "download"


def _heal_modes(album_dir: Path) -> None:
    """Give pictures the CLI left unreadable to the group the group-read bit.

    Regular files only: a symlink is never followed or changed. One failure
    never stops the run.
    """
    try:
        children = list(album_dir.iterdir())
    except OSError:
        return
    for child in children:
        if child.suffix.lower() not in ENTE_EXTENSIONS:
            continue
        try:
            info = child.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_mode & stat.S_IRGRP:
                continue
            os.chmod(child, PICTURE_MODE)
        except OSError:
            continue


def _remove_unwanted(root: Path, wanted_ids: set[str]) -> None:
    try:
        children = list(root.iterdir())
    except OSError:
        return
    for child in children:
        if child.name in wanted_ids or not _is_id(child.name):
            continue
        if child.is_symlink():
            child.unlink(missing_ok=True)
        elif child.is_dir():
            shutil.rmtree(child, ignore_errors=True)


def run_export(
    cli: Path,
    mirror_root: Path,
    home: Path,
    vault_key: Path,
    status: Path,
    *,
    runner=subprocess.run,
    clock=time.time,
) -> int:
    root = Path(mirror_root)
    previous = read_status(status, now=clock(), stale_after=float("inf"))
    last_success = previous["last_success"]

    def fail(message: str) -> int:
        write_status(status, "failing", last_success=last_success, error=message)
        return 1

    try:
        key = Path(vault_key).read_text(encoding="utf-8").strip()
    except (OSError, ValueError):
        key = ""
    if not key:
        return fail(ERR_KEY)
    ente = _Cli(cli, home, key, runner)

    _sweep_temps(root)
    accounts = ente.json("account", "list")
    if accounts is None:
        return fail(ERR_ACCOUNT)
    if not is_logged_in(accounts):
        write_status(status, "not_set_up", last_success=last_success, error=None)
        return 0
    albums = ente.json("photos", "album", "list")
    if not isinstance(albums, list):
        return fail(ERR_ALBUMS)

    root.mkdir(parents=True, exist_ok=True)
    wanted = wanted_albums(albums)
    _remove_unwanted(root, {a["id"] for a in wanted})
    problems: set[str] = set()
    for entry in wanted:
        problem = _sync_album(ente, root, entry)
        _heal_modes(root / entry["id"])
        if problem:
            problems.add(problem)
    if problems:
        return fail(ERR_FILES if "files" in problems else ERR_DOWNLOAD)
    write_status(status, "ok", last_success=_iso(clock()), error=None)
    return 0


def _iso(epoch: float) -> str:
    return time.strftime(_ISO, time.gmtime(epoch))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Sync Ente albums into the mirror.")
    for flag in ("cli", "mirror-root", "home", "vault-key", "status"):
        parser.add_argument(f"--{flag}", type=Path, required=True)
    args = parser.parse_args(argv)
    return run_export(
        args.cli, args.mirror_root, args.home, args.vault_key, args.status
    )


if __name__ == "__main__":
    sys.exit(main())
