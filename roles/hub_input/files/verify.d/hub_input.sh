#!/usr/bin/env bash
# snaphouse hub-input check: snapserver is up and serving the three streams the
# house expects (AirPlay, Bluetooth, and the House meta stream). Run by
# `snaphouse verify` from /etc/snaphouse/verify.d.
set -euo pipefail

systemctl is-active --quiet snapserver.service

status="$(curl -fsS -m 5 \
    -d '{"id":1,"jsonrpc":"2.0","method":"Server.GetStatus"}' \
    http://localhost:1780/jsonrpc)"

streams="$(printf '%s' "${status}" | jq -r '.result.server.streams[].id')"

missing=()
for want in AirPlay Bluetooth House; do
    if ! printf '%s\n' "${streams}" | grep -qx "${want}"; then
        missing+=("${want}")
    fi
done

if [ "${#missing[@]}" -ne 0 ]; then
    echo "snapserver is missing streams: ${missing[*]}" >&2
    exit 1
fi
