# snaphouse Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Each task maps to one GitHub issue, one Orca worktree, and one PR. Tasks labeled `needs-owner` run over SSH with the owner present; never wipe, reinstall, or remove anything on a machine without the owner's explicit go-ahead in that session.

**Goal:** Synced audio in three rooms from AirPlay and Bluetooth senders, a room-picker page, and machines that rebuild from one bootstrap command and update themselves from `main` with a safe fallback.

**Architecture:** One Ansible playbook, `site.yml`, applied locally on each machine by a self-update wrapper (git fetch, `ansible-playbook -c local`, `snaphouse verify`, fall back to last-good). The roles each machine gets come from its hostname in `vars/hosts.yml`. Identifying values live in the untracked `/etc/snaphouse/local.yml`. The few pieces of logic that aren't configuration (status writer, update wrapper, AVRCP metadata bridge, client settings, room-picker model) are small scripts with unit tests.

**Tech Stack:** Ansible (ansible-core), Debian / Raspberry Pi OS Lite 64-bit, Snapcast (snapserver, snapclient, pinned release .debs), BlueZ + BlueALSA (`bluez-alsa-utils`), shairport-sync, nginx, Samba, avahi, unattended-upgrades, Python 3.11 + pytest (uv), plain ES-module JavaScript + `node --test`, GitHub Actions, shellcheck, ansible-lint.

**Spec:** `docs/superpowers/specs/2026-11-06-snaphouse-design.md`; contracts in `docs/cross-repo-features.md` (see Pinned contracts).

## Global Constraints

