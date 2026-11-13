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
  redirect to, `openlibrary.org`, `covers.openlibrary.org`; the hub also reaches
  `geocoding-api.open-meteo.com` to resolve a typed weather city to coordinates;
  the hub's homebody Matter controller (python-matter-server) also fetches the
  Matter PAA root certificates from the CSA Distributed Compliance Ledger
  (`on.dcl.csa-iot.org`) and GitHub (`api.github.com` and
  `raw.githubusercontent.com`, the project-chip development certificates);
  the hub also pulls its pinned AirPlay 2 receiver image from Docker Hub
  (`registry-1.docker.io`, `auth.docker.io`, `production.cloudflare.docker.com`);
  plus nightstand's list on the `nightstand` host). Lookup requests carry only
  track, show, book, artist, or typed place names and a User-Agent naming the
  public repo.
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

## Frames and the `frame_hardware` flag

A frame host is one whose `roles` include `nightstand_bootstrap` (currently
`nightstand` and `office`). The hub lists every such host in its frame inventory,
so the room-picker page shows it as a frame and the house-status timer
republishes it under `/frames/<id>/` (offline until a real frame answers).

Whether a frame host actually installs the nightstand frame app is a separate,
per-host boolean: `frame_hardware` in `vars/hosts.yml`. `nightstand_bootstrap`
installs nothing (no clone, no installer, no service, no verify check, just a
debug line) where `frame_hardware` is `false`. This keeps a frame host's
self-update from running nightstand's installer before the display is wired: a
host can be in the frame list first and gain its hardware later (the office Pi 5
runs its audio with `frame_hardware: false` until its Inky is attached). When the
display is attached, flip that host's `frame_hardware` to `true` in a one-line PR;
its next self-update then clones and installs the frame app.

## The AirPlay 2 receiver is a pinned container

The hub's AirPlay input is the official `mikebrady/shairport-sync` image (5.x,
AirPlay 2 with NQPTP), pinned by tag and digest in `vars/all.yml`
(`shairport_image_*`), run by `snaphouse-airplay-in.service` as `docker run --rm`
with host networking and `--device /dev/snd`. It replaces Debian's AirPlay 1
shairport-sync package, which the `hub_input` role removes. It plays into
`hw:Loopback,0,1` directly at 44100 S16_LE stereo (the container has no host ALSA
config, so there is no plug device) and writes the same metadata pipe,
`/run/snaphouse/airplay-metadata`, that `airplay_meta.py` reads. RTSP is on 7000
(`airplay_rtsp_port`); the verify check probes it.

