# AGENTS.md

Guidance for coding agents working in `snaphouse`. Read this before making
changes. It is the short version; the full design is in
`docs/superpowers/specs/2026-11-06-snaphouse-design.md` and the cross-repo
contracts are in `docs/cross-repo-features.md`.

## What this repo is

Synced whole-house audio on Snapcast across three rooms: a ZimaBlade hub
(`house`, living room) and two Raspberry Pis (`office`, `nightstand`).
The repo is the whole system written down. Each machine applies this repo to
itself with Ansible in pull mode: a systemd timer fetches `main`, runs
`site.yml` with `connection: local`, runs `snaphouse verify`, and falls back to
the last-good commit if the apply or verify fails.

## The hard rules

- **No identifying data in the repo, ever.** No MAC or IP addresses, Wi-Fi
  details, usernames, real names, or home location. These live only in the
  untracked `/etc/snaphouse/local.yml` (shape shown by `local.example.yml`). The
  same rule covers web requests and search queries made while working here: do
  not put the household's details into them. `tests/python/test_hosts.py`
  enforces the no-MAC, no-private-IP part on `vars/` and `local.example.yml`.
- **No em dashes** in code, comments, docs, commit messages, or UI text.
- **No inbound access from the internet.** No tunnels, port forwards, or
  cloud-hosted control. Outbound is limited per machine (GitHub, OS mirrors,
  NTP; the hub (`house`) also reaches the now-playing cover-lookup catalogues:
  `itunes.apple.com` and the `*.mzstatic.com` artwork CDN, `musicbrainz.org`,
  `coverartarchive.org` and the `archive.org` hosts it and Open Library covers
  redirect to, `openlibrary.org`, `covers.openlibrary.org`; plus nightstand's
  list on the `nightstand` host). Lookup requests carry only track, show, book,
  or artist names and a User-Agent naming the public repo.
- **TDD.** Write the failing test, watch it fail, implement, watch it pass. Run
  the focused test while iterating and the full suite before committing.

## Layout

```
ansible.cfg            local-only defaults
site.yml               the one play; applies snaphouse_hosts[snaphouse_host].roles in order
vars/hosts.yml         per host: roles, room_label, host_id, output, latency_ms
vars/all.yml           shared settings (update cadence, reboot times, stream names)
local.example.yml      documented placeholders for /etc/snaphouse/local.yml
bootstrap.sh           first run on a fresh machine (root); --host <key> names it first
bin/snaphouse          CLI: pair | verify | status | update
lib/snaphouse/         Python behind the CLI (status, update, pairing, metadata, ...)
roles/<name>/          one role per capability; empty roles are filled task by task
tests/python/          pytest (uv)
tests/js/              node --test (room picker model)
tests/container/run.sh container apply of site.yml for every host
.github/workflows/ci.yml
```

## How a machine selects its config

`site.yml` targets `localhost`. `snaphouse_host` defaults to the machine's
hostname and can be overridden with `-e snaphouse_host=<name>` (the container
check does this to exercise every host). The chosen host's `roles` list comes
from `snaphouse_hosts[snaphouse_host]` in `vars/hosts.yml` (the top-level key is
`snaphouse_hosts`, not `hosts`, because `hosts` is an Ansible reserved keyword)
and is applied in order; the first three are always `common`, `web`,
`selfupdate`. Tag every hardware task `hardware` and every service start or
restart `service`, so the container check can skip them.

Because selection is by hostname, a machine must already be named after its
inventory key before the first apply. A repurposed machine still carrying
another name (the ZimaBlade shipped as `casaos`) is named with
`bootstrap.sh --host <key>`, which validates the key against `vars/hosts.yml`
and sets the hostname before applying.

## Snapserver inputs hold through speech pauses

The two alsa sources in `roles/hub_input/templates/snapserver.conf.j2` (AirPlay
and Bluetooth) set `idle_threshold` from `snapserver_idle_threshold_ms` in
`vars/all.yml` (10000 ms). snapserver's 100 ms default reads the digital silence
between spoken sentences as the stream stopping, which flaps the House meta
stream and chops every room during audiobooks and podcasts (issue #90). Ten
seconds keeps speech pauses as playing while a real stop still goes idle;
`tests/python/test_hub_input.py` pins the parameter on both sources.

## mDNS advertises IPv4 only

The `common` role sets `use-ipv6=no` ([server]) and `publish-aaaa-on-ipv4=no`
([publish]) in `/etc/avahi/avahi-daemon.conf` on every host, so `.local` names
resolve to IPv4 only. A phone VPN (for example Proton with Allow LAN
connections) exempts private IPv4 ranges from the tunnel but not global IPv6, so
a name that also carried a global IPv6 address would be tried over IPv6 first and
sent into the tunnel: the IP worked but `house.local` did not. The `avahi_mdns`
verify check pins both settings on device.

## Verify checks and self-update pruning

A role installs an on-device check by copying or templating it to
`/etc/snaphouse/verify.d/<name>`, and every shipped check carries the
`snaphouse-managed-check` marker comment near the top. On a fallback the
self-update wrapper prunes marked checks the applied checkout no longer installs
(`lib/snaphouse/update.py`), so a rolled-back commit does not keep failing on a
check only the newer, failed commit shipped.

For that pruning to be safe, install each check with one static `dest` line:
`dest: /etc/snaphouse/verify.d/<name>`, bare or quoted, with no loop item, no
variable, and no templated path. The pruner reads the expected set from those
`dest` lines across the host's roles (both `.yml` and `.yaml` task files). A
dest it cannot read as a static name makes it skip pruning for that run rather
than risk deleting a check, and `tests/python/test_update_prune.py` fails CI if a
shipped check (any `roles/*/files/verify.d/*` or any verify-check template) is not
captured by the derivation, so a new install shape is caught in review, not in
the field.

## Tooling

- Python: `uv run pytest tests/python`. Dev deps (pytest, pyyaml) are in
  `pyproject.toml`; `uv.lock` is committed.
- Python package management is always uv, on the machines too. Never install
  Python libraries with apt (`python3-*`) or pip. The `common` role installs a
  pinned uv from GitHub (`uv_version` in `vars/all.yml`); on-device Python runs
  from a uv-synced venv off `pyproject.toml`/`uv.lock` (`uv sync --frozen
  --no-dev`), with runtime deps kept separate from the dev group. Stdlib-only
  modules (like `lib/snaphouse/status.py`) need no venv.
- JavaScript: `node --test tests/js` (plain ES modules, no build step).
- Ansible: `ansible-playbook --syntax-check site.yml`, and `ansible-lint`.
- Shell: `shellcheck`.
- Container apply: `tests/container/run.sh` (needs docker).

CI runs all of the above on every pull request. `main` is protected; changes
land by PR with CI green.

## Cross-repo

`snaphouse` is the hub of a two-repo system; `nightstand` (the bedside e-ink
frame on the `nightstand` host) consumes snapserver's API and the art share. The seams
and pinned contracts (status file schema, art listing, the art index and control files,
`/jsonrpc` proxy) are in `docs/cross-repo-features.md`. A change to a seam updates that
file and both repos' docs in the same PR set. The pull-request template carries the
completeness checklist.
