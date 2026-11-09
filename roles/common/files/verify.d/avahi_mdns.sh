#!/usr/bin/env bash
# snaphouse-managed-check: self-update prunes this when the applied checkout drops it
# snaphouse mDNS check: avahi advertises .local names over IPv4 only. A phone VPN
# exempts private IPv4 from the tunnel but not global IPv6, so a name that also
# resolves to a global IPv6 address sends the request into the tunnel and fails.
# Both settings must be active in /etc/avahi/avahi-daemon.conf: use-ipv6=no (do
# not bind IPv6 at all) and publish-aaaa-on-ipv4=no (do not advertise AAAA
# records over the IPv4 socket). Run by `snaphouse verify` from
# /etc/snaphouse/verify.d.
set -euo pipefail

conf=/etc/avahi/avahi-daemon.conf

grep -Eq '^[[:space:]]*use-ipv6[[:space:]]*=[[:space:]]*no' "$conf"
grep -Eq '^[[:space:]]*publish-aaaa-on-ipv4[[:space:]]*=[[:space:]]*no' "$conf"
