#!/usr/bin/env bash
# snaphouse-managed-check: self-update prunes this when the applied checkout drops it
# snaphouse journal check (issue #178): the journal is kept on disk across
# restarts, so a lost link or a crash can be diagnosed afterwards. Needs the
# journald drop-in and a journal directory with files in it. Run by
# `snaphouse verify` from /etc/snaphouse/verify.d.
set -euo pipefail

dropin=/etc/systemd/journald.conf.d/snaphouse-persistent.conf
if ! grep -Eq '^Storage=persistent[[:space:]]*$' "$dropin" 2>/dev/null; then
    echo "journald is not set to Storage=persistent in ${dropin}" >&2
    exit 1
fi

if [ ! -d /var/log/journal ]; then
    echo "/var/log/journal does not exist" >&2
    exit 1
fi

if ! find /var/log/journal -name '*.journal' -print -quit | grep -q .; then
    echo "no journal files under /var/log/journal yet" >&2
    exit 1
fi
