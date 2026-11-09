#!/usr/bin/env bash
# snaphouse-managed-check: self-update prunes this when the applied checkout drops it
# snaphouse hub-input check: snapserver is up and serving the three streams the
# house expects (AirPlay, Bluetooth, and the House meta stream), with the
# Bluetooth source on the shared 44100:16:2 sample format. Run by
# `snaphouse verify` from /etc/snaphouse/verify.d.
#
# The stream check runs in the on-device venv Python, not jq: Server.GetStatus
# carries cover art and track metadata inline, and that payload can contain
# unescaped control characters that jq (and a strict JSON parser) reject with
# 'control characters must be escaped'. A strict read here would fail verify on a
# bloated status and roll the hub back to its last-good commit. snaphouse.clients
# verify-streams parses with strict=False (see check_streams) so the check is
# tolerant of the inline art.
set -euo pipefail

systemctl is-active --quiet snapserver.service
# The standalone shairport-sync that feeds the AirPlay loopback (the AirPlay
# source reads the other end). Without it there is no AirPlay input.
systemctl is-active --quiet snaphouse-airplay-in.service

PYTHONPATH=/opt/snaphouse/lib /opt/snaphouse/.venv/bin/python \
    -m snaphouse.clients verify-streams --url http://localhost:1780/jsonrpc
