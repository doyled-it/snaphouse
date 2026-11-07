#!/usr/bin/env bash
# snaphouse snapclient check: this room's player is running. snapclient connects
# to snapserver on the hub and plays the House stream through the room's output.
# Whether audio actually reaches a Bluetooth speaker depends on the speaker being
# powered on, which the bt_speaker check covers; this check only asserts the
# service is active. Run by `snaphouse verify` from /etc/snaphouse/verify.d.
set -euo pipefail

systemctl is-active --quiet snapclient.service
