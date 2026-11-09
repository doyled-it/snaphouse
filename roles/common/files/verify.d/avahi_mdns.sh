#!/usr/bin/env bash
# snaphouse-managed-check: self-update prunes this when the applied checkout drops it
# snaphouse mDNS check: avahi advertises .local names over IPv4 only. A phone VPN
# exempts private IPv4 from the tunnel but not global IPv6, so a name that also
# resolves to a global IPv6 address sends the request into the tunnel and fails.
# Both settings must be active in /etc/avahi/avahi-daemon.conf: use-ipv6=no (do
# not bind IPv6 at all) and publish-aaaa-on-ipv4=no (do not advertise AAAA
# records over the IPv4 socket). The value is anchored to the whole line (an
# optional trailing comment aside) so a prefix like "note" cannot pass. When the
# running daemon can be inspected, the check also fails if the conf was edited
# after avahi last started, which would mean the daemon is still serving the old
# config. Run by `snaphouse verify` from /etc/snaphouse/verify.d.
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

# Prove the running daemon, not just the file, when that is cheap. If avahi is
# active under systemd and the conf's mtime is newer than avahi's last start, the
# daemon is running a stale config and a restart was missed. Any uncertainty
# (no systemctl, inactive, or an unparseable timestamp) skips this: the file
# check above is the hard gate, and this only fails on clear evidence of drift.
if command -v systemctl >/dev/null 2>&1 \
    && systemctl is-active --quiet avahi-daemon.service 2>/dev/null; then
    started="$(systemctl show -p ActiveEnterTimestamp --value avahi-daemon.service 2>/dev/null || true)"
    if [ -n "${started}" ]; then
        started_epoch="$(date -d "${started}" +%s 2>/dev/null || true)"
        conf_mtime="$(stat -c %Y "${conf}" 2>/dev/null || true)"
        if [ -n "${started_epoch}" ] && [ -n "${conf_mtime}" ] \
            && [ "${conf_mtime}" -gt "${started_epoch}" ]; then
            echo "avahi-daemon started before ${conf} was last edited; running a stale config" >&2
            exit 1
        fi
    fi
fi
