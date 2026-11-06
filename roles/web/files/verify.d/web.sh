#!/usr/bin/env bash
# snaphouse web check: nginx serves this machine's status file locally and it is
# valid JSON carrying a host field. Run by `snaphouse verify` (Task 3) from
# /etc/snaphouse/verify.d.
set -euo pipefail

curl -fsS http://localhost/status.json | jq -e '.host' >/dev/null
