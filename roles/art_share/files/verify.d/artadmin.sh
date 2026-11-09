#!/usr/bin/env bash
# snaphouse art-admin check, managed by Ansible (roles/art_share). The hub runs
# the art admin service: a loopback HTTP API the room-picker page drives through
# nginx, which also publishes art-index.json and art-control.json. This check
# proves the service is up and its published index is served and parses. Run by
# `snaphouse verify` from /etc/snaphouse/verify.d on a real machine.
#
# Nothing here reads any household value; the index carries only piece names and
# folder names, never a MAC, IP, or location.
set -euo pipefail

systemctl is-active --quiet snaphouse-artadmin.service

# The published index is served same-origin by nginx and must parse as a JSON
# object carrying the folders and pieces arrays the page and the frame read.
curl -fsS http://localhost/art-index.json \
    | jq -e 'type == "object" and (.folders | type == "array") and (.pieces | type == "array")' >/dev/null
