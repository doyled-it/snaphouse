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
# The AirPlay 2 shairport-sync container that feeds the AirPlay loopback (the
# AirPlay source reads the other end). Without it there is no AirPlay input.
systemctl is-active --quiet snaphouse-airplay-in.service

# ...and the receiver must actually answer RTSP on the AirPlay 2 port (7000, from
# airplay_rtsp_port in vars/all.yml). An active unit only means the container is
# up; the receiver inside takes a few seconds to start, so retry briefly.
airplay_answers() {
    local reply
    reply="$(timeout 3 bash -c '
        exec 3<>/dev/tcp/127.0.0.1/7000
        printf "OPTIONS * RTSP/1.0\r\nCSeq: 1\r\n\r\n" >&3
        head -n1 <&3' 2>/dev/null || true)"
    [[ "${reply}" == RTSP/1.0* ]]
}
for _ in 1 2 3 4 5 6; do
    airplay_answers && break
    sleep 2
done
airplay_answers || { echo "AirPlay receiver does not answer RTSP on port 7000" >&2; exit 1; }

PYTHONPATH=/opt/snaphouse/lib /opt/snaphouse/.venv/bin/python \
    -m snaphouse.clients verify-streams --url http://localhost:1780/jsonrpc
