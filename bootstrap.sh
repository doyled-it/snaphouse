#!/usr/bin/env bash
# snaphouse bootstrap: the one command run on a freshly flashed machine.
#
# Run as root. It installs the tools, clones the repo, optionally sets this
# machine's hostname to its inventory key, writes the local settings file from
# the example if it is missing, runs the first apply, and turns on the
# self-update timer. After this the machine keeps itself current from main.
#
# Usage:
#   bootstrap.sh [--host <name>]
#
# --host <name> names which snaphouse_hosts block in vars/hosts.yml this machine
# should become (for example `bootstrap.sh --host office`). Give it when the
# machine is not already named after its inventory key: site.yml selects the
# host block by hostname, so a machine still carrying its factory name (the
# ZimaBlade shipped as `casaos`) would fail the inventory check before the
# common role could rename it. With --host, bootstrap sets the hostname before
# the first apply. Without it, the current hostname is used unchanged.
set -euo pipefail

repo_url="https://github.com/doyled-it/snaphouse"
repo_dir="/opt/snaphouse"
local_dir="/etc/snaphouse"
local_file="${local_dir}/local.yml"

# Which /etc/hosts to edit. Overridable only so the tests can point set_hostname
# at a temp file; on a real machine it is always /etc/hosts.
etc_hosts="${ETC_HOSTS:-/etc/hosts}"

# The hostname requested with --host, or empty when the argument was not given.
target_host=""

usage() {
    cat >&2 <<'EOF'
Usage: bootstrap.sh [--host <name>]

  --host <name>   Set this machine's hostname to <name> before the first apply.
                  <name> must be a host key in vars/hosts.yml. Omit to keep the
                  current hostname.
EOF
}

# Parse the command line, setting the global target_host. Returns non-zero on a
# bad argument so main stops before doing anything.
parse_args() {
    target_host=""
    while [ "$#" -gt 0 ]; do
        case "$1" in
            --host)
                if [ "$#" -lt 2 ]; then
                    echo "bootstrap.sh: --host needs a value" >&2
                    return 2
                fi
                target_host="$2"
                shift 2
                ;;
            --host=*)
                target_host="${1#--host=}"
                shift
                ;;
            -h | --help)
                usage
                return 2
                ;;
            *)
                echo "bootstrap.sh: unknown argument '$1'" >&2
                usage
                return 2
                ;;
        esac
    done
}

# Print the host keys defined under snaphouse_hosts in the given hosts.yml, one
# per line. The keys are the only lines indented by exactly two spaces inside
# that block, so no YAML parser is needed (none is installed this early).
valid_hosts() {
    local hosts_file="$1"
    awk '
        /^snaphouse_hosts:/ { in_block = 1; next }
        in_block && /^[^[:space:]]/ { in_block = 0 }
        in_block && /^  [^[:space:]]/ {
            key = $0
            sub(/:.*/, "", key)
            sub(/^  /, "", key)
            print key
        }
    ' "$hosts_file"
}

# Fail with a clear, host-key-listing error when host is not in hosts_file.
validate_host() {
    local host="$1" hosts_file="$2"
    local keys
    keys="$(valid_hosts "$hosts_file")"
    if printf '%s\n' "$keys" | grep -qxF -- "$host"; then
        return 0
    fi
    {
        echo "bootstrap.sh: '${host}' is not a host in vars/hosts.yml. Valid hosts:"
        printf '%s\n' "$keys" | sed 's/^/  /'
    } >&2
    return 1
}

# Set this machine's hostname to host and point the 127.0.1.1 line in /etc/hosts
# at it. Idempotent: a no-op when both already match.
set_hostname() {
    local host="$1"
    local current
    current="$(hostnamectl --static 2>/dev/null || true)"
    if [ "$current" = "$host" ]; then
        echo "==> Hostname is already ${host}"
    else
        echo "==> Setting hostname to ${host}"
        hostnamectl set-hostname "$host"
    fi

    # Keep the Debian 127.0.1.1 line in step with the hostname: rewrite it when
    # present, append it when not. Either way the file ends with exactly one
    # 127.0.1.1 line pointing at host. Written through a temp file and copied
    # back (not sed -i, whose flags differ between GNU and BSD), so /etc/hosts
    # keeps its own permissions and inode.
    local tmp
    tmp="$(mktemp)"
    if grep -qE '^127\.0\.1\.1[[:space:]]' "$etc_hosts"; then
        awk -v host="$host" '
            /^127\.0\.1\.1[[:space:]]/ { print "127.0.1.1\t" host; next }
            { print }
        ' "$etc_hosts" > "$tmp"
    else
        cat "$etc_hosts" > "$tmp"
        printf '127.0.1.1\t%s\n' "$host" >> "$tmp"
    fi
    cat "$tmp" > "$etc_hosts"
    rm -f "$tmp"
}

# Run the first apply, then enable the self-update timer. The timer must come on
# even when the first apply does not pass verify: a brand-new machine can fail its
# very first verify for a transient, self-correcting reason, and we want it to
# keep retrying on its own schedule rather than sit dark until someone reruns
# bootstrap by hand. The apply's exit status is remembered and returned, so
# bootstrap still ends non-zero on a failed first apply, but only after the timer
# is on. Returns the apply's exit status.
start_self_update() {
    local rc=0

    echo "==> Running the first apply"
    "${repo_dir}/bin/snaphouse" update --force || rc=$?

    echo "==> Enabling the self-update timer"
    systemctl enable --now snaphouse-update.timer

    if [ "${rc}" -ne 0 ]; then
        echo "bootstrap.sh: the first apply did not pass verify (exit ${rc}); the self-update timer is enabled and will retry" >&2
    fi
    return "${rc}"
}

main() {
    parse_args "$@"

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

    # Validate and apply --host only after cloning, so the check runs against the
    # inventory this machine will actually apply.
    if [ -n "${target_host}" ]; then
        validate_host "${target_host}" "${repo_dir}/vars/hosts.yml"
        set_hostname "${target_host}"
    fi

    # local.yml holds identifying values and the art-share password, so keep the
    # directory and file readable by root only (0700 dir, 0600 file). Fix the
    # mode on an already-present file too, so older installs are tightened on the
    # next run.
    install -d -m 0700 -o root -g root "${local_dir}"
    if [ ! -f "${local_file}" ]; then
        echo "==> Creating ${local_file} from the example; opening it for editing"
        install -m 0600 -o root -g root "${repo_dir}/local.example.yml" "${local_file}"
        "${EDITOR:-vi}" "${local_file}"
    else
        chmod 0600 "${local_file}"
    fi

    local rc=0
    start_self_update || rc=$?

    if [ "${rc}" -ne 0 ]; then
        echo "==> Bootstrap finished with errors: the first apply did not pass verify. The self-update timer is enabled and will keep trying." >&2
        exit "${rc}"
    fi

    echo "==> Bootstrap complete"
}

# Run main only when executed, not when sourced (the tests source this file to
# exercise the functions above in isolation).
if [ "${BASH_SOURCE[0]}" = "${0}" ]; then
    main "$@"
fi
