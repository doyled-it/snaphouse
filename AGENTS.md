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
  NTP; plus nightstand's list on the `nightstand` host).
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
and pinned contracts (status file schema, art listing, `/jsonrpc` proxy) are in
`docs/cross-repo-features.md`. A change to a seam updates that file and both
repos' docs in the same PR set. The pull-request template carries the
completeness checklist.
