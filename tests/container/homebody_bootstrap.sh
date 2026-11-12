#!/usr/bin/env bash
# homebody bootstrap container check (Task 1 of the homebody plan).
#
# Applies site.yml for the hub inside a debian:bookworm container, pointing the
# bootstrap at a local stub repo instead of the real homebody (the repo URL is a
# role variable for exactly this). It proves two things:
#   1. On first apply the bootstrap clones the repo to /opt/homebody and runs its
#      installer once, which writes the update state file last, and the role
#      templates the three systemd units.
#   2. A second apply changes nothing (the clone and installer are guarded by
#      creates, the templates are stable), so the role never re-touches a
#      homebody-owned path.
#
# This is a focused feature gate; the broad clean-apply gate is run.sh. It runs
# in its own CI job.
#
# Usage: tests/container/homebody_bootstrap.sh
# Requires: docker.
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "${script_dir}/../.." && pwd)"
image="${SNAPHOUSE_TEST_IMAGE:-debian:bookworm}"

echo "==> homebody bootstrap container check using ${image}"

docker run --rm -i -v "${repo_root}:/repo:ro" -w /repo -e DEBIAN_FRONTEND=noninteractive \
    "${image}" bash -s <<'INNER'
set -euo pipefail

apt-get update -qq
apt-get install -y -qq git ansible-core python3 >/dev/null

# Placeholder local settings, the same shape bootstrap.sh would write.
mkdir -p /etc/snaphouse
cp /repo/local.example.yml /etc/snaphouse/local.yml

# A stub homebody repo. It stands in for github.com/doyled-it/homebody so the
# bootstrap can be exercised with no network and no real thermostat. Its
# installer mirrors the real one's contract: idempotent, run as root, and it
# writes /var/lib/homebody/update/update.state at the end, after creating
# /etc/homebody/local.toml from the example when that file is missing.
stub=/srv/stub-homebody
mkdir -p "${stub}/deploy"
cat > "${stub}/deploy/local.example.toml" <<'TOML'
# Stub homebody local config example.
comfort_f = 72
TOML
cat > "${stub}/deploy/install.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
mkdir -p /etc/homebody
if [ ! -f /etc/homebody/local.toml ]; then
    cp "$(dirname "$0")/local.example.toml" /etc/homebody/local.toml
fi
mkdir -p /var/lib/homebody/update
touch /var/lib/homebody/update/update.state
EOF
chmod +x "${stub}/deploy/install.sh"
git -C "${stub}" init -q
git -C "${stub}" -c user.email=ci@example.invalid -c user.name=ci add -A
git -C "${stub}" -c user.email=ci@example.invalid -c user.name=ci commit -q -m "stub homebody"

# A stub nightstand repo too: the hub does not run nightstand_bootstrap, so this
# is only needed if the role list ever changes, but keeping the apply hermetic is
# cheap. The hub host is whichever host carries the homebody_bootstrap role.
host="$(python3 -c "import yaml; h=yaml.safe_load(open('/repo/vars/hosts.yml'))['snaphouse_hosts']; print(next(k for k, v in h.items() if 'homebody_bootstrap' in v['roles']))")"
echo "==> Applying site.yml for ${host} (first apply)"
ansible-playbook site.yml -e "snaphouse_host=${host}" -e "homebody_bootstrap_repo_url=${stub}" \
    --skip-tags hardware,service

echo "==> Checking the first apply cloned, installed, and templated the units"
test -d /opt/homebody/.git
test -f /etc/homebody/local.toml
test -f /var/lib/homebody/update/update.state
test -f /etc/systemd/system/homebody-matter.service
test -f /etc/systemd/system/homebody-update.service
test -f /etc/systemd/system/homebody-update.timer
echo "==> Clone, installer, and units landed"

echo "==> Applying site.yml for ${host} (second apply)"
recap="$(ansible-playbook site.yml -e "snaphouse_host=${host}" -e "homebody_bootstrap_repo_url=${stub}" \
    --skip-tags hardware,service | tee /dev/stderr | grep 'PLAY RECAP' -A1 | tail -1)"

echo "==> Second apply recap: ${recap}"
if ! echo "${recap}" | grep -q 'changed=0'; then
    echo "!!! second apply was not idempotent: ${recap}" >&2
    exit 1
fi
echo "==> Second apply changed nothing"
INNER
