#!/usr/bin/env bash
# snaphouse-managed-check: self-update prunes this when the applied checkout drops it
# snaphouse mDNS check: avahi advertises .local names over IPv4 only. A phone VPN
# exempts private IPv4 from the tunnel but not global IPv6, so a name that also
# resolves to a global IPv6 address sends the request into the tunnel and fails.
# Both settings must be active in /etc/avahi/avahi-daemon.conf: use-ipv6=no (do
# not bind IPv6 at all) and publish-aaaa-on-ipv4=no (do not advertise AAAA
# records over the IPv4 socket). The value is anchored to the whole line (an
# optional trailing comment aside) so a prefix like "note" cannot pass. The role
# flushes handlers right after the edit, so the restart is already bound to the
# change; this check proves the file, and deliberately does not compare the conf
# mtime against avahi's start time, because the Pis have no RTC and boot with a
# stale clock until NTP syncs, which would make a healthy host fail verify and
# fall back on every timer run. Run by `snaphouse verify` from
# /etc/snaphouse/verify.d.
set -euo pipefail

conf=/etc/avahi/avahi-daemon.conf

# Each key must be set to exactly "no" on its own line (a trailing comment is
# allowed). A clear message names the offending setting, since `snaphouse verify`
# surfaces only the last stderr line.
check_setting() {
    local key="$1"
    if ! grep -Eq "^[[:space:]]*${key}[[:space:]]*=[[:space:]]*no[[:space:]]*(#.*)?\$" "$conf"; then
        echo "${key} is not set to no in ${conf}" >&2
        exit 1
    fi
}

check_setting use-ipv6
check_setting publish-aaaa-on-ipv4
