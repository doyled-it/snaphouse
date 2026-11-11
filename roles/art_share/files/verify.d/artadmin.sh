#!/usr/bin/env bash
# snaphouse-managed-check: self-update prunes this when the applied checkout drops it
# snaphouse art-admin check, managed by Ansible (roles/art_share). The hub runs
# the art admin service: a loopback HTTP API the room-picker page drives through
# nginx, which also publishes art-index.json and art-control.json. This check
# proves the service is up and its published index is served and parses. Run by
# `snaphouse verify` from /etc/snaphouse/verify.d on a real machine.
#
# It retries for up to 45 seconds before failing: right after an apply the
# service may still be binding its socket or nginx may be mid-reload, and a
# first miss should not fail the whole verify and trigger a self-update
# rollback. The service publishes the index immediately (before thumbnails), so
# in practice the first attempt passes; the window is a safety net and stays
# well under `snaphouse verify`'s 60 second per-check timeout.
#
# Nothing here reads any household value; the index carries only piece names and
# folder names, never a MAC, IP, or location.
set -euo pipefail

ready() {
    systemctl is-active --quiet snaphouse-artadmin.service || return 1
    curl -fsS http://localhost/art-index.json \
        | jq -e 'type == "object" and (.folders | type == "array") and (.pieces | type == "array")' \
        >/dev/null 2>&1 || return 1
    # The same service publishes frame-control.json (the shared frame settings and
    # the per-frame quiet hours and pins under `frames`); prove it is served and
    # parses too.
    curl -fsS http://localhost/frame-control.json \
        | jq -e 'type == "object" and (.settings | type == "object") and (.frames | type == "object")' \
        >/dev/null 2>&1 || return 1
    return 0
}

deadline=$(( $(date +%s) + 45 ))
while ! ready; do
    if [ "$(date +%s)" -ge "${deadline}" ]; then
        # Window exhausted: run the checks once more without suppression so the
        # real failure (service down, or a bad or missing index) reaches stderr.
        systemctl is-active --quiet snaphouse-artadmin.service
        curl -fsS http://localhost/art-index.json \
            | jq -e 'type == "object" and (.folders | type == "array") and (.pieces | type == "array")' >/dev/null
        curl -fsS http://localhost/frame-control.json \
            | jq -e 'type == "object" and (.settings | type == "object") and (.frames | type == "object")' >/dev/null
        exit 1
    fi
    sleep 2
done
