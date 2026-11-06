# Cross-repo feature guide

A feature in this system usually spans more than one repo. The seam between repos
(the API contract, shared config, deploy wiring, and each repo's docs) is where a
change ships "done" in one repo and never reaches users. This guide is the
checklist to stop that.

## Start here: run your agent from the hub

For cross-repo work, run your coding agent from the hub repo (`snaphouse`) with
the other repos checked out as siblings. If one is missing, clone it:

```
git clone git@github.com:doyled-it/snaphouse.git
git clone git@github.com:doyled-it/nightstand.git
```

## The repos and what they own

- **snaphouse**: whole-house audio and the machines it runs on. Owns the hub
  (`zimablade`: snapserver, AirPlay and Bluetooth inputs, room-picker page, art share),
  the two Pi audio clients, base OS configuration on all three machines, and the first
  bootstrap of every device, including `pi-bedroom`. Exposes snapserver's JSON-RPC and
  HTTP API on the LAN, and the art share.
- **nightstand**: the bedside e-ink frame on `pi-bedroom`. Consumes snapserver's API and
  the art share. After its first install (run by the snaphouse bootstrap) it owns
  everything display-specific on `pi-bedroom`: SPI/I2C, the `inky` library, its service,
  its self-update timer, and its art sync.

## The seams (where features leak)

- **Now-playing metadata (snaphouse produces, nightstand consumes).** snapserver JSON-RPC
  `Server.GetStatus` on the hub: the active stream's playback status and its metadata
  properties (title, artist, album, art URL when present), plus each client's mute state
  and name, which give "rooms playing". snaphouse must get AirPlay metadata and art, and
  Bluetooth AVRCP metadata, into those stream properties. nightstand keeps a recorded
  `Server.GetStatus` response as a test fixture; when the shape changes on the snaphouse
  side, re-record it.
- **Art share (snaphouse produces, nightstand consumes).** A folder on the hub: writable
  over SMB for people, read-only to `pi-bedroom` over a LAN pull (mechanism chosen in
  snaphouse and recorded here). nightstand syncs it to a local cache on a timer.
- **Device ownership on `pi-bedroom`.** snaphouse bootstrap installs the OS base and
  audio, clones nightstand, and runs its installer once. Both repos self-update on their
  own timers. Neither repo's playbook or installer may change files the other owns.
- **Hub address.** nightstand reads the hub address from its local config, defaulting to
  `zimablade.local` (mDNS). The hostname is set in snaphouse.
- **Docs.** Each repo's README and AGENTS.md describe its side of these seams. A change to
  a seam updates this file in the same PR set.

## Feature type to surface

| Change | snaphouse | nightstand |
|---|---|---|
| New audio source or metadata field | stream config, metadata into stream properties, room picker if shown there | parser, fixture, screen if shown |
| New screen using house data | API already exposes it? If not, add it | screen module, rule, fixture, preview |
| New screen using outside data only | N/A | screen module, rule, fixture, preview |
| Art share change | share config, this doc | sync config |
| New device or hostname change | inventory, bootstrap, this doc | hub address default if the hub moved |

## Definition of done (cross-repo)

A cross-repo feature is done only when every slice of its surface has landed and
is documented. At design, list the surface per repo; at finish, verify each slice
(or mark N/A); a deferred slice must be stated and tracked, never silently missing.
The request-template checklist in each repo enforces this. The
`cross-repo-completeness` skill automates the check.
