#!/usr/bin/env bash
# snaphouse-managed-check: self-update prunes this when the applied checkout drops it
# snaphouse selfupdate check: the self-update timer is active, so this machine
# keeps pulling main and applying it. Run by `snaphouse verify` (see verify.py)
# from /etc/snaphouse/verify.d.
set -euo pipefail

if ! systemctl is-active --quiet snaphouse-update.timer; then
    echo "snaphouse-update.timer is not active" >&2
    exit 1
fi
