#!/usr/bin/env bash
# snaphouse bootstrap: the one command run on a freshly flashed machine.
#
# Run as root. It installs the tools, clones the repo, writes the local settings
# file from the example if it is missing, runs the first apply, and turns on the
# self-update timer. After this the machine keeps itself current from main.
set -euo pipefail

repo_url="https://github.com/doyled-it/snaphouse"
repo_dir="/opt/snaphouse"
local_dir="/etc/snaphouse"
local_file="${local_dir}/local.yml"

if [ "$(id -u)" -ne 0 ]; then
    echo "bootstrap.sh must run as root" >&2
    exit 1
fi

echo "==> Installing git, ansible-core, and python3"
apt-get update
apt-get install -y git ansible-core python3

if [ -d "${repo_dir}/.git" ]; then
    echo "==> Updating the existing checkout at ${repo_dir}"
    git -C "${repo_dir}" pull --ff-only
else
    echo "==> Cloning ${repo_url} to ${repo_dir}"
    git clone "${repo_url}" "${repo_dir}"
fi

# local.yml holds identifying values and the art-share password, so keep the
# directory and file readable by root only (0700 dir, 0600 file). Fix the mode
# on an already-present file too, so older installs are tightened on the next run.
install -d -m 0700 -o root -g root "${local_dir}"
if [ ! -f "${local_file}" ]; then
    echo "==> Creating ${local_file} from the example; opening it for editing"
    install -m 0600 -o root -g root "${repo_dir}/local.example.yml" "${local_file}"
    "${EDITOR:-vi}" "${local_file}"
else
    chmod 0600 "${local_file}"
fi

echo "==> Running the first apply"
"${repo_dir}/bin/snaphouse" update --force

echo "==> Enabling the self-update timer"
systemctl enable --now snaphouse-update.timer

echo "==> Bootstrap complete"
