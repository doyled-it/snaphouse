"""Write this machine's status file.

The status file is the one cross-repo health signal every machine publishes. Its
schema is pinned in docs/cross-repo-features.md and consumed by the room-picker
page and by nightstand, so the writer holds to that schema exactly and writes
atomically: a reader polling the file sees either the old document or the
complete new one, never a half-written file.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Literal

# This repo is always "snaphouse" in the status file. nightstand writes the same
# schema with its own repo name into its own public directory.
REPO = "snaphouse"


def write_status(
    path: Path,
    *,
    host: str,
    commit: str,
    main_commit: str,
    last_good: str,
    verify: Literal["pass", "fail"],
    now: datetime,
) -> None:
    """Write the status file at path atomically, in the pinned schema.

    The document is built in the cross-repo contract's shape with repo fixed to
    "snaphouse", serialized, written to a temporary file in the same directory,
    flushed to disk, then renamed over path. os.replace is atomic on POSIX, so a
    concurrent reader never sees a partial write.

    verify must be "pass" or "fail", and now must be timezone-aware so checked_at
    carries a UTC offset. Either requirement unmet raises ValueError before any
    file is touched.
    """
    if verify not in ("pass", "fail"):
        raise ValueError(f"verify must be 'pass' or 'fail', got {verify!r}")
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware so checked_at carries an offset")

    path = Path(path)
    document = {
        "host": host,
        "repo": REPO,
        "commit": commit,
        "main_commit": main_commit,
        "last_good": last_good,
        "verify": verify,
        "checked_at": now.isoformat(),
    }
    payload = json.dumps(document, indent=2) + "\n"

    # The temporary file must share a directory with the destination so the
    # rename stays on one filesystem and is therefore atomic.
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f"{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
            # mkstemp creates the file 0600 and os.replace keeps that mode, which
            # would leave status.json unreadable by nginx (www-data). Widen it to
            # 0644 so the web role can serve it.
            os.fchmod(handle.fileno(), 0o644)
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
