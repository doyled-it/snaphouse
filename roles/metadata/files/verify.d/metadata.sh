#!/usr/bin/env bash
# snaphouse-managed-check: self-update prunes this when the applied checkout drops it
# snaphouse metadata check: the two control scripts are executable and run under
# the on-device venv, which must carry their dependencies (jeepney for Bluetooth
# AVRCP over D-Bus; Pillow, the hub-only extra, for the AirPlay cover downscale).
# Driving each with an empty event stream exercises the Snapcast handshake
# without any hardware or snapserver: each must announce Plugin.Stream.Ready. Run
# by `snaphouse verify` from /etc/snaphouse/verify.d.
set -euo pipefail

test -x /opt/snaphouse/lib/snaphouse/avrcp_meta.py
test -x /opt/snaphouse/lib/snaphouse/airplay_meta.py

PYTHONPATH=/opt/snaphouse/lib /opt/snaphouse/.venv/bin/python - <<'PY'
import io
import json

import jeepney  # noqa: F401  the Bluetooth D-Bus dependency must be installed
import PIL  # noqa: F401  the AirPlay cover downscale dependency (hub extra)

from snaphouse import avrcp_meta, airplay_meta

ready = {"jsonrpc": "2.0", "method": "Plugin.Stream.Ready"}
for module, player in (
    (avrcp_meta, avrcp_meta.Player()),
    (airplay_meta, airplay_meta.Player()),
):
    out = io.StringIO()
    module.serve(player, [], out)
    first = json.loads(out.getvalue().splitlines()[0])
    assert first == ready, (module.__name__, first)
PY
