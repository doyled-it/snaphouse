#!/usr/bin/env bash
# snaphouse-managed-check: self-update prunes this when the applied checkout drops it
# snaphouse homebody daemon check, managed by Ansible (roles/homebody_bootstrap).
# The hub runs `homebody run` (homebody.service), which writes a status file that
# nginx serves as /homebody.json. This check proves the unit is active and the
# file is served as a JSON object. It reads only that status document.
#
# A 404 is not tolerated: the daemon writes the file after its first tick, and
# this check retries for up to 45 seconds (under `snaphouse verify`'s 60 second
# per-check timeout), so a fresh start gets time to write it. After the window the
# failure message says which part is missing: unit not active, file not written
# (404), or nginx not answering.
set -euo pipefail

url=http://localhost/homebody.json

ready() {
    systemctl is-active --quiet homebody.service || return 1
    curl -fsS "${url}" | jq -e 'type == "object" and has("version")' >/dev/null 2>&1
}

deadline=$(( $(date +%s) + 45 ))
while ! ready; do
    if [ "$(date +%s)" -ge "${deadline}" ]; then
        if ! systemctl is-active --quiet homebody.service; then
            echo "verify: homebody.service is not active" >&2
            exit 1
        fi
        code="$(curl -s -o /dev/null -w '%{http_code}' "${url}" || true)"
        if [ "${code}" = "404" ]; then
            echo "verify: homebody is running but has not written ${url} (404)" >&2
        else
            echo "verify: ${url} did not return a valid status document (http ${code})" >&2
        fi
        exit 1
    fi
    sleep 2
done
echo "verify: ok"
