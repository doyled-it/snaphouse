#!/usr/bin/env bash
# snaphouse now-playing check: the service is active, and now-playing.json exists
# and parses as JSON with a track key. The service writes that file at startup
# (before snapserver answers), so it is present even when nothing is playing. Run
# by `snaphouse verify` from /etc/snaphouse/verify.d on a real machine.
set -euo pipefail

systemctl is-active --quiet snaphouse-nowplaying.service

python3 - <<'PY'
import json

with open("/var/lib/snaphouse/public/now-playing.json", encoding="utf-8") as handle:
    doc = json.load(handle)
assert isinstance(doc, dict), "now-playing.json is not a JSON object"
assert "track" in doc, "now-playing.json has no track key"
PY
