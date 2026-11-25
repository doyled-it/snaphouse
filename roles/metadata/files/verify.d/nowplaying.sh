#!/usr/bin/env bash
# snaphouse-managed-check: self-update prunes this when the applied checkout drops it
# snaphouse now-playing check: the service is active, and now-playing.json exists
# and parses as JSON with a track key, and cover-archive.json parses with a
# covers list. The service writes that file at startup
# (before snapserver answers), so it is present even when nothing is playing. Run
# by `snaphouse verify` from /etc/snaphouse/verify.d on a real machine.
set -euo pipefail

systemctl is-active --quiet snaphouse-nowplaying.service

python3 - <<'PY'
import json

with open("/var/lib/snaphouse/public/nowplaying/now-playing.json", encoding="utf-8") as handle:
    doc = json.load(handle)
assert isinstance(doc, dict), "now-playing.json is not a JSON object"
assert "track" in doc, "now-playing.json has no track key"

# The cover collection index is written at service start too.
with open("/var/lib/snaphouse/public/nowplaying/cover-archive.json", encoding="utf-8") as handle:
    archive = json.load(handle)
assert isinstance(archive.get("covers"), list), "cover-archive.json has no covers list"
PY
