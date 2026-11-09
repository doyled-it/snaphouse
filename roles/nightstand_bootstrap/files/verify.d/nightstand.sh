#!/usr/bin/env bash
# snaphouse-managed-check: self-update prunes this when the applied checkout drops it
# snaphouse nightstand check, managed by Ansible (roles/nightstand_bootstrap).
# snaphouse only bootstraps the frame once; everything display-specific, and the
# frame's own health, is owned and reported by nightstand through its status
# file. This check confirms only that the hand-off landed: nightstand's service
# is enabled, so it will come up and keep itself current. Run by `snaphouse
# verify` from /etc/snaphouse/verify.d.
set -euo pipefail

systemctl is-enabled nightstand >/dev/null
