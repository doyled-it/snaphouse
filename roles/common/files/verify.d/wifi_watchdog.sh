#!/usr/bin/env bash
# snaphouse-managed-check: self-update prunes this when the applied checkout drops it
# snaphouse Wi-Fi watchdog check (issue #178): on a machine with a wlan0 managed
# by NetworkManager, the watchdog timer must be enabled and active. Passes where
# there is no wlan0 (the wired hub) or no NetworkManager Wi-Fi connection profile,
# since the role installs no watchdog there. The gate is the same test the role
# uses (wlan0 plus a profile of type 802-11-wireless), so this cannot fail falsely
# and trigger a fallback. Run by `snaphouse verify` from /etc/snaphouse/verify.d.
set -euo pipefail

if [ ! -e /sys/class/net/wlan0 ]; then
    exit 0
fi
if ! types=$(nmcli -g TYPE connection show 2>/dev/null) || ! printf '%s\n' "$types" | grep -q '802-11-wireless'; then
    exit 0
fi

timer=snaphouse-wifi-watchdog.timer
if ! systemctl is-enabled --quiet "$timer"; then
    echo "${timer} is not enabled" >&2
    exit 1
fi
if ! systemctl is-active --quiet "$timer"; then
    echo "${timer} is not active" >&2
    exit 1
fi
