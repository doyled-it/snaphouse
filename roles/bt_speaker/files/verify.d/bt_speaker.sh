#!/usr/bin/env bash
# snaphouse bedroom speaker check: the JBL is connected, or absent. The JBL is
# off most of the time, so absence is normal and passes; the keepalive timer
# reconnects it when it powers on. The check only fails if the Bluetooth stack
# itself cannot be read (bluetoothctl missing). Run by `snaphouse verify` from
# /etc/snaphouse/verify.d.
#
# The speaker address is the household's, so the name (jbl) is passed and
# snaphouse.clients reads the address from local.yml; it never reaches stderr.
set -euo pipefail

PYTHONPATH=/opt/snaphouse/lib \
    exec /opt/snaphouse/.venv/bin/python -m snaphouse.clients speaker-state jbl
