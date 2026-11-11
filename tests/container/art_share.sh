#!/usr/bin/env bash
# Art-share container check (Task 8).
#
# Applies site.yml for the hub (house) inside a debian:bookworm container the
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
# art-share password is a throwaway value that never leaves the container. Both
# passwords below deliberately carry a double quote, a dollar sign, a backtick
# and a backslash, the characters a shell would mangle, to prove the role sets
# them verbatim (it feeds smbpasswd on stdin, no shell interpolation).
mkdir -p /etc/snaphouse
cp /repo/local.example.yml /etc/snaphouse/local.yml

pw1='Qu"x$y`z\1'
pw2='Nu"w$v`t\2'

# Rewrite the art_share_password line in place. The value is single-quoted for
# YAML (none of these passwords contain a single quote), and "$1" is a literal
# expansion so the shell never re-interprets its $ or backtick.
set_art_password() {
    sed -i '/^art_share_password:/d' /etc/snaphouse/local.yml
    printf "art_share_password: '%s'\n" "$1" >> /etc/snaphouse/local.yml
}

# The hub is whichever host carries the art_share role; read it from the
# inventory rather than hardcoding a hostname, so a later hostname change needs
# no edit here.
host="$(python3 -c "import yaml; h=yaml.safe_load(open('/repo/vars/hosts.yml'))['snaphouse_hosts']; print(next(k for k, v in h.items() if 'art_share' in v['roles']))")"

# The hub also carries homebody_bootstrap, which clones homebody on apply. Point
# it at a local stub so this art-share gate stays hermetic (no network, no real
# homebody). The stub installer mirrors the real contract: idempotent, root,
# creates /etc/homebody/local.toml. homebody's own gate is homebody_bootstrap.sh.
hb_stub=/srv/stub-homebody
mkdir -p "${hb_stub}/deploy"
cat > "${hb_stub}/deploy/install.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
mkdir -p /etc/homebody
touch /etc/homebody/local.toml
EOF
chmod +x "${hb_stub}/deploy/install.sh"
git -C "${hb_stub}" init -q
git -C "${hb_stub}" -c user.email=ci@example.invalid -c user.name=ci add -A
git -C "${hb_stub}" -c user.email=ci@example.invalid -c user.name=ci commit -q -m "stub homebody"

set_art_password "${pw1}"
echo "==> Applying site.yml for ${host} (the art-share host)"
ansible-playbook site.yml -e "snaphouse_host=${host}" \
    -e "homebody_bootstrap_repo_url=${hb_stub}" --skip-tags hardware,service

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

# The share account exists in Samba's passdb.
pdbedit -L | grep -q '^art:'
echo "==> art account present in the passdb"

# Authenticate as art with the first password and write a file through the share,
# then confirm it shows up in the read-only JSON listing.
echo "hello from the art share" > /tmp/art-upload.txt
echo "==> Writing a file over SMB with the first password"
smbclient //localhost/art -U "art%${pw1}" -c 'put /tmp/art-upload.txt uploaded1.txt'
curl -fsS http://localhost/art/ | jq -e 'any(.[]; .name == "uploaded1.txt")' >/dev/null
echo "==> First password authenticates and the upload is listed"

# Rotate the password in local.yml and re-apply. local.yml is the source of
# truth, so the role must notice the change and re-set the SMB password.
set_art_password "${pw2}"
echo "==> Re-applying after rotating the password"
ansible-playbook site.yml -e "snaphouse_host=${host}" \
    -e "homebody_bootstrap_repo_url=${hb_stub}" --skip-tags hardware,service

echo "==> The old password must now be rejected"
if smbclient //localhost/art -U "art%${pw1}" -c 'ls' >/dev/null 2>&1; then
    echo "!!! the old password still works after rotation" >&2
    exit 1
fi
echo "==> Writing a file over SMB with the rotated password"
smbclient //localhost/art -U "art%${pw2}" -c 'put /tmp/art-upload.txt uploaded2.txt'
curl -fsS http://localhost/art/ | jq -e 'any(.[]; .name == "uploaded2.txt")' >/dev/null
echo "==> Rotated password authenticates; password path verified"

# With the password unchanged in local.yml, a re-apply must set nothing: the
# rotation check compares the stored hash to the configured one and stays quiet.
echo "==> A re-apply with the same password must change nothing"
recap="$(ansible-playbook site.yml -e "snaphouse_host=${host}" \
    -e "homebody_bootstrap_repo_url=${hb_stub}" --skip-tags hardware,service \
    | grep 'PLAY RECAP' -A1 | tail -1)"
echo "==> Unchanged re-apply recap: ${recap}"
if ! echo "${recap}" | grep -q 'changed=0'; then
    echo "!!! art_share was not idempotent on an unchanged password: ${recap}" >&2
    exit 1
fi
echo "==> Unchanged re-apply is a no-op; idempotency verified"
INNER
