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

# A stub nightstand repo so the nightstand bootstrap stays hermetic here: the
# nightstand host's clone points at this instead of reaching out to GitHub. Its
# installer mirrors the real contract (idempotent, root, creates
# /etc/nightstand/local.toml). The focused idempotency check is in
# nightstand_bootstrap.sh; here it only needs to apply cleanly.
stub_nightstand=/srv/stub-nightstand
mkdir -p "${stub_nightstand}/deploy"
cat > "${stub_nightstand}/deploy/install.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
mkdir -p /etc/nightstand
touch /etc/nightstand/local.toml
EOF
chmod +x "${stub_nightstand}/deploy/install.sh"
git -C "${stub_nightstand}" init -q
git -C "${stub_nightstand}" -c user.email=ci@example.invalid -c user.name=ci add -A
git -C "${stub_nightstand}" -c user.email=ci@example.invalid -c user.name=ci commit -q -m "stub nightstand"

hosts="$(python3 -c "import yaml; print(' '.join(yaml.safe_load(open('/repo/vars/hosts.yml'))['snaphouse_hosts']))")"
echo "==> Hosts: ${hosts}"

rc=0
for host in ${hosts}; do
    echo "==> Applying site.yml for ${host}"
    if ! ansible-playbook site.yml -e "snaphouse_host=${host}" \
        -e "nightstand_bootstrap_repo_url=${stub_nightstand}" --skip-tags hardware,service; then
        echo "!!! apply failed for ${host}" >&2
        rc=1
    fi
done

if [ "${rc}" -eq 0 ]; then
    echo "==> All hosts applied cleanly"
fi
exit "${rc}"
INNER
