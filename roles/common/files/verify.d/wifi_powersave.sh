#!/usr/bin/env bash
# snaphouse-managed-check: self-update prunes this when the applied checkout drops it
# snaphouse Wi-Fi check: power saving is off on wlan0. With it on, the radio dozes
# between packets and the Wi-Fi rooms underrun and resync (issue #140). Passes
# on a machine with no wlan0 (the wired hub), since there is nothing to doze.
# Run by `snaphouse verify` from /etc/snaphouse/verify.d.
set -euo pipefail

if [ ! -e /sys/class/net/wlan0 ]; then
    exit 0
fi

if ! out=$(iw dev wlan0 get power_save 2>&1); then
    echo "iw could not read wlan0 power save: ${out}" >&2
    exit 1
fi

if ! printf '%s\n' "$out" | grep -Eq 'Power save:[[:space:]]*off'; then
    echo "wlan0 power save is on, expected off" >&2
    exit 1
fi