- Hostnames: `zimablade` (hub, living room), `pi-office`, `pi-bedroom`. Room labels: "Living room", "Office", "Bedroom". snapclient host IDs: `living`, `office`, `bedroom`.
- Nothing identifying in the repo: no MACs, IPs, Wi-Fi details, usernames, real names, location. Those go in `/etc/snaphouse/local.yml`; the repo ships `local.example.yml` with placeholders.
- No inbound access from the internet. Outbound per machine: GitHub, OS mirrors, NTP (plus nightstand's list on `pi-bedroom`). No telemetry.
- Bluetooth inbound pairing is open only inside a window of 120 seconds started by `snaphouse pair phone`.
- AirPlay and Bluetooth speaker name: "House". The meta stream is "House", with AirPlay listed before Bluetooth (AirPlay wins a tie).
- Update timer: every 15 minutes, `RandomizedDelaySec=120`. Security updates are automatic, with reboots at 03:15 (`zimablade`), 03:45 (`pi-office`), 04:15 (`pi-bedroom`).
- Status file schema and paths, art listing, `/jsonrpc` proxy: exactly as in `docs/cross-repo-features.md`, Pinned contracts.
- Room picker: static files, no build step, no server-side runtime beyond nginx.
- Living room "off" mutes only; the Vizio Bluetooth link stays connected.
- Sender volume (AirPlay, Bluetooth AVRCP) moves house volume; per-room sliders set each room.
- No em dashes in prose, comments, or UI text.

## Review Focus

1. The bedroom JBL is off for days: `pi-bedroom` must not fill the journal (one log line per connect/disconnect transition), and snapclient must recover within 60 s of the speaker powering on. Test in Task 6.
2. Bluetooth adapter numbering swaps after a reboot (hci0 and hci1 reversed): roles resolve adapters by MAC from `local.yml`, never by index. Test in Task 4.
3. An update lands while the hub is offline or GitHub is unreachable: the wrapper exits cleanly, changes nothing, and `status.json` still reports the running commit. Test in Task 3.
4. A bad commit on `main` that passes CI but fails `verify` on one machine: that machine returns to last-good, doesn't retry the same commit every 15 minutes, and the room picker shows the warning. Tests in Tasks 3 and 7.
5. A phone sending Bluetooth with no metadata (a podcast app, or AVRCP off): the stream still plays, and `Server.GetStatus` simply lacks a title, so nightstand falls through to art. Test in Task 5.

---

## File map

```
ansible.cfg
site.yml                      # one play, hosts: localhost; includes roles listed for the hostname
vars/hosts.yml                # hostname -> roles, room label, host id, output, latency_ms
vars/all.yml                  # snapcast_version, shairport options, update interval, reboot times
local.example.yml             # documented placeholders for /etc/snaphouse/local.yml
bootstrap.sh                  # first-run on a fresh machine
bin/snaphouse                 # CLI: pair phone | pair speaker | verify | status | update
lib/snaphouse/                # python: status.py, update.py, avrcp_meta.py, clients.py, adapters.py
roles/common, selfupdate, web, bluetooth, hub_input, metadata, snapclient, bt_speaker,
      room_picker, art_share, nightstand_bootstrap
roles/*/files/verify.d/*.sh   # each role installs its own checks into /etc/snaphouse/verify.d/
roles/room_picker/files/site/ # index.html, rooms.js, rooms-model.js, style.css
tests/python/, tests/js/, tests/container/
.github/workflows/ci.yml
AGENTS.md, CLAUDE.md (shim: `@AGENTS.md`)
```

---

### Task 0: Hub recon and OS decision (needs-owner)

No code. Output is a comment on the issue and an update to the design's "Open items".

- [ ] **Step 1:** Find the hub on the LAN: try `casaos.local` / `zimaos.local`, then the router's device list, then `nmap -sn <home subnet>/24` (the owner confirms the subnet). Expected: an address that serves the Casa/Zima web UI.
- [ ] **Step 2:** The owner supplies credentials from their password manager. SSH in. Run `cat /etc/os-release; uname -a; df -h /; lsblk; docker ps -a; lsusb`. Expected: the OS identified, the dongles and hub visible in `lsusb` (two `Cambridge Silicon Radio` entries).
- [ ] **Step 3: Decision gate.** CasaOS: list the Docker apps and get the owner's per-app OK before removing any. ZimaOS: stop, record the finding, and get the owner's go-ahead for a Debian stable reinstall (separate issue, labeled `needs-owner`).
- [ ] **Step 4:** Record the result (OS, version, disk, RAM, dongles seen) on the issue, without addresses or MACs.

---

### Task 1: Repo scaffold, CLI skeleton, CI

**Files:**
- Create: `ansible.cfg`, `site.yml`, `vars/{hosts,all}.yml`, `local.example.yml`, `bootstrap.sh`, `bin/snaphouse`, `lib/snaphouse/__init__.py`, `pyproject.toml`, `.github/workflows/ci.yml`, `tests/container/run.sh`, `AGENTS.md`, `CLAUDE.md`
- Test: `tests/python/test_hosts.py`

**Interfaces:**
- Produces:
  - `vars/hosts.yml` keys per host: `roles: [..]`, `room_label`, `host_id`, `output: {kind: alsa|bluetooth, device: str|None, speaker: vizio|jbl|None}`, `latency_ms: int` (start at 0).
  - `site.yml`: `hosts: localhost`, `connection: local`, loads `vars/all.yml`, `vars/hosts.yml`, `/etc/snaphouse/local.yml`; `snaphouse_host` defaults to `ansible_hostname` and is overridable with `-e`; includes `hosts[snaphouse_host].roles` in order.
  - Tags: every task touching hardware is tagged `hardware`; every service start/restart is tagged `service`.
  - `bootstrap.sh`, run as root on a fresh machine: installs `git ansible-core python3`, clones `https://github.com/doyled-it/snaphouse` to `/opt/snaphouse`, copies `local.example.yml` to `/etc/snaphouse/local.yml` if missing and opens it in `$EDITOR`, runs the first apply via `bin/snaphouse update --force`, enables the update timer.
  - `bin/snaphouse` dispatches subcommands to `lib/snaphouse` modules; unknown subcommands print usage and exit 2.
  - CI jobs: `ansible-lint`; `ansible-playbook --syntax-check site.yml`; `uv run pytest tests/python`; `node --test tests/js`; `shellcheck bootstrap.sh bin/* roles/*/files/**/*.sh`; container apply: for each host in `vars/hosts.yml`, run `site.yml` in `debian:bookworm` with `-e snaphouse_host=<host> --skip-tags hardware,service` and a placeholder `local.yml`.

- [ ] **Step 1: Failing test:** `test_hosts.py` asserts that every host lists `common`, `web`, and `selfupdate` first, every role named exists under `roles/`, the `host_id`s are unique and in {living, office, bedroom}, and no value in `vars/` or `local.example.yml` matches a MAC (`([0-9A-F]{2}:){5}[0-9A-F]{2}`, case-insensitive, except `00:00:00:00:00:00`) or an IPv4 private address.
- [ ] **Step 2:** Run `uv run pytest tests/python -q`. Expected: FAIL.
- [ ] **Step 3:** Implement with empty role directories (`tasks/main.yml` containing `[]`).
- [ ] **Step 4:** Run the tests and `tests/container/run.sh`. Expected: PASS; the container apply succeeds for all three hosts.
- [ ] **Step 5: Commit** `feat: scaffold ansible layout, CLI, CI`.

---

### Task 2: common and web roles, status writer

**Files:**
- Create: `roles/common/*`, `roles/web/*`, `lib/snaphouse/status.py`
- Test: `tests/python/test_status.py`

**Interfaces:**
- Produces:
  - `common`: packages (`avahi-daemon`, `unattended-upgrades`, `jq`, `curl`); hostname set to `snaphouse_host`; unattended-upgrades security origin only, `Automatic-Reboot "true"`, `Automatic-Reboot-Time` from `vars/all.yml` per host.
  - `web`: nginx serving `/status.json` from `/var/lib/snaphouse/public/` with `Access-Control-Allow-Origin: *`; on `pi-bedroom` also `location /nightstand/` aliasing `/var/lib/nightstand/public/` with the same header; a per-role include dir `/etc/nginx/snaphouse.d/` for later roles.
  - `write_status(path: Path, *, host: str, commit: str, main_commit: str, last_good: str, verify: Literal["pass","fail"], now: datetime) -> None`: atomic write (temp file plus rename) in the Pinned contracts schema with `repo="snaphouse"`.
  - verify check `web.sh`: `curl -fsS http://localhost/status.json | jq -e .host`.

- [ ] **Step 1: Failing tests:** the schema keys exactly match the contract; the write is atomic (a reader never sees partial JSON: the temp file sits in the same dir and gets renamed); `checked_at` is ISO 8601 with offset.
- [ ] **Step 2:** Run. Expected: FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run the tests plus the container apply. Expected: PASS.
- [ ] **Step 5: Commit** `feat: common and web roles, status file`.

---

### Task 3: Self-update with last-good fallback, verify framework

**Files:**
- Create: `roles/selfupdate/*` (service, timer, `/etc/snaphouse/verify.d/`), `lib/snaphouse/update.py`, `lib/snaphouse/verify.py`
- Test: `tests/python/test_update.py`, `tests/python/test_verify.py`

**Interfaces:**
- Consumes: `write_status`.
- Produces:
  - `snaphouse verify`: runs every executable in `/etc/snaphouse/verify.d/` (sorted, 60 s timeout each) and prints `PASS name` / `FAIL name: <last stderr line>`; exits 0 only if all pass.
  - `snaphouse update [--force]` state, kept in `/var/lib/snaphouse/state.json` as `{last_good, failed}`. Algorithm:

```
fetch origin main (network failure: write status with current commit, exit 0)
target = origin/main
if not force and (target == HEAD == last_good or target == failed): write status, exit 0
checkout target; apply (ansible-playbook site.yml -c local); verify
if apply and verify pass: last_good = target; failed = None
else: failed = target; checkout last_good; apply; verify
write status (verify result of what is now running); exit 0 unless the fallback also failed
```

  - Timer per Global Constraints. `snaphouse status` prints the status JSON.

- [ ] **Step 1: Failing tests** against a temp bare repo and PATH shims for `ansible-playbook` and the verify dir:
  - a good new commit becomes `last_good`;
  - a commit failing verify is rolled back, recorded as `failed`, and not retried on the next run;
  - a newer commit after a failure is tried;
  - fetch failure changes nothing and still writes status;
  - `--force` re-applies HEAD.
- [ ] **Step 2:** Run. Expected: FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run. Expected: PASS.
- [ ] **Step 5: Commit** `feat: self-update with last-good fallback`.

---

### Task 4: Bluetooth role, adapter resolution, pairing CLI

**Files:**
- Create: `roles/bluetooth/*`, `lib/snaphouse/adapters.py`, `lib/snaphouse/pairing.py`
- Test: `tests/python/test_adapters.py`, `tests/python/test_pairing.py`

**Interfaces:**
- Produces:
  - `local.yml` keys: `bt_receive_adapter` (hub only), `bt_send_adapter` (hub and pi-bedroom; on pi-bedroom the onboard adapter), `speakers: {vizio: MAC, jbl: MAC}` (filled by pairing).
  - `resolve_adapter(mac: str, sys_root: Path = Path("/sys/class/bluetooth")) -> str` returns `hciN` by reading `hci*/address`; raises `AdapterNotFound` naming the MAC's last two octets only.
  - `bluetooth` role: `bluez`, `bluez-tools`, `bluez-alsa-utils`; bluealsa service with `-p a2dp-sink` on the hub and `-p a2dp-source` on hosts with a Bluetooth output; adapters made non-discoverable and non-pairable at boot.
  - `snaphouse pair phone` (hub): on the receive adapter, sets discoverable and pairable, runs `bt-agent -c NoInputNoOutput` for 120 s, then turns both off; newly paired devices are trusted. `snaphouse pair speaker {vizio|jbl}`: scans 20 s on the send adapter, lists named devices, the owner picks one, then pair, trust, connect, and write the MAC to `local.yml` (preserving comments).
  - verify `bluetooth.sh`: both configured adapters resolve, and neither is discoverable.

- [ ] **Step 1: Failing tests:** `resolve_adapter` against a fake sys tree where hci0 and hci1 are swapped between two calls; an unknown MAC raises with a redacted message; the pairing window closes after 120 s, even when the agent process exits early (fake clock and process); the `local.yml` writer keeps comments and other keys.
- [ ] **Step 2:** Run. Expected: FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run. Expected: PASS.
- [ ] **Step 5: Commit** `feat: bluetooth role and pairing`.
- [ ] **Step 6 (needs-owner, hub):** Apply on the hub and run `snaphouse pair phone` with the owner's Android. Expected: it pairs inside the window; after the window `bluetoothctl show` reports `Discoverable: no`.

---

### Task 5: Hub inputs and metadata

**Files:**
- Create: `roles/hub_input/*` (snd-aloop, bluealsa-aplay unit, snapserver install and `snapserver.conf`), `roles/metadata/*`, `lib/snaphouse/avrcp_meta.py`
- Test: `tests/python/test_avrcp_meta.py`

**Interfaces:**
- Consumes: `resolve_adapter`.
- Produces:
  - `snd-aloop` loaded at boot. `snaphouse-bt-in.service`: `bluealsa-aplay --pcm=hw:Loopback,0,0 00:00:00:00:00:00` (any connected phone).
  - snapserver from the pinned `snapcast_version` release .deb, chosen at build time as the newest release that supports the `meta` stream, the `airplay` stream with metadata, and stream `controlscript`; record the version and the reason in `vars/all.yml`. Config:

```
[stream]
source = airplay:///shairport-sync?name=AirPlay&devicename=House
source = alsa:///?name=Bluetooth&device=hw:Loopback,1,0&controlscript=/opt/snaphouse/lib/snaphouse/avrcp_meta.py
source = meta:///AirPlay/Bluetooth?name=House
[http]
enabled = true
bind_to_address = 0.0.0.0
port = 1780
```

  (Exact parameter names follow the pinned version's docs; adjust and note them in the PR.)
  - `avrcp_meta.py`: a snapserver stream control script (JSON-RPC over stdin/stdout per Snapcast's control-script API). It watches BlueZ `org.bluez.MediaPlayer1` `Track` and `Status` over D-Bus and emits `Plugin.Stream.Player.Properties` with `metadata.title`, `metadata.artist` (list), `metadata.album`, and `playbackStatus`. Pure mapping function: `track_to_metadata(track: dict) -> dict` (missing keys omitted, empty strings dropped).
  - Sender volume to house volume: shairport-sync applies AirPlay volume to the stream itself (its `ignore_volume_control = "no"`, software volume). For Bluetooth, `bluealsa-aplay` applies the phone's AVRCP volume in software (`--volume=software` or the pinned version's equivalent). Either way the stream is attenuated at the source, so every room moves together and keeps its relative level.
  - Verify `hub_input.sh`: snapserver active, `Server.GetStatus` lists streams AirPlay, Bluetooth, House.

- [ ] **Step 1: Failing tests:** `track_to_metadata` on full, partial, and empty tracks (empty means only `playbackStatus`, for Review Focus 5); a fake stdin/stdout round-trip of the control-script handshake yields one `Properties` notification per track change.
- [ ] **Step 2:** Run. Expected: FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run. Expected: PASS.
- [ ] **Step 5: Commit** `feat: hub inputs and metadata`.
- [ ] **Step 6 (needs-owner, hub):** Play from the Android and from an Apple device in turn. Expected: `curl -s -d '{"id":1,"jsonrpc":"2.0","method":"Server.GetStatus"}' localhost:1780/jsonrpc | jq '.result.server.streams[] | {id, status, m: .properties.metadata}'` shows House playing with title and artist, plus `artUrl` for AirPlay. Change volume on each sender; expected: the level changes everywhere. Save one response (scrubbed of names) as `tests/fixtures/server_status_playing.json` and open a nightstand issue to replace its hand-written fixtures.

---

### Task 6: snapclient, Bluetooth speaker keepalive, client settings

**Files:**
- Create: `roles/snapclient/*`, `roles/bt_speaker/*`, `lib/snaphouse/clients.py`
- Test: `tests/python/test_clients.py`, `tests/python/test_keepalive.py`

**Interfaces:**
- Consumes: `resolve_adapter`, the `speakers` MACs.
- Produces:
  - snapclient (pinned version) run as `--host zimablade.local --hostID {host_id} --player alsa --soundcard {device}`. For Bluetooth outputs the device is an ALSA alias `snaphouse_bt` defined in `/etc/asound.conf` as `bluealsa` with `DEV={speaker MAC}` and `PROFILE=a2dp`. Office: the USB adapter's ALSA card, found with `aplay -L` during Task 10 and stored in `vars/hosts.yml` by card name (not index).
  - `bt_speaker`: `snaphouse-bt-keepalive.timer` every 30 s runs `keepalive(mac) -> Literal["connected","connecting","absent"]`, which calls `bluetoothctl connect` only when not connected and logs only on state transitions.
  - `apply_client_settings(rpc, clients: dict[host_id, {label, latency_ms}]) -> list[str]` (hub only, run by the role after snapserver is up): `Client.SetName` and `Client.SetLatency` for every known client present; returns the ids it changed. Re-running is a no-op.
  - Verify `snapclient.sh`: the snapclient service is active. `bt_speaker.sh` passes when the speaker is connected *or* absent (absence is normal for the JBL).

- [ ] **Step 1: Failing tests:** `apply_client_settings` sends only the needed requests (fake RPC), skips absent clients, and is idempotent; keepalive logs once across 100 "absent" cycles and once on the transition to connected.
- [ ] **Step 2:** Run. Expected: FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run. Expected: PASS.
- [ ] **Step 5: Commit** `feat: snapclient, speaker keepalive, client settings`.
- [ ] **Step 6 (needs-owner, hub):** Run `snaphouse pair speaker vizio`; play music. Expected: the living room plays. With the TV on, confirm TV audio still plays through the bar.

---

### Task 7: Room picker page

**Files:**
- Create: `roles/room_picker/*` (nginx include for `/`, `/jsonrpc` proxy with WebSocket upgrade, `rooms.json` template from `vars/hosts.yml`), `roles/room_picker/files/site/{index.html,rooms.js,rooms-model.js,style.css}`
- Test: `tests/js/rooms-model.test.js`

**Interfaces:**
- Produces (`rooms-model.js`, pure ES module):
  - `roomsFromStatus(status, labels) -> [{id, label, connected, on, volume}]`, ordered as in `labels` (`on` = not muted and group not muted).
  - `nowPlaying(status) -> {title, artist} | null`.
  - `toggleRequest(id, on) -> {method: "Client.SetVolume", params: {id, volume: {muted: !on}}}`.
  - `volumeRequest(id, percent) -> {method: "Client.SetVolume", params: {id, volume: {percent}}}`, clamped 0 to 100.
  - `healthBanner(statuses: (object|null)[]) -> string | null`: names the hosts where `verify != "pass"`, `commit != main_commit`, or the status is unreachable.
- UI: one card per room with a large toggle and a slider; the now-playing line at the top; a health banner when non-null; refreshes over the snapserver WebSocket notifications at `/jsonrpc`, falling back to a 5 s poll. A `manifest.webmanifest` lets it save to Android and iPhone home screens.

- [ ] **Step 1: Failing tests:** mapping from a status fixture; a muted group makes all its rooms off; clamping; `healthBanner` names a failing host and an unreachable one, and returns null when all pass.
- [ ] **Step 2:** Run `node --test tests/js`. Expected: FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run. Expected: PASS.
- [ ] **Step 5: Commit** `feat: room picker page`.

---

### Task 8: Art share

**Files:**
- Create: `roles/art_share/*`
- Test: container apply plus a verify script

**Interfaces:**
- Produces: `/srv/snaphouse/art`; Samba share `[art]`, writable, with user `art` and password `art_share_password` from `local.yml`; nginx `location /art/ { alias /srv/snaphouse/art/; autoindex on; autoindex_format json; }` as in Pinned contracts. Verify `art_share.sh`: `curl -fsS http://localhost/art/ | jq -e 'type=="array"'`, and `smbclient -L localhost -N` lists `art`.

- [ ] **Step 1:** Write `art_share.sh` first; run it in the container. Expected: FAIL.
- [ ] **Step 2:** Implement the role.
- [ ] **Step 3:** Run the container apply plus `art_share.sh`. Expected: PASS.
- [ ] **Step 4: Commit** `feat: art share`.

---

### Task 9: nightstand bootstrap on pi-bedroom

**Files:**
- Create: `roles/nightstand_bootstrap/*`

**Interfaces:**
- Consumes: nightstand's `deploy/install.sh` (nightstand plan, Task 9).
- Produces: if `/opt/nightstand` is absent, clone `https://github.com/doyled-it/nightstand` and run `deploy/install.sh` once (`creates: /etc/nightstand/local.toml`). After that this role never touches nightstand-owned paths. Verify `nightstand.sh`: `systemctl is-enabled nightstand` (pass if enabled; the frame's own health is reported through its status file).

- [ ] **Step 1:** Container apply with `-e snaphouse_host=pi-bedroom`, using a stub nightstand repo URL via a variable. Expected: the install runs once; a second apply runs zero tasks with `changed`.
- [ ] **Step 2: Commit** `feat: bootstrap nightstand on pi-bedroom`.

---

### Task 10: Rollout, tuning, reboot proof (needs-owner)

- [ ] **Step 1:** Flash both Pis with Raspberry Pi Imager: Raspberry Pi OS Lite 64-bit, hostname, user, SSH key, 5 GHz Wi-Fi. `ssh pi-office.local true` works.
- [ ] **Step 2:** `pi-office`: remove the old Bluetooth receiver, plug in the USB adapter, wire 3.5 mm to aux, run `bootstrap.sh`, set the card name in `vars/hosts.yml` via PR. Expected: the office plays.
- [ ] **Step 3:** `pi-bedroom`: run `bootstrap.sh`, `snaphouse pair speaker jbl`. Expected: the bedroom plays; JBL off and back on recovers within 60 s; the frame comes up.
- [ ] **Step 4:** Latency: play a track with clear transients, walk the rooms, adjust in Snapweb until locked, copy the values into `vars/hosts.yml` via PR. Expected: after the update tick, `Server.GetStatus` shows the committed latencies.
- [ ] **Step 5:** Reboot each machine. Expected: all rooms play and the status files report `verify: pass` with no manual steps.
- [ ] **Step 6:** Merge a trivial change (a comment in `vars/all.yml`). Expected: all three `commit` values match the new `main` within 20 minutes.

---

## Order and dependencies

0 → 1 → 2 → 3 → 4 → 5 → 6 → (7, 8, 9 in parallel) → 10. Tasks 1 to 3 and 7 need no hardware. Task 9 needs nightstand Task 9.
