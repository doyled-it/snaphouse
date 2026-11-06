#!/usr/bin/env bash
# snaphouse bluetooth check: every adapter this host is configured to use is
# present and none of them is left discoverable.
#
# Adapters are named in /etc/snaphouse/local.yml by address (bt_receive_adapter,
# bt_send_adapter) and resolved to the current hciN, since the index can swap
# across reboots. Pairing is opened only for a short window by `snaphouse pair`;
# outside it the adapters must stay closed. Run by `snaphouse verify` from
# /etc/snaphouse/verify.d.
#
# The work is in Python so it can reuse snaphouse.adapters (and read local.yml
# with the same ruamel the CLI uses); the address itself never reaches stderr,
# only the last two octets, so a failure cannot leak the household's addresses.
set -euo pipefail

exec /opt/snaphouse/.venv/bin/python - <<'PY'
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, "/opt/snaphouse/lib")
from ruamel.yaml import YAML

from snaphouse.adapters import AdapterNotFound, resolve_adapter

local = YAML().load(Path("/etc/snaphouse/local.yml").read_text()) or {}

failures = []
for key in ("bt_receive_adapter", "bt_send_adapter"):
    mac = local.get(key)
    if not mac:
        continue
    try:
        hci = resolve_adapter(mac)
    except AdapterNotFound as exc:
        failures.append(str(exc))
        continue
    shown = subprocess.run(
        ["bluetoothctl", "show", mac], capture_output=True, text=True, check=False
    ).stdout
    if "Discoverable: yes" in shown:
        failures.append(f"adapter {hci} is discoverable")

for line in failures:
    print(line, file=sys.stderr)
sys.exit(1 if failures else 0)
PY
