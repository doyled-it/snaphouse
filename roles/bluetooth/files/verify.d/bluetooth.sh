#!/usr/bin/env bash
# snaphouse-managed-check: self-update prunes this when the applied checkout drops it
# snaphouse bluetooth check: every adapter this host is configured to use is
# present and none of them is left open to pairing.
#
# Adapters are named in /etc/snaphouse/local.yml by address (bt_receive_adapter,
# bt_send_adapter) and resolved to the current hciN, since the index can swap
# across reboots. Each configured adapter must be present. Pairing is opened only
# for a short window by `snaphouse pair`; outside it no adapter may be open,
# which here means neither discoverable nor pairable, and that is checked on
# every controller the host has, not only the configured ones. Run by
# `snaphouse verify` from /etc/snaphouse/verify.d.
#
# The work is in Python so it can reuse snaphouse.adapters (and read local.yml
# with the same ruamel the CLI uses); the address itself never reaches stderr,
# only the last two octets, so a failure cannot leak the household's addresses.
set -euo pipefail

exec /opt/snaphouse/.venv/bin/python - <<'PY'
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, "/opt/snaphouse/lib")
from ruamel.yaml import YAML

from snaphouse.adapters import AdapterNotFound, resolve_adapter

# bluetoothctl colours its output; strip the escape codes before matching so a
# coloured "Controller" header or state field is still recognised.
_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")

local = YAML().load(Path("/etc/snaphouse/local.yml").read_text()) or {}

failures = []

# Each configured adapter must be present (its index may have moved).
for key in ("bt_receive_adapter", "bt_send_adapter"):
    mac = local.get(key)
    if not mac:
        continue
    try:
        resolve_adapter(mac)
    except AdapterNotFound as exc:
        failures.append(str(exc))

# No controller on this host may be open to pairing outside a pairing window.
# `bluetoothctl list` prints "Controller <addr> <name> [default]"; the address
# is resolved back to its hciN for the message so the address never reaches
# stderr. Every controller is checked, not only the configured ones.
listed = subprocess.run(
    ["bluetoothctl", "list"], capture_output=True, text=True, check=False
).stdout
for line in listed.splitlines():
    parts = _ANSI.sub("", line).split()
    if len(parts) < 2 or parts[0] != "Controller":
        continue
    addr = parts[1]
    try:
        label = resolve_adapter(addr)
    except AdapterNotFound:
        label = "an adapter"
    shown = _ANSI.sub(
        "",
        subprocess.run(
            ["bluetoothctl", "show", addr], capture_output=True, text=True, check=False
        ).stdout,
    )
    if "Discoverable: yes" in shown:
        failures.append(f"adapter {label} is discoverable")
    if "Pairable: yes" in shown:
        failures.append(f"adapter {label} is pairable")

for line in failures:
    print(line, file=sys.stderr)
sys.exit(1 if failures else 0)
PY
