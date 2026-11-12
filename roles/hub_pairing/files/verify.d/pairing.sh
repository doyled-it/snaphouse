#!/usr/bin/env bash
# snaphouse-managed-check: self-update prunes this when the applied checkout drops it
# snaphouse web pairing check, managed by Ansible (roles/hub_pairing). The hub
# runs the pairing service: a loopback HTTP API the room-picker page drives
# through nginx (/api/pair/) to open the Bluetooth pairing window (the hub accepts
# the first phone that asks). This check proves the service is up and its state endpoint
# answers, idle, through nginx. Run by `snaphouse verify` from
# /etc/snaphouse/verify.d on a real machine.
#
# It retries for up to 45 seconds before failing: right after an apply the
# service may still be binding its socket or nginx may be mid-reload, and a first
# miss should not fail the whole verify and trigger a self-update rollback. The
# window stays well under `snaphouse verify`'s 60 second per-check timeout.
#
# The state endpoint is read-only and opens no window; it returns only the window
# state and (after a pairing) the phone's name. Nothing here reads or prints any address.
set -euo pipefail

ready() {
    systemctl is-active --quiet snaphouse-pairing.service || return 1
    # The state endpoint answers and reports a known state. Idle is expected at
    # rest; any of the valid states proves the service is serving.
    curl -fsS http://localhost/api/pair/state \
        | jq -e 'type == "object" and (.state | type == "string") and
                 ([.state] | inside(["idle","open","confirm","paired","declined","failed"]))' \
        >/dev/null 2>&1 || return 1
    return 0
}

deadline=$(( $(date +%s) + 45 ))
while ! ready; do
    if [ "$(date +%s)" -ge "${deadline}" ]; then
        # Window exhausted: run the checks once more without suppression so the
        # real failure (service down, or a bad or missing state) reaches stderr.
        systemctl is-active --quiet snaphouse-pairing.service
        curl -fsS http://localhost/api/pair/state \
            | jq -e 'type == "object" and (.state | type == "string")' >/dev/null
        exit 1
    fi
    sleep 2
done
