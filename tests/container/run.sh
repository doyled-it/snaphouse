#!/usr/bin/env bash
# Container apply check.
#
# Runs site.yml once per host from vars/hosts.yml inside a debian:bookworm
# container, the way a real machine applies it (connection: local), with a
# placeholder local.yml and the hardware and service tasks skipped. In this
# scaffold the roles are empty, so every host must apply cleanly and fast. As
# roles grow, this is the CI gate that proves the container-testable parts work.
#
# Usage: tests/container/run.sh
# Requires: docker.
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "${script_dir}/../.." && pwd)"
image="${SNAPHOUSE_TEST_IMAGE:-debian:bookworm}"

echo "==> Container apply using ${image}"

# The whole apply runs inside one container. PyYAML is pulled in by ansible-core,
# so host enumeration can happen in-container with no extra dependency.
docker run --rm -i -v "${repo_root}:/repo:ro" -w /repo -e DEBIAN_FRONTEND=noninteractive \
    "${image}" bash -s <<'INNER'
set -euo pipefail

apt-get update -qq
apt-get install -y -qq git ansible-core python3 >/dev/null

# Placeholder local settings, the same shape bootstrap.sh would write.
mkdir -p /etc/snaphouse
cp /repo/local.example.yml /etc/snaphouse/local.yml

hosts="$(python3 -c "import yaml; print(' '.join(yaml.safe_load(open('/repo/vars/hosts.yml'))['snaphouse_hosts']))")"
echo "==> Hosts: ${hosts}"

rc=0
for host in ${hosts}; do
    echo "==> Applying site.yml for ${host}"
    if ! ansible-playbook site.yml -e "snaphouse_host=${host}" --skip-tags hardware,service; then
        echo "!!! apply failed for ${host}" >&2
        rc=1
    fi
done

if [ "${rc}" -eq 0 ]; then
    echo "==> All hosts applied cleanly"
fi
exit "${rc}"
INNER
