#!/usr/bin/env bash
# snaphouse room picker check (hub only): nginx serves the page and its room
# list locally, and snapserver answers through the /jsonrpc proxy the page uses.
# Run by `snaphouse verify` from /etc/snaphouse/verify.d.
set -euo pipefail

# The page is served.
curl -fsS http://localhost/ | grep -q "<title>"

# The room list is valid JSON with at least one room.
curl -fsS http://localhost/rooms.json | jq -e ".rooms | length > 0" >/dev/null

# The hub-aggregated health file is served and carries a machines array.
curl -fsS http://localhost/house-status.json | jq -e ".machines | length > 0" >/dev/null

# snapserver answers Server.GetStatus through the proxy the page talks to.
curl -fsS -X POST http://localhost/jsonrpc \
    -H "Content-Type: application/json" \
    -d '{"id":1,"jsonrpc":"2.0","method":"Server.GetStatus"}' \
    | jq -e ".result.server" >/dev/null
