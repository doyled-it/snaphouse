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
  (`house`: snapserver, AirPlay and Bluetooth inputs, room-picker page, art share),
  the two Pi audio clients, base OS configuration on all three machines, and the first
  bootstrap of every device, including `nightstand`. Exposes snapserver's JSON-RPC and
  HTTP API on the LAN, and the art share.
- **nightstand**: the bedside e-ink frame on the `nightstand` host. Consumes snapserver's API and
  the art share. After its first install (run by the snaphouse bootstrap) it owns
  everything display-specific on that host: SPI/I2C, the `inky` library, its service,
  its self-update timer, and its art sync.

## The seams (where features leak)

- **Now-playing metadata (snaphouse produces, nightstand consumes).** snapserver JSON-RPC
  `Server.GetStatus` on the hub: the active stream's playback status and its metadata
  properties (title, artist, album, art URL when present), plus each client's mute state
  and name, which give "rooms playing". snaphouse must get AirPlay metadata and art, and
  Bluetooth AVRCP metadata, into those stream properties. The cover travels only as an
  `artUrl` (a `/cover/` URL, see below), never as inline `artData`: embedding the image
  bytes bloated the status to megabytes and stalled the audio reader (issue #38).
  nightstand keeps a recorded `Server.GetStatus` response as a test fixture; when the shape
  changes on the snaphouse side, re-record it (the fixture no longer carries `artData`, and
  `artUrl` now points at `/cover/<hash>.jpg`).
- **Now-playing cover art (snaphouse produces, nightstand consumes).** The hub downscales
  the active AirPlay cover to a small JPEG (<=600 px, under 150 KB) and serves it at
  `http://house.local/cover/<hash>.jpg`; the `artUrl` in the now-playing metadata above is
  that URL. This is distinct from the art share below: `/cover/` is the current track's
  cover from snapserver's stream, `/art/` is the images people drop in for the frame.
- **Art share (snaphouse produces, nightstand consumes).** One folder on the hub,
  `/srv/snaphouse/art`, exposed two ways on the LAN. People drop images in over a writable
  Samba share (`[art]`, one account whose password lives only in the hub's local settings).
  the `nightstand` host reads it over HTTP: nginx serves the same folder read-only at `/art/` with
  `autoindex on; autoindex_format json;`, the "Art listing" contract below. nightstand
  syncs that listing to a local cache on a timer.
- **Art history (nightstand produces, snaphouse consumes).** nightstand writes an
  art-history file each time it pushes the ART screen to the panel, served by snaphouse's
  nginx on the `nightstand` host at `http://nightstand.local/nightstand/art-history.json`
  (the "Art history file" contract below). The hub's house-status timer fetches it with the
  same hardening as the status poll and republishes it same-origin at
  `http://house.local/art-history.json`, so the room-picker page shows what is on the frame
  without the browser resolving the frame's `.local` name. A change to the file's shape
  updates the contract below and both repos in the same PR set.
- **Device ownership on `nightstand`.** snaphouse bootstrap installs the OS base and
  audio, clones nightstand, and runs its installer once. Both repos self-update on their
  own timers. Neither repo's playbook or installer may change files the other owns.
- **Hub address.** nightstand reads the hub address from its local config, defaulting to
  `house.local` (mDNS). The hostname is set in snaphouse.
- **Docs.** Each repo's README and AGENTS.md describe its side of these seams. A change to
  a seam updates this file in the same PR set.

## Pinned contracts

**Machine status file.** Every machine serves `http://<host>.local/status.json`
(nginx, `Access-Control-Allow-Origin: *`). nightstand serves its own at
`http://nightstand.local/nightstand/status.json`. Same schema everywhere:

```json
{
  "host": "office",
  "repo": "snaphouse",
  "commit": "<sha running now>",
  "main_commit": "<sha of origin/main at last check>",
  "last_good": "<sha that last passed verify>",
  "verify": "pass",
  "checked_at": "2026-11-06T21:15:00-08:00"
}
```

`verify` is `"pass"` or `"fail"`. `last_good` may be `""` before a machine's first
successful verify; consumers treat an empty value as "no last-good commit yet". On a
brand-new machine the first apply briefly publishes a provisional document with
`verify: "fail"` and `last_good: ""` before its first real verify result replaces it,
so a freshly bootstrapping host shows as troubled for a few seconds; this is expected
and clears on the first passing verify. A machine
is healthy when `verify == "pass"` and `commit == main_commit`. snaphouse writes `/var/lib/snaphouse/public/status.json`;
nightstand writes `/var/lib/nightstand/public/status.json`, and snaphouse's nginx on
the `nightstand` host serves that directory at `/nightstand/`.

**Art listing.** The hub serves the art folder read-only at `http://house.local/art/`
with nginx `autoindex on; autoindex_format json;`. Each entry is
`{"name": str, "type": "file" | "directory", "mtime": str, "size": int}`. nightstand
mirrors files one level deep and ignores directories. Sidecar captions are files named
`<image file>.txt` (line 1 title, line 2 artist) in the same folder.

**Art history file.** nightstand produces, snaphouse consumes. nightstand writes
`/var/lib/nightstand/public/art-history.json` atomically (0644) each time the ART screen
is actually pushed to the panel; snaphouse's nginx on the `nightstand` host serves it at
`http://nightstand.local/nightstand/art-history.json`. Shape:

```json
{
  "current": {
    "file": "aic-12345.jpg",
    "title": "A Still Life",
    "artist": "A Painter",
    "source_url": "https://www.artic.edu/artworks/12345",
    "shown_at": "2026-11-06T21:14:00-08:00"
  },
  "history": [ { "...": "Piece" } ]
}
```

`history` is newest first, at most 50 entries, and `current` is also `history[0]`
(`current` is `null` and `history` is `[]` when nothing has been shown). A `Piece` is
`{"file", "title", "artist": str|null, "source_url": str|null, "shown_at": ISO-8601 with
offset}`. `file` is a bare image file name in the art share (no path separators).
`source_url` is an https URL to the museum object page, or `null`; consumers drop a
non-https value. Captions come from the sidecar `<image file>.txt` files of the "Art
listing" contract (line 1 title, line 2 artist, optional line 3 an https source URL that
is ignored unless it parses as https). The hub republishes a validated copy same-origin at
`http://house.local/art-history.json`, keeping only these fields, capping history at 50,
and keeping the last good copy when a fetch fails.

**snapserver API.** `http://house.local/jsonrpc` is proxied by nginx on the hub to
snapserver's JSON-RPC (port 1780). Port 1780 itself stays reachable on the LAN for
Snapweb. nightstand calls port 1780 directly.

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
