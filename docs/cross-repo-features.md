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
  cover from snapserver's stream, `/art/` is the images people drop in for the frame. For
  a track that arrives without a cover (Bluetooth, some apps), the hub's now-playing
  service resolves one from public catalogues and serves it under `/cover/lookup/`, with
  history thumbnails kept under `/cover/history/`; see the hub outbound list in the design.
- **Now-playing file (snaphouse produces, nightstand and the room-picker page consume).**
  The hub's `snaphouse-nowplaying` service is the single source of now-playing data: it
  follows the House stream over snapserver's control port and writes `now-playing.json` and
  `play-history.json` into the public web root, served same-origin by nginx. Consumers read
  those files rather than snapserver directly, because the files carry a position anchor
  (snapserver's properties have no timestamp) and a resolved cover for coverless tracks.
  The "Now-playing file" and "Play-history file" contracts below pin their shapes.
- **Art share (snaphouse produces, nightstand consumes).** One folder on the hub,
  `/srv/snaphouse/art`, exposed two ways on the LAN. People drop images in over a writable
  Samba share (`[art]`, one account whose password lives only in the hub's local settings).
  the `nightstand` host reads it over HTTP: nginx serves the same folder read-only at `/art/` with
  `autoindex on; autoindex_format json;`, the "Art listing" contract below. nightstand
  syncs that listing to a local cache on a timer.
- **Art rotation control (snaphouse produces, the room-picker page and nightstand consume).**
  The hub runs the `snaphouse-artadmin` service: a loopback HTTP API nginx proxies at
  `/api/art/` that the room-picker page calls to manage the library (upload, delete to a
  restorable trash, move, folders) and the rotation (interval, which folders rotate, and
  next/previous/show commands). Every write carries the `X-Snaphouse: 1` header a cross-site
  page cannot send, and uploads are capped at 25 MB per file and stripped of all metadata. The
  service publishes two same-origin documents the page and the frame read, `art-index.json`
  (the whole library: folders, pieces, trash, with thumbnails) and `art-control.json` (the
  rotation settings and the last command), rewriting both after every change and on a 60 s
  rescan so edits made over SMB show up too. The "Art index file" and "Art control file"
  contracts below pin their shapes. For the frame's sync this supersedes the flat `/art/`
  listing (nightstand Task 4); the listing stays until then.
- **Art history (nightstand produces, snaphouse consumes).** nightstand writes an
  art-history file each time it pushes the ART screen to the panel, served by snaphouse's
  nginx on the `nightstand` host at `http://nightstand.local/nightstand/art-history.json`
  (the "Art history file" contract below). The hub's house-status timer fetches it with the
  same hardening as the status poll and republishes it same-origin at
  `http://house.local/art-history.json`, so the room-picker page shows what is on the frame
  without the browser resolving the frame's `.local` name. A change to the file's shape
  updates the contract below and both repos in the same PR set.
- **Frame mirror (nightstand produces, snaphouse consumes).** After each push to the panel
  nightstand writes the image it sent, passed through its panel simulation, as `frame.png`
  (800x480 PNG) plus `frame.json` (`{"updated_at", "screen"}`), served by snaphouse's nginx
  on the `nightstand` host at `http://nightstand.local/nightstand/frame.png` and
  `/frame.json` (the "Frame mirror file" contract below). The hub's house-status timer
  republishes both same-origin at `http://house.local/frame.png` and `/frame.json` (into a
  timer-owned subdir of the public root, never chowning the shared root), with the same
  hardening as the art-history poll: a size cap, a PNG-signature or JSON-shape check, an
  atomic write, and the last good copy kept when a fetch fails. A missing file hides the
  mirror on the page.
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
offset, "id": str|null}`. `file` is a bare image file name in the art share (no path
separators). `id` is the piece id relative to the art library root (at most one folder
deep, the same id as in the "Art index file"), added so the room-picker page can delete
the piece by id; it is `null` on older entries written before this field existed, and a
consumer falls back to `file`. `source_url` is an https URL to the museum object page, or
`null`; consumers drop a non-https value. Captions come from the sidecar `<image file>.txt`
files of the "Art listing" contract (line 1 title, line 2 artist, optional line 3 an https
source URL that is ignored unless it parses as https). The hub republishes a validated copy
same-origin at `http://house.local/art-history.json`, keeping only these fields (an `id`
that is unsafe, a traversal, more than one folder deep, prefixed with the reserved
`Collection` folder, or whose last segment is not the entry's `file` becomes `null`),
capping history at 50, and keeping the last good copy when a fetch fails.

**Frame mirror file.** nightstand produces, snaphouse consumes. After each push to the
panel nightstand writes the image it sent (passed through its panel simulation) atomically
(0644) as `/var/lib/nightstand/public/frame.png`, an 800x480 PNG, plus
`/var/lib/nightstand/public/frame.json`; snaphouse's nginx on the `nightstand` host serves
them at `http://nightstand.local/nightstand/frame.png` and `/frame.json`. `frame.json`:

```json
{
  "updated_at": "2026-11-09T07:00:00-08:00",
  "screen": "art"
}
```

`updated_at` is an ISO-8601 stamp with offset of when the frame last drew; `screen` names
what is on the panel (`"art"`, `"now_playing"`, or another screen name), a non-empty
string. The hub's house-status timer republishes both same-origin at
`http://house.local/frame.png` and `http://house.local/frame.json`: the image fetch is
capped at 2 MB and the body must start with the PNG signature, `frame.json` is validated to
exactly these two fields, both are written atomically into a timer-owned subdir of the
public root, and the last good copy is kept when a fetch fails or a body is malformed. The
room-picker page reads the same-origin copies and hides the mirror when either is missing.

**Now-playing file.** snaphouse produces, nightstand and the room-picker page consume. The
hub's `snaphouse-nowplaying` service writes `/var/lib/snaphouse/public/nowplaying/now-playing.json`
atomically (0644); nginx serves it same-origin at `http://house.local/now-playing.json`. It
is whatever is on the House stream right now, from either input. Shape:

```json
{
  "updated_at": "2026-11-08T21:14:03-08:00",
  "track": {
    "title": "Episode 412: ...",
    "artist": "The Long Version",
    "album": "The Long Version",
    "kind": "long_form",
    "source": "bluetooth",
    "status": "playing",
    "duration": 3120.0,
    "position": 1390.0,
    "position_at": "2026-11-08T21:13:58-08:00",
    "started_at": "2026-11-08T20:50:48-08:00",
    "art_url": "/cover/3f2a....jpg"
  },
  "rooms": ["Bedroom", "Office"]
}
```

`track` is `null` when nothing with a title is on House, and also once a track has been
paused (or the House stream idle) for 10 minutes; before that it stays with
`status: "paused"`. `status` is `"playing"` or `"paused"`; `source` is `"airplay"` or
`"bluetooth"`; `kind` is `"music"` or `"long_form"`. `artist`, `album`, `duration`,
`position`, `position_at`, and `art_url` may each be `null`. `art_url` is always a
same-origin `/cover/...` path on the hub (or `null`), never an absolute or off-origin URL.
`position` is the position at the moment the properties were emitted and `position_at` is
when the service received it, so a consumer ticks `position + (now - position_at)` while
playing and freezes it while paused. `rooms` is the connected, unmuted clients on the House
stream by display name; an empty list is allowed (the frame treats it as nothing playing,
the web page still shows the track). A missing, empty, or malformed file means nothing
playing: a consumer hides the card and never shows stale data.

**Play-history file.** snaphouse produces, the room-picker page consumes. The same service
writes `/var/lib/snaphouse/public/nowplaying/play-history.json` atomically (0644); nginx serves
it at `http://house.local/play-history.json`. Shape:

```json
{
  "plays": [
    {
      "title": "...", "artist": "...", "album": "...",
      "kind": "music", "source": "airplay",
      "duration": 322.0, "started_at": "2026-11-08T21:01:10-08:00",
      "art_url": "/cover/history/....jpg"
    }
  ]
}
```

Newest first, at most 50. A play is added once a track has been current for 30 seconds; a
track's identity is `(title, artist, album)`, so pausing and resuming the same track is one
play (not two), as is a mid-track service restart within the track's duration plus 10
minutes. `art_url` is a same-origin `/cover/history/...` path (a durable copy the service
keeps so it survives cover pruning) or `null`, and is filled in later if the cover lookup
finishes after the play was recorded. `artist`, `album`, `duration`, and `art_url` may be
`null`.

**Art index file.** snaphouse produces, the room-picker page and nightstand consume. The
`snaphouse-artadmin` service writes `/var/lib/snaphouse/public/art/art-index.json` atomically
(0644); nginx serves it same-origin at `http://house.local/art-index.json`. It is the whole
library, published immediately (a metadata-only pass, so a large library is listed at once)
and rebuilt after every change and on a 60 s rescan. Thumbnails are generated in the
background and the index is republished as they land. Shape:

```json
{
  "generated_at": "2026-11-09T07:00:00-08:00",
  "folders": [
    {"name": "Collection", "count": 172, "enabled": true, "builtin": true},
    {"name": "Photos", "count": 12, "enabled": false, "builtin": false}
  ],
  "pieces": [
    {
      "id": "Photos/beach.jpg", "folder": "Photos", "file": "beach.jpg",
      "title": "Beach", "artist": null, "source_url": null,
      "width": 4032, "height": 3024, "landscape": true,
      "size": 2481234, "mtime": 1794125000,
      "thumb": "/art-thumbs/3f2a....jpg", "url": "/art/Photos/beach.jpg"
    }
  ],
  "trash": [
    {"id": ".trash/2026-11-09T06-58-00/old.jpg", "original": "old.jpg",
     "deleted_at": "2026-11-09T06:58:00-08:00", "thumb": "/art-thumbs/....jpg"}
  ]
}
```

`folders[].builtin` is true only for the reserved top-level `Collection`; a folder named
`Collection` cannot be created in any case. A piece `id` is its path relative to the library
root, at most one folder deep; `url` and `thumb` are percent-encoded per path segment, while
`id` is raw. `landscape` is true when the aspect is at least `settings.min_aspect` from the
"Art control file" (default 1.1, the frame's landscape rule); pieces below the cutoff stay
listed but do not rotate. `trash` entries are restorable for 30 days.
`thumb` (on a piece or a trash entry) is `null` until the background pass has generated that
thumbnail, so the index can be published before the thumbnails exist; a consumer must tolerate
a null `thumb` (show a placeholder and re-read later). Thumbnails are served by the same nginx
at `/art-thumbs/...`, 320 px on the long side.

**Art control file.** snaphouse produces, nightstand consumes (the room-picker page reads it
too, to show the current settings). The same service writes
`/var/lib/snaphouse/public/art/art-control.json` atomically (0644); nginx serves it at
`http://house.local/art-control.json`. The private copy it is rebuilt from persists at
`/var/lib/snaphouse/artadmin/art-control.json` (not served). Shape:

```json
{
  "settings": {"rotate_minutes": 60, "folders_enabled": ["Collection"], "min_aspect": 1.1},
  "command": {"seq": 7, "action": "next", "piece": null,
              "issued_at": "2026-11-09T06:59:10-08:00"}
}
```

`settings.rotate_minutes` is one of 15, 30, 60, 120, 180, 360, 720, or 1440 (default 60).
`settings.folders_enabled` lists the folders in rotation by name; renaming a folder carries its
enabled state. `settings.min_aspect` is the landscape cutoff (a positive number, default 1.1):
a piece rotates only when its width/height reaches it, and the hub's `landscape` flag in the
"Art index file" uses the same value, so the hub is the single source of truth and the frame
follows it (its local config is only the fallback). `command` is `null` until the first
command; `action` is `next`, `previous`, or
`show` (`piece` is the id for `show`, otherwise `null`). `seq` only increases, so a frame acts on
a `seq` greater than the last one it applied; a frame that starts up and sees an existing command
records its `seq` without replaying it.

**snapserver API.** `http://house.local/jsonrpc` is proxied by nginx on the hub to
snapserver's JSON-RPC (port 1780). Port 1780 itself stays reachable on the LAN for
Snapweb. The nightstand frame no longer calls snapserver directly for playback data: since the
now-playing work it reads the hub's `/now-playing.json` (and covers via each track's `art_url`),
the "Now-playing file" contract above.

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
