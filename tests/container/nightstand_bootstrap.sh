#!/usr/bin/env bash
# nightstand bootstrap container check (Task 9).
#
# Applies site.yml for the frame host inside a debian:bookworm container,
# pointing the bootstrap at a local stub repo instead of the real nightstand
# (the repo URL is a role variable for exactly this). It proves three things:
#   1. A host that carries nightstand_bootstrap but has frame_hardware false (the
#      office before its Inky is wired) installs nothing from nightstand: no
#      clone, no installer, no verify check. It still appears as a frame on the
#      hub, but the role is a no-op there.
#   2. On the frame host (frame_hardware true) the first apply clones the repo to
#      /opt/nightstand and runs its installer once, creating /etc/nightstand/local.toml.
#   3. A second apply of the frame host changes nothing (the clone and installer
#      are guarded by creates), so the role never re-touches nightstand-owned paths.
#
# This is a focused feature gate; the broad clean-apply gate is run.sh. It runs
# in its own CI job.
#
# Usage: tests/container/nightstand_bootstrap.sh
# Requires: docker.
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "${script_dir}/../.." && pwd)"
image="${SNAPHOUSE_TEST_IMAGE:-debian:bookworm}"

echo "==> nightstand bootstrap container check using ${image}"

docker run --rm -i -v "${repo_root}:/repo:ro" -w /repo -e DEBIAN_FRONTEND=noninteractive \
    "${image}" bash -s <<'INNER'
set -euo pipefail

apt-get update -qq
apt-get install -y -qq git ansible-core python3 >/dev/null

# Placeholder local settings, the same shape bootstrap.sh would write.
mkdir -p /etc/snaphouse
cp /repo/local.example.yml /etc/snaphouse/local.yml

# A stub nightstand repo. It stands in for github.com/doyled-it/nightstand so the
# bootstrap can be exercised with no network and no real e-ink frame. Its
# installer mirrors the real one's contract: idempotent, run as root, and it
# creates /etc/nightstand/local.toml from the example when that file is missing.
stub=/srv/stub-nightstand
mkdir -p "${stub}/deploy"
cat > "${stub}/deploy/local.example.toml" <<'TOML'
# Stub nightstand local config example.
hub_address = "house.local"
TOML
cat > "${stub}/deploy/install.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
mkdir -p /etc/nightstand
if [ ! -f /etc/nightstand/local.toml ]; then
    cp "$(dirname "$0")/local.example.toml" /etc/nightstand/local.toml
fi
EOF
chmod +x "${stub}/deploy/install.sh"
git -C "${stub}" init -q
git -C "${stub}" -c user.email=ci@example.invalid -c user.name=ci add -A
git -C "${stub}" -c user.email=ci@example.invalid -c user.name=ci commit -q -m "stub nightstand"

# The frame host is one that carries nightstand_bootstrap AND has the frame
# hardware attached (frame_hardware true); read it from the inventory rather than
# hardcoding a hostname. A host with the role but frame_hardware false is a frame
# with no display yet, where the role must be a no-op.
host="$(python3 -c "import yaml; h=yaml.safe_load(open('/repo/vars/hosts.yml'))['snaphouse_hosts']; print(next(k for k, v in h.items() if 'nightstand_bootstrap' in v['roles'] and v.get('frame_hardware')))")"
nohw="$(python3 -c "import yaml; h=yaml.safe_load(open('/repo/vars/hosts.yml'))['snaphouse_hosts']; print(next((k for k, v in h.items() if 'nightstand_bootstrap' in v['roles'] and not v.get('frame_hardware')), ''))")"

# Prove the no-op first, while /opt/nightstand still does not exist. A
# frame_hardware-false host must install nothing from nightstand even though it
# carries the role (so the office self-update never runs nightstand's installer
# before the Inky is wired).
if [ -n "${nohw}" ]; then
    echo "==> Applying site.yml for ${nohw} (frame_hardware false: expect a nightstand no-op)"
    ansible-playbook site.yml -e "snaphouse_host=${nohw}" -e "nightstand_bootstrap_repo_url=${stub}" \
        --skip-tags hardware,service
    echo "==> Checking the frame_hardware-false host installed nothing from nightstand"
    test ! -e /opt/nightstand
    test ! -e /etc/nightstand
    test ! -e /etc/snaphouse/verify.d/nightstand.sh
    echo "==> ${nohw} is a nightstand no-op (role present, hardware absent)"
else
    echo "==> No frame_hardware-false host in the inventory; skipping the no-op check"
fi

echo "==> Applying site.yml for ${host} (first apply)"
ansible-playbook site.yml -e "snaphouse_host=${host}" -e "nightstand_bootstrap_repo_url=${stub}" \
    --skip-tags hardware,service

echo "==> Checking the first apply cloned and installed nightstand"
test -d /opt/nightstand/.git
test -f /etc/nightstand/local.toml
echo "==> Clone and installer landed"

echo "==> Applying site.yml for ${host} (second apply)"
recap="$(ansible-playbook site.yml -e "snaphouse_host=${host}" -e "nightstand_bootstrap_repo_url=${stub}" \
    --skip-tags hardware,service | tee /dev/stderr | grep 'PLAY RECAP' -A1 | tail -1)"

echo "==> Second apply recap: ${recap}"
if ! echo "${recap}" | grep -q 'changed=0'; then
    echo "!!! second apply was not idempotent: ${recap}" >&2
    exit 1
fi
echo "==> Second apply changed nothing"
INNER
