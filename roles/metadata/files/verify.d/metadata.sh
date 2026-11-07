#!/usr/bin/env bash
# snaphouse metadata check: the Bluetooth AVRCP control script is executable and
# runs under the on-device venv, which must carry its D-Bus dependency (jeepney).
# Driving it with an empty event stream exercises the Snapcast handshake without
# any hardware or snapserver: it must announce Plugin.Stream.Ready. Run by
# `snaphouse verify` from /etc/snaphouse/verify.d.
set -euo pipefail

script=/opt/snaphouse/lib/snaphouse/avrcp_meta.py
test -x "${script}"

PYTHONPATH=/opt/snaphouse/lib /opt/snaphouse/.venv/bin/python - <<'PY'
import io
import json

import jeepney  # noqa: F401  the runtime D-Bus dependency must be installed

from snaphouse import avrcp_meta

out = io.StringIO()
avrcp_meta.serve(avrcp_meta.Player(), [], out)
first = json.loads(out.getvalue().splitlines()[0])
assert first == {"jsonrpc": "2.0", "method": "Plugin.Stream.Ready"}, first
PY
