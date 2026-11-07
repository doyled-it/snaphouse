#!/usr/bin/env bash
# Close every Bluetooth controller to pairing at boot.
#
# snaphouse opens pairing only inside a short window the owner starts on purpose
# with `snaphouse pair`. The rest of the time no adapter may be discoverable or
# accept a new pairing, so this runs once at boot (and after bluetooth.service)
# and turns both off on each controller. It is the standing state; the pairing
# command flips it on briefly and restores it.
set -euo pipefail

# bluetoothctl can colour its output; strip the escape codes before awk so a
# coloured address field is not mangled into a token that `select` cannot use,
# which would leave that controller open. The verify check strips the same
# `bluetoothctl list` output. The pattern matches the Python _ANSI regex.
esc="$(printf '\033')"
controllers="$(bluetoothctl list | sed -E "s/${esc}\[[0-9;]*[A-Za-z]//g" | awk '{print $2}')"
if [ -z "${controllers}" ]; then
    echo "no Bluetooth controllers present" >&2
    exit 0
fi

# Power the controller on before closing it: pairable/discoverable off only
# sticks on a powered adapter, and an adapter that comes up powered later can
# otherwise default to pairable (the Pairable: yes seen on the hub). The snaphouse
# adapters are meant to be powered anyway (A2DP sink and source).
for controller in ${controllers}; do
    bluetoothctl <<EOF
select ${controller}
power on
discoverable off
pairable off
EOF
done
