#!/usr/bin/env bash
# snaphouse-managed-check: self-update prunes this when the applied checkout drops it
# snaphouse panel check, managed by Ansible (roles/panel). The kiosk service being
# active means cage and Chromium are up showing the room picker; the backlight
# service being active means the screen follows the dim/wake schedule rather than
# sitting dark. A panel failing either is effectively down. Run by `snaphouse
# verify` from /etc/snaphouse/verify.d.
set -euo pipefail

if ! systemctl is-active --quiet snaphouse-kiosk.service; then
    echo "snaphouse-kiosk.service is not active" >&2
    exit 1
fi

if ! systemctl is-active --quiet snaphouse-backlight.service; then
    echo "snaphouse-backlight.service is not active" >&2
    exit 1
fi
