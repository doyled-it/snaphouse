#!/usr/bin/env bash
# snaphouse-managed-check: self-update prunes this when the applied checkout drops it
# snaphouse Wi-Fi watchdog check (issue #178): on a machine with a wlan0 managed
# by NetworkManager, the watchdog timer must be enabled and active. Passes where
# there is no wlan0 (the wired hub) or no NetworkManager, since the role installs
# no watchdog there. Run by `snaphouse verify` from /etc/snaphouse/verify.d.
set -euo pipefail

if [ ! -e /sys/class/net/wlan0 ] || ! command -v nmcli >/dev/null 2>&1; then
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
