#!/usr/bin/env bash
# snaphouse art-share check, managed by Ansible (roles/art_share). The hub serves
# the art folder two ways on the LAN: a read-only JSON listing over nginx that
# nightstand pulls from, and a writable SMB share people drop images into. This
# check proves both surfaces are up. Run by `snaphouse verify` from
# /etc/snaphouse/verify.d.
#
# The JSON listing must be a JSON array (nginx autoindex_format json), and the
# SMB server must advertise the 'art' share. Nothing here reads any household
# value; the share password lives only in local.yml and never reaches this check.
set -euo pipefail

curl -fsS http://localhost/art/ | jq -e 'type == "array"' >/dev/null
smbclient -L localhost -N 2>/dev/null | grep -qw art
