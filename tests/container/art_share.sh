#!/usr/bin/env bash
# Art-share container check (Task 8).
#
# Applies site.yml for the hub (zimablade) inside a debian:bookworm container the
# way a real machine would (connection: local), then brings up nginx and smbd as
# plain processes (the container has no systemd, so the service tasks are gated
# off) and runs the on-device art-share verify. The verify asserts the read-only
# JSON listing at /art/ and the writable SMB 'art' share are both up.
#
# This is a focused feature gate; the broad clean-apply gate is run.sh. It runs
# in its own CI job, in parallel with that one.
#
# Usage: tests/container/art_share.sh
# Requires: docker.
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "${script_dir}/../.." && pwd)"
image="${SNAPHOUSE_TEST_IMAGE:-debian:bookworm}"

echo "==> Art-share container check using ${image}"

docker run --rm -i -v "${repo_root}:/repo:ro" -w /repo -e DEBIAN_FRONTEND=noninteractive \
    "${image}" bash -s <<'INNER'
set -euo pipefail

apt-get update -qq
apt-get install -y -qq git ansible-core python3 >/dev/null

# Placeholder local settings, the same shape bootstrap.sh would write. The
# art-share password is a throwaway value that never leaves the container.
mkdir -p /etc/snaphouse
cp /repo/local.example.yml /etc/snaphouse/local.yml

# The hub is whichever host carries the art_share role; read it from the
# inventory rather than hardcoding a hostname, so a later hostname change needs
# no edit here.
host="$(python3 -c "import yaml; h=yaml.safe_load(open('/repo/vars/hosts.yml'))['snaphouse_hosts']; print(next(k for k, v in h.items() if 'art_share' in v['roles']))")"
echo "==> Applying site.yml for ${host} (the art-share host)"
ansible-playbook site.yml -e "snaphouse_host=${host}" --skip-tags hardware,service

# The service tasks are gated off in the container, so start nginx and smbd by
# hand the way systemd would on a real host. smbd needs its state dirs present.
echo "==> Starting nginx and smbd"
nginx
mkdir -p /run/samba /var/log/samba
smbd --daemon --no-process-group
# Give smbd a moment to open its listening socket before the verify connects.
for _ in $(seq 1 20); do
    if smbclient -L localhost -N >/dev/null 2>&1; then
        break
    fi
    sleep 0.5
done

echo "==> Running the art-share verify"
/etc/snaphouse/verify.d/art_share.sh
echo "==> Art-share verify passed"
INNER