**Never run the image with the host D-Bus mounted unless `ENABLE_AVAHI=0`.** With
it unset, its startup launches its own `dbus-daemon`, which destroyed the host's
system bus socket (issue #138). The unit sets `ENABLE_AVAHI=0` and mounts the
host's `/run/dbus` and `/run/avahi-daemon` (at both `/run` and `/var/run`) so the
image advertises through the host's avahi; `tests/python/test_hub_input.py` pins
the pairing. The docker install and image pull are tagged `hardware`/`service`, so
the container apply skips them. To bump the image, change the tag and digest
together.

## Snapserver inputs hold through speech pauses

The two alsa sources in `roles/hub_input/templates/snapserver.conf.j2` (AirPlay
and Bluetooth) set `idle_threshold` from `snapserver_idle_threshold_ms` in
`vars/all.yml` (10000 ms). snapserver's 100 ms default reads the digital silence
between spoken sentences as the stream stopping, which flaps the House meta
stream and chops every room during audiobooks and podcasts (issue #90). Ten
seconds keeps speech pauses as playing while a real stop still goes idle;
`tests/python/test_hub_input.py` pins the parameter on both sources.

## Phone pairing runs in one long-lived bluetoothctl session

The inbound pairing window (`run_pairing_window` in `lib/snaphouse/pairing.py`)
runs the whole open in ONE long-lived `bluetoothctl` process: it registers the
DisplayYesNo agent, makes it the default, and only then (in that same session)
selects the receive adapter and turns pairable and discoverable on. With BlueZ
`AlwaysPairable=false`, setting pairable in a throwaway session and then spawning
a separate agent dropped bondable the moment that first session's agent went
away, and every phone got "Pairing Not Allowed (0x18)" (issue #128). Keep the
agent and the pairable/discoverable state in the same process; do not split them.
`test_pairing.py::test_open_commands_all_go_to_one_session_with_pairable_after_default_agent`
is the regression pin, and the bluez-side `pairable-timeout`/`discoverable-timeout`
are set to the window length so the adapter self-closes even if the process dies.

The web half is the `snaphouse-pairing` service (`lib/snaphouse/pairservice.py`,
`roles/hub_pairing`): a stdlib loopback HTTP API nginx proxies at `/api/pair/`,
on the hub only (a non-empty `bt_receive_adapter`). It is a separate service from
the art admin one because pairing needs BlueZ rights (register an agent, set
adapter properties) that the `art` user must not have; the stock Debian BlueZ
D-Bus policy grants those to root only, so the pairing service runs as root and is
hardened by systemd instead. The port is pinned in `vars/all.yml` (`pairing_port`),
shared by the unit and the nginx proxy. A window opened over the web accepts the
FIRST pairing request itself and declines later ones (the person pairing is the one
holding the phone, and Android drops its dialog when they switch to the browser to
read a code), trusts the device, and closes; the state is never `confirm`, and
`POST /api/pair/confirm` always answers 409. The CLI keeps its interactive confirm.
The page side is `roles/room_picker/files/site/pair-controller.js` (the view in
`rooms.js` is a thin renderer over it): every request has a deadline, a poll never
waits for a POST in flight, and a poll answered after an action is dropped, so one
stalled request can never freeze the buttons until a reload
(`tests/js/pair-controller.test.js`). The room-picker nginx `location /` sends
`Cache-Control: no-cache` so the HTML and its ES modules always load as one set.
Never log or return the phone's or the adapter's address; the phone's name is
returned only on the LAN page.

## Bluetooth devices list and stale pairings

Settings also lists the phones paired to the hub (`lib/snaphouse/pairdevices.py`,
served by the pairing service at `GET /api/pair/devices`, with guarded
`POST /api/pair/devices/disconnect` and `/forget`). Each device's `id` is an HMAC
of its address under a per-host secret in `/var/lib/snaphouse/pairing` (mode 0700,
the only path the otherwise read-only unit may write), so no address is ever
returned or logged; `last_connected` is stored there by id, sampled on each
request and every 30 s. The page side is `devices-controller.js` over the
`rooms-model.js` helpers, with the pairing section's deadlines and visibility
rule; forgetting asks inline first, then reminds the owner to forget the speaker
on the phone too. In a pairing window, a phone the hub already had paired that
fails authentication (the phone forgot the speaker, the hub kept the key) has its
stale pairing removed once so its next try in the same window succeeds
(`_make_line_handler` in `pairing.py`).

## Page navigation

The room-picker page has no drawer. A `<nav aria-label="Pages">` of tabs
(Listen, Frame, Gallery, Climate, Settings) is fixed to the bottom on a phone
and becomes a vertical left rail in the 800x480 kiosk media query. The URL hash
is the screen: `#listen` (home), `#frame` and `#frame/<id>`, `#gallery`,
`#climate`, `#settings`. `#art` and `#art/...` are aliases of `#gallery`. The
rules are pure in `nav-model.js` (`resolveScreen`, `visibleTabs`,
`frameTabVisible`, tests in `tests/js/nav-model.test.js`); `rooms.js` applies
them on load and on `hashchange`, and rewrites the hash with `replaceState`
when it was an alias or a hidden screen, so Back never lands on it. The current
tab carries `aria-current="page"`.

Frame is hidden until the frame-control document lists frames or a frame mirror
(`frame.json`) answers. Climate is hidden until the homebody page exists: to
unhide it, flip `CLIMATE_ENABLED` in `nav-model.js` and add a `screen-climate`
section to `index.html` and `els.screens`.

## Polling sleeps while hidden, lists patch in place

Every polling interval in `rooms.js` registers with `page-timers.js`; the
`visibilitychange` handler pauses them all while the tab is hidden and resumes them
(then refreshes once) on return. The socket and its reconnect are separate. The
devices list and the art grid patch by key (`keyed-list.js`): each row carries a
signature of everything that affects its DOM, so only a changed row is replaced and
a poll that changes nothing leaves focus, hover and thumbnails alone. The now-playing
progress fill is a full-width element moved by `scaleX` in one `requestAnimationFrame`
loop that runs only while a track is playing and the tab is visible; the time text is
written only when the whole second changes. Tests: `tests/js/page-timers.test.js`,
`tests/js/keyed-list.test.js`.

## Volume slider

Each room's volume is a custom `div role="slider"` (`volume-slider.js`), not an
`<input type=range>`, so it follows the finger and never snaps back. The rules
live in `volume-controller.js`, the pure part (no DOM, clock and transport
injected), pinned by `tests/js/volume-controller.test.js`: at most one write per
100 ms with a trailing send of the final value, at most one request in flight per
room (the WebSocket path is fire-and-forget, so it holds the slot for the
100 ms gap only; the HTTP fallback returns its promise from `sendControl`, so it
holds the slot until the promise settles), status for a room is ignored while it is dragged, and after release the
user's value is held until the hub reports within 1 of it or 2 s pass. Fill and
thumb move once per animation frame from a cached track width: the fill by `clip-path: inset()` (a scaled fill rasterizes once and shows blocky stripes on Android), the thumb by the `translate` property (never `transform`, which the held `scale: 1.2` would stretch past the track end).
Haptics (`haptics.js`, Android `navigator.vibrate`, feature-detected) fire only
from pointer, key and tap handlers, never from a status update.

## mDNS advertises IPv4 only

The `common` role sets `use-ipv6=no` ([server]) and `publish-aaaa-on-ipv4=no`
([publish]) in `/etc/avahi/avahi-daemon.conf` on every host, so `.local` names
resolve to IPv4 only. A phone VPN (for example Proton with Allow LAN
connections) exempts private IPv4 ranges from the tunnel but not global IPv6, so
a name that also carried a global IPv6 address would be tried over IPv6 first and
sent into the tunnel: the IP worked but `house.local` did not. The `avahi_mdns`
verify check pins both settings on device.

## Wi-Fi power saving is off

The `common` role writes `/etc/NetworkManager/conf.d/snaphouse-wifi-powersave.conf`
(`wifi.powersave = 2`, disable) where NetworkManager is present, reloads it by
handler, and switches the running `wlan0` off once with `iw` when it was on.
NetworkManager's default leaves power saving on, which makes the radio doze
between packets and underruns the Wi-Fi rooms (issue #140). The `wifi_powersave.sh`
verify check fails if `iw` reports it on, and passes where there is no `wlan0`.

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

## Cover color

The page color follows the now-playing cover. `cover-color.js` is pure at its core
(`extractHues` turns canvas pixels into `{h1, h2, chroma}` via OKLab, ignoring
transparent, near-black, near-white and near-grey pixels); only `loadCoverHues`
(a 32x32 canvas, same-origin `/cover/...` so `getImageData` is allowed) and
`swapImage` (preload and `decode()`, then set `src`, so the image never blanks)
touch the DOM. `applyCoverColor` writes `--h`, `--h2` and `--k` on the root, and
every token in `style.css` is OKLCH derived from them. The default hue is 255
(slate blue). A greyscale or missing cover returns the defaults with chroma 0, so
`--k` (`0.15 + 0.85 * chroma`) scales chroma down to near-neutral instead of
picking a random hue. `@property` registers the three variables so the hue eases
over 1.4 s; browsers without `@property` change hue instantly, and reduced motion
turns the easing off. In `rooms.js`, `followCover` guards out-of-order results with
a token. The frame mirror image only preloads through `swapImage`; the now-playing
cover is the single source of color. Tests: `tests/js/cover-color.test.js`.

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
