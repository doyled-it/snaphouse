#!/usr/bin/env bash
# snaphouse-managed-check: self-update prunes this when the applied checkout drops it
# snaphouse Ente sync check, managed by Ansible (roles/ente_sync). Proves the sync
# timer is active and the status record the runner writes parses. It passes in the
# "not set up" state (the owner has not logged in to Ente yet): the runner still
# writes a status record then, so only a corrupt record fails.
set -euo pipefail

systemctl is-active --quiet snaphouse-ente-sync.timer
status=/var/lib/snaphouse/ente/status.json
# Right after the first apply the timer has not fired yet, so there is no record.
# That is the same "not set up yet" state, not a failure; a record that exists
# must parse.
if [ -e "${status}" ]; then
    jq -e '.state' "${status}" >/dev/null
fi
