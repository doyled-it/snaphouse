#!/usr/bin/env bash
# snaphouse-managed-check: self-update prunes this when the applied checkout drops it
# snaphouse homebody check, managed by Ansible (roles/homebody_bootstrap).
# snaphouse only bootstraps homebody once; homebody owns its own health and
# proves it through deploy/verify.sh (the venv imports homebody, and if the
# Matter controller is active its WebSocket answers on 127.0.0.1:5580). This
# check just delegates to that, so the one source of truth for "is homebody
# healthy" lives in the homebody repo. Run by `snaphouse verify` from
# /etc/snaphouse/verify.d.
set -euo pipefail

/opt/homebody/deploy/verify.sh
