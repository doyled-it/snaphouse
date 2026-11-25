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
  contracts below pin their shapes. A rotation command carries an optional `frame`: with a frame
  id it applies only to that frame, with none (the compatible default) to every frame. For the
  frame's sync this supersedes the flat `/art/` listing (nightstand Task 4); the listing stays
  until then.
- **Frame control (snaphouse produces, the room-picker page and nightstand consume).** The
  same `snaphouse-artadmin` service owns the shared frame settings and each frame's own quiet
  hours and pin: which kinds of audio take the now-playing screen, the quiet-hours and
  morning-weather windows (shared), and, per frame, a quiet-hours override and a pin that holds
  that frame on the current art piece, the weather, or now playing. The page sets them over three
  write endpoints behind the same `X-Snaphouse: 1` guard (`/api/frame/settings`, with an optional
  `frame` for the per-frame keys; `/api/frame/pin` and `/api/frame/unpin`, each with a required
  `frame`), and the service publishes one same-origin document, `frame-control.json`, with a
  `frames` map keyed by the inventory frame ids, rewritten after every change and at startup. Each
  frame reads it each cycle and follows its own entry; a missing or malformed document falls back
  to local config and no pin (the page shows the settings as unknown rather than wrong). The
  "Frame control file" contract below pins its shape.
- **Art history (nightstand produces, snaphouse consumes).** Each frame host (one whose
  snaphouse roles include `nightstand_bootstrap`: `nightstand`, `office`) writes an
  art-history file each time it pushes the ART screen to the panel, served by snaphouse's
  nginx at `http://<frame>.local/nightstand/art-history.json` (the "Art history file"
  contract below). The hub's house-status timer fetches every frame's file with the same
  hardening as the status poll and republishes each same-origin per frame at
  `http://house.local/frames/<id>/art-history.json`, so the room-picker page shows what is
  on each frame without the browser resolving the frame's `.local` name. For one release the
  timer also keeps serving the nightstand's copy at the old single path
  `http://house.local/art-history.json`. A change to the file's shape updates the contract
  below and both repos in the same PR set.
- **Frame mirror (nightstand produces, snaphouse consumes).** After each push to the panel a
  frame writes the image it sent, passed through its panel simulation, as `frame.png`
  (800x480 PNG) plus `frame.json` (`{"updated_at", "screen"}`), served by snaphouse's nginx
  at `http://<frame>.local/nightstand/frame.png` and `/frame.json` (the "Frame mirror file"
  contract below). The hub's house-status timer republishes each frame's pair same-origin
  per frame at `http://house.local/frames/<id>/frame.png` and `/frame.json` (into a
  timer-owned subdir of the public root, never chowning the shared root), with the same
  hardening as the art-history poll: a size cap, a PNG-signature or JSON-shape check, an
  atomic write, and the last good copy kept when a fetch fails. For one release the timer
  also keeps serving the nightstand's copy at the old single paths
  `http://house.local/frame.png` and `/frame.json`. A missing file hides that frame's mirror
  on the page.
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

**Art history file.** nightstand produces, snaphouse consumes. Each frame host writes
`/var/lib/nightstand/public/art-history.json` atomically (0644) each time the ART screen
is actually pushed to its panel; snaphouse's nginx on that host serves it at
`http://<frame>.local/nightstand/art-history.json`. Shape:

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
of each frame's file same-origin per frame at `http://house.local/frames/<id>/art-history.json`
(`<id>` is the frame's inventory key, matching `^[a-z][a-z0-9-]{0,31}$` and validated before
any path is built), keeping only these fields (an `id` that is unsafe, a traversal, more than
one folder deep, prefixed with the reserved `Collection` folder, or whose last segment is not
the entry's `file` becomes `null`), capping history at 50, and keeping the last good copy when
a fetch fails. For one release it also republishes the nightstand's copy at the old single
path `http://house.local/art-history.json`.

**Frame mirror file.** nightstand produces, snaphouse consumes. After each push to the
panel a frame writes the image it sent (passed through its panel simulation) atomically
(0644) as `/var/lib/nightstand/public/frame.png`, an 800x480 PNG, plus
`/var/lib/nightstand/public/frame.json`; snaphouse's nginx on that host serves
them at `http://<frame>.local/nightstand/frame.png` and `/frame.json`. `frame.json`:

```json
{
  "updated_at": "2026-11-09T07:00:00-08:00",
  "screen": "art"
}
```

`updated_at` is an ISO-8601 stamp with offset of when the frame last drew; `screen` names
what is on the panel (`"art"`, `"now_playing"`, or another screen name), a non-empty
string. The hub's house-status timer republishes each frame's pair same-origin per frame at
`http://house.local/frames/<id>/frame.png` and `/frames/<id>/frame.json` (`<id>` validated
as above): the image fetch is capped at 2 MB and the body must start with the PNG signature,
`frame.json` is validated to exactly these two fields, both are written atomically into a
timer-owned subdir of the public root, and the last good copy is kept when a fetch fails or a
body is malformed. For one release it also republishes the nightstand's pair at the old
single paths `http://house.local/frame.png` and `/frame.json`. The room-picker page reads the
same-origin copies and hides a frame's mirror when either is missing.

**Frame change feedback (additive to the Frame mirror file).** `frame.json` may also carry two
optional fields, so the page can show a change in flight:

```json
{
  "updated_at": "2026-11-23T07:00:00-08:00",
  "screen": "art",
  "applied_seq": 12,
  "pending": {"seq": 13, "id": "art/example.jpg", "since": "2026-11-23T07:01:00-08:00",
              "state": "changing", "reason": null}
}
```

`pending` is written by the frame on the cycle it applies a next, previous or show command
addressed to it, before the e-ink push. `seq` is the command's seq, `id` the piece id the
rotation moved to, `since` an ISO stamp. `state` is `changing` (the frame will push this piece
now), `waiting` (the piece is next but the frame is not drawing art now; `reason` is one of
`music`, `game`, `pin`, `settling`, or the generic `screen` for any other scheduled
screen such as the morning weather) or `failed` (the push raised; `reason` null). The frame
clears `pending` on its next mirror write and replaces it when a newer command applies.
`applied_seq` is the seq of the latest command whose piece is on the panel, written on every
mirror write after a command push and absent until the first. Confirmation is by seq only
(`applied_seq >= seq`), never by comparing clocks.

The hub's validator keeps `pending` only when `seq` is a non-negative int, `id` passes the
piece-id check, `since` is a string of at most 40 characters, and `state` and `reason` are from
the lists above; otherwise it drops `pending` alone. `applied_seq` must be a non-negative int or
it is dropped. `POST /api/art/command` already replies `{"command": {"seq", "frame", ...}}`; the
page matches `pending.seq` and `applied_seq` against that seq. While a change is in flight, the
path unit `snaphouse-frame-follow.path` (watching `art-control.json`) starts the one-shot
`snaphouse-frame-follow.service`, which republishes the targeted frame's mirror every 2 s (every
frame when the command's `frame` is null) until `applied_seq` reaches the seq or 90 s pass from
the latest command, fetching `frame.png` only when `updated_at` changes. A frame without these
fields (not yet updated) simply never confirms, and the page ends in its 90 s "did not
confirm" message.

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
    "genre": ["Podcast"],
    "source": "bluetooth",
    "status": "playing",
    "duration": 3120.0,
    "position": 1390.0,
    "position_at": "2026-11-08T21:13:58-08:00",
    "started_at": "2026-11-08T20:50:48-08:00",
    "art_url": "/cover/3f2a....jpg",
    "podcast_id": 1200361736,
    "controllable": true
  },
  "resume": null,
  "rooms": ["Bedroom", "Office"]
}
```

`track` is `null` when nothing with a title is on House, and also once a track has been
paused (or the House stream idle) for 10 minutes; before that it stays with
`status: "paused"`. `controllable` (additive, boolean) is true for a Bluetooth track while
the phone's media player is present (the stream's `canControl`), false otherwise; the nightstand
frame ignores it. `resume` (additive, top level) is `null` except in one case: a paused
Bluetooth track whose phone player is still present once the 10 minute hold has passed. Then
`track` is `null` exactly as before (so the frame sees the same document it always did) and
`resume` carries the same shape as `track`, with `status: "paused"`. It clears when the phone's
player goes away, and a newer or resumed track returns to `track`. The nightstand frame ignores
`resume`; the room-picker page shows it as a paused track (the panel offers Play). A hub restart
forgets it. `status` is `"playing"` or `"paused"`; `source` is `"airplay"` or
`"bluetooth"`; `kind` is `"music"` or `"long_form"`. `genre` is a list of strings from
the stream metadata (the hub's additive field), or `null` when unknown; it is additive, and the
nightstand frame uses it with `kind` to derive the now-playing category for the kinds filter (the
room-picker page uses it to route the "Open in" sheet).
`artist`, `album`, `duration`, `position`, `position_at`, and `art_url` may each be `null`. `art_url` is always a
same-origin `/cover/...` path on the hub (or `null`), never an absolute or off-origin URL.
`podcast_id` is the matched show's iTunes `collectionId` (a positive integer), filled in
later like `art_url` once the podcast art lookup lands, or `null` for anything that is not a
matched podcast; it is additive and the nightstand frame ignores it (the room-picker page
uses it to open the show in Pocket Casts, `https://pca.st/itunes/<podcast_id>`, and on the
Apple Podcasts show page, `https://podcasts.apple.com/podcast/id<podcast_id>`).
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
      "kind": "music", "genre": ["Rock"], "source": "airplay",
      "duration": 322.0, "started_at": "2026-11-08T21:01:10-08:00",
      "last_played_at": "2026-11-08T21:01:10-08:00",
      "art_url": "/cover/history/....jpg", "podcast_id": null
    }
  ]
}
```

Newest first, at most 50. A play is added once a track has been current for 30 seconds; a
track's identity is `(title, artist, album)`, so pausing and resuming the same track is one
play (not two), as is a mid-track service restart within the track's duration plus 10
minutes. `art_url` is a same-origin `/cover/history/...` path (a durable copy the service
keeps so it survives cover pruning) or `null`, and is filled in later if the cover lookup
finishes after the play was recorded. `genre` is a list of strings from the stream metadata,
or `null` when unknown; it is additive, so entries written before this field have no `genre`
key and stay valid (a consumer treats a missing `genre` as unknown). `podcast_id` is the
matched show's iTunes `collectionId` (a positive integer), filled in later like `art_url`
once the podcast art lookup lands, or `null` for anything that is not a matched podcast; it
is additive too (entries written before this field have no `podcast_id` key and stay valid)
and routes the room-picker page's "Open in" sheet to Pocket Casts and the Apple Podcasts
show page. `artist`, `album`, `duration`, `genre`, `art_url`, and `podcast_id` may be `null`.

`last_played_at` is additive (ISO-8601, when the newest session of the row began; equal to
`started_at` for a first play; older entries lack it and a consumer falls back to
`started_at`). Long-form items merge by identity: when a play is counted and an existing row
has the same `(title, artist, album)` and either the row or the new play has kind
`long_form`, the old row is removed and one merged row is inserted at the top. The merged
row keeps the first `started_at`, sets `last_played_at` to the newest session's start,
takes kind `long_form` (a short chapter is no longer filed as `music`), and takes the
newest non-null `art_url`, `genre`, `podcast_id` and `duration`. Music repeats stay separate
plays. On load the service collapses existing duplicates the same way and orders rows by
`last_played_at` (or `started_at`). The page treats a row as a book when `kind` is
`long_form`, `genre` names no podcast, and `podcast_id` is `null`.

**Art index file.** snaphouse produces, the room-picker page and nightstand consume. The
`snaphouse-artadmin` service writes `/var/lib/snaphouse/public/art/art-index.json` atomically
(0644); nginx serves it same-origin at `http://house.local/art-index.json`. It is the whole
library, published immediately (a metadata-only pass, so a large library is listed at once)
and rebuilt after every change and on a 60 s rescan. Thumbnails are generated in the
background and the index is republished as they land. Shape:

```json
{
  "generated_at": "2026-11-09T07:00:00-08:00",
  "ente_sync": {"state": "ok", "last_success": "2026-11-09T06:30:00-08:00", "error": null},
  "folders": [
    {"name": "Collection", "count": 172, "enabled": true, "builtin": true},
    {"name": "Photos", "count": 12, "enabled": false, "builtin": false},
    {"name": "ente-a1B2c3", "count": 40, "enabled": true, "builtin": false,
     "source": "ente", "title": "Album One"}
  ],
  "pieces": [
    {
      "id": "Photos/beach.jpg", "folder": "Photos", "file": "beach.jpg",
      "title": "Beach", "artist": null, "source_url": null,
      "width": 4032, "height": 3024, "landscape": true,
      "crop": {"x": 0.1, "y": 0.05, "w": 0.6, "h": 0.6048},
      "auto_crop": {"x": 0.0, "y": 0.0, "w": 0.9921, "h": 1.0},
      "size": 2481234, "mtime": 1794125000,
      "thumb": "/art-thumbs/3f2a....jpg", "url": "/art/Photos/beach.jpg"
    },
    {
      "id": "ente-a1B2c3/9f8e7d.jpg", "folder": "ente-a1B2c3", "file": "9f8e7d.jpg",
      "title": "Album One", "artist": null, "source_url": null,
      "width": 1600, "height": 1067, "landscape": true,
      "crop": null, "auto_crop": {"x": 0.0, "y": 0.0, "w": 1.0, "h": 0.9987},
      "size": 412345, "mtime": 1794125000,
      "thumb": "/art-thumbs/7c1d....jpg", "url": "/art/ente-a1B2c3/9f8e7d.jpg",
      "source": "ente"
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
listed but do not rotate, except that a piece with a saved `crop` reports `landscape: true`
(informational: the frame decides eligibility from the crop it syncs, see "Frame crop"). `trash`
entries are restorable for 30 days.

`crop` and `auto_crop` are additive (an older consumer ignores them; a consumer must tolerate their
absence). Each is `{"x", "y", "w", "h"}` or `null`: the left, top, width and height of a 5:3 box
(the 800x480 panel's shape) as fractions of the UPRIGHT picture, that is after EXIF orientation is
applied (a file that still carries an orientation tag over SMB is measured upright; `width` and
`height` above are as stored). The box satisfies `w * W / (h * H) = 800 / 480` within rounding, with
every value in [0, 1], `x + w <= 1` and `y + h <= 1`. `crop` is the owner's saved box (`null` when
none). `auto_crop` is the automatic box: the hub's port of the window nightstand's `detail_crop`
picks today (scale to cover 800x480, slide along the overflowing axis toward the busiest edge
energy, centered when the picture is within 0.1 of 5:3). It is computed once per picture on the
thumbnail pass and cached beside the thumbnail, so it is `null` until that pass has reached the
piece (like `thumb`) and `null` for a picture that cannot be measured; Ente and museum pieces both
carry one. The box to show or cut is `crop` when set, else `auto_crop`. A crop is kept through a
move, a trash and restore, and an Ente re-sync; it is dropped when its picture is purged or no
longer exists.

Ente albums are additive and optional. A folder whose `name` starts with `ente-` (the reserved
prefix, matched case-insensitively) is an Ente Photos album mirrored read-only by the hub: its
`name` is `ente-` plus the album id, `source` is `"ente"` and `title` is the album's display
name (art folders carry neither key). Its pieces carry `"source": "ente"`, a `file` of
`<fileId>.jpg`, the album title as `title`, and a `url` under `/art/` that nginx serves from the
downscaled JPEG renditions (up to 1600 px on the long side), not the originals. Ente folders sort
after art folders, are never `builtin`, and a user-made art folder with the `ente-` prefix is
ignored by the scan. A metadata-only pass lists an Ente piece only once its rendition exists.
Top-level `ente_sync` is `{"state", "last_success", "error"}` with `state` one of `ok`,
`failing`, `not_set_up` or `unknown`; it is present only on a hub that has the Ente source
configured, and a consumer must tolerate its absence. The frame exempts `ente-` folders from
`min_aspect`: an Ente piece rotates and can be shown whatever its shape, with `landscape` still
reported as measured.
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
  "settings": {"rotate_minutes": 60, "folders_enabled": ["Collection"], "min_aspect": 1.1,
               "ente_seen": []},
  "command": {"seq": 7, "action": "next", "piece": null, "frame": null,
              "issued_at": "2026-11-09T06:59:10-08:00"}
}
```

`settings.rotate_minutes` is one of 15, 30, 60, 120, 180, 360, 720, or 1440 (default 60).
`settings.folders_enabled` lists the folders in rotation by name; renaming a folder carries its
enabled state. `settings.min_aspect` is the landscape cutoff (a positive number, default 1.1):
a piece rotates only when its width/height reaches it, and the hub's `landscape` flag in the
"Art index file" uses the same value, so the hub is the single source of truth and the frame
follows it (its local config is only the fallback). `settings.ente_seen`
lists the `ente-` folder ids the hub has already seen: an `ente-` folder in the "Art index file"
that is not in `ente_seen` is appended to both `ente_seen` and `folders_enabled` once (a new
album starts on), and an id in `ente_seen` that the owner switched off stays off across rescans
and renames in Ente. The frame should treat `ente-` folders as exempt from `min_aspect`.
`command` is `null` until the first
command; `action` is `next`, `previous`, or
`show` (`piece` is the id for `show`, otherwise `null`). `frame` scopes the command: a frame id
means only that frame acts on it, and `null` means every frame (the compatible default). A frame
acts on a command only when `frame` is absent or `null`, or equals its own id. `frame` is additive:
a command written before per-frame commands has no `frame` key and the hub republishes it as
`null`. The write `POST /api/art/command` takes `{"action", "piece"?, "frame"?}`, where `frame` is
a known frame id (an unknown id is a 404, a malformed id a 400) or absent for every frame. `seq`
only increases, so a frame acts on a `seq` greater than the last one it applied; a frame that
starts up and sees an existing command records its `seq` without replaying it.

**Frame crop (`POST /api/art/crop`).** The page writes, the hub stores and publishes, nightstand
reads. Body `{"id": "<piece id>", "crop": {"x", "y", "w", "h"} | null}`; a box saves, `null`
removes ("Automatic"). Same guard as the other art writes (`X-Snaphouse: 1`, no origin rule; nginx
already proxies `/api/art/`). Reply `{"id", "crop"}` with the stored box (`h` snapped to the exact
5:3 aspect, values rounded to six decimals) or `null`. Errors are `{"error": "<short sentence>"}`:
400 for a missing `id` or `crop`, a value that is not a finite number (a bool or a string is not
one), a value outside [0, 1], a box that leaves the picture, an aspect more than 1 percent off 5:3,
or a box smaller than 400 px of the upright picture's width (a picture too small for that only
accepts the largest box that fits); 404 for an unknown id (an Ente piece whose rendition does not
exist yet is unknown). Ente pieces can be cropped: the crop is a note in `crops.json` in the art
state directory (`/var/lib/snaphouse/artadmin/`, keyed by piece id), never a write to the Ente
mirror or cache, so the read-only 409 does not apply. After a save the service republishes the
index and wakes the thumbnail pass. `POST /api/art/command` with `show` accepts a piece that has a
crop whatever its aspect. A rename done over SMB is outside the API: the new name is a new piece
and starts automatic, and the old crop is pruned on the next scan.

The frame side (nightstand, a later PR): cut the effective box (`crop` else `auto_crop`) from the
upright picture (apply `exif_transpose` first) and resize it to 800x480 with Lanczos before
dithering; fall back to its own `detail_crop` only when both are absent or invalid. Redraw the shown
piece on the next cycle, the way a command redraws, when its effective box changes. A piece with a
`crop` is eligible whatever its aspect or `min_aspect`; a folder whose rotation switch is off still
keeps its pieces out.

**Frame control file.** snaphouse produces, nightstand and the room-picker page consume. The
`snaphouse-artadmin` service writes `/var/lib/snaphouse/public/art/frame-control.json` atomically
(0644); nginx serves it same-origin at `http://house.local/frame-control.json`. The private copy
it is rebuilt from persists at `/var/lib/snaphouse/artadmin/frame-control.json` (not served). Shape:

```json
{
  "settings": {
    "now_playing_kinds": ["music"],
    "quiet_hours": {"start": "22:00", "end": "06:00"},
    "morning_weather": {"enabled": true,
                        "weekday": ["06:00", "08:30"],
                        "weekend": ["07:00", "09:00"]},
    "weather_location": {"name": "San Diego, California, United States",
                         "latitude": 32.72, "longitude": -117.16,
                         "timezone": "America/Los_Angeles"}
  },
  "frames": {
    "nightstand": {"quiet_hours": null, "pin": null},
    "office": {"quiet_hours": {"enabled": false}, "pin": null}
  }
}
```

`settings` is shared across every frame. `frames` lists exactly the inventory frame hosts (the
hosts whose roles include `nightstand_bootstrap`), so the room-picker page's frame switcher reads
the frame ids from it. Each frame carries its own `quiet_hours` override and its own `pin`, so
pinning or silencing one frame never moves another. A frame's `quiet_hours` is `null` (fall back
to the shared `settings.quiet_hours`), `{"enabled": false}` (that frame keeps no quiet hours), or
a window `{"start", "end"}` (an override). A frame id matches `^[a-z][a-z0-9-]{0,31}$`.

`now_playing_kinds` is a subset of `music`, `podcast`, `audiobook` (default `["music"]`): the
now-playing screen takes the frame only when the derived category is in it. `quiet_hours.start`
and `.end` are `HH:MM` and may wrap past midnight (22:00 to 06:00). `morning_weather` carries an
`enabled` flag and a `weekday` and `weekend` window, each `[start, end]` in `HH:MM` with start
before end. `weather_location` is `null` (no city set) or
`{"name", "latitude", "longitude", "timezone"}`: a display name, city-centre coordinates rounded
to two decimals (city level), and an IANA timezone name, resolved on the hub from a typed city
through Open-Meteo's keyless geocoding API (`geocoding-api.open-meteo.com`). The write takes
`{"weather_location": {"query": "San Diego, CA"}}` (the hub geocodes it) or
`{"weather_location": null}` (clears it); only the typed place name leaves the LAN. A bad city is a
400 and an unreachable geocoder a 502, each a JSON error. When it is present and valid the frame
uses it for the forecast; otherwise the frame falls back to its own local config. A frame's `pin`
(in `frames[<id>]`) is `null` or
`{"what": "art" | "weather" | "now_playing", "piece": id | null, "until": iso | null, "seq": n,
"set_at": iso}`. `piece` is the art id captured when an `art` pin was set (the piece then on that
frame, read from that frame's own republished `art-history.json` under `/frames/<id>/`), or `null`;
`until` is an ISO-8601 stamp with
offset, or `null` for "until unpinned". The "until tomorrow morning" choice resolves on the hub to
the next morning-window start for that day of the week. `seq` is per frame and increases on every
pin and unpin of that frame, so a frame can tell a change to its own pin and redraw once (a pin
change forces one redraw even in quiet hours).
The three write endpoints each carry the `X-Snaphouse: 1` guard, validate the frame id (an unknown
id is a 404, a malformed one a 400, so a write never reaches an arbitrary path), and return JSON
errors like the art API: `/api/frame/pin` with `{"frame", "what", "duration"}` (`duration` is `1h`,
`4h`, `morning`, or `forever`) and `/api/frame/unpin` with `{"frame"}` both take a required `frame`
and act on that frame alone; `/api/frame/settings` takes an optional `frame` (with it, a subset of
the per-frame keys, currently `quiet_hours`; without it, a subset of the shared `settings`). A
missing, stale, or malformed file means every frame keeps working on local config and no pin, and
the page shows the settings as unknown rather than wrong. A pinned art piece deleted from the
library while pinned makes that frame fall back to the schedule, no crash.

**snapserver API.** `http://house.local/jsonrpc` is proxied by nginx on the hub to
snapserver's JSON-RPC (port 1780). Port 1780 itself stays reachable on the LAN for
Snapweb. The nightstand frame no longer calls snapserver directly for playback data: since the
now-playing work it reads the hub's `/now-playing.json` (and covers via each track's `art_url`),
the "Now-playing file" contract above.

**homebody status file and settings API.** `homebody` (a separate repo, run on the hub as
`homebody.service` by the `homebody_bootstrap` role) tracks who is home and sets the thermostat.
`homebody run` is one process: the daemon loop and the settings API. snaphouse owns the unit, the
nginx routes and the port pin (`homebody_api_port: 8783` in `vars/all.yml`); homebody owns the
document and the API behaviour.

*Status file.* The daemon writes `/var/lib/snaphouse/public/homebody/homebody.json` atomically
(temp file in the same dir, fsync, rename), mode 0644, after every tick and right after a settings
change. The directory is owned by the `homebody` user (0755); the shared public root is never
chowned. nginx serves it at `location = /homebody.json` (`default_type application/json`, same
headers as `/now-playing.json`). All keys are always present, null when unknown:

```json
{
  "version": 1,
  "updated": "2026-11-13T15:04:05Z",
  "enabled": true,
  "occupied": true,
  "people": [
    {"name": "Display Name", "state": "home", "last_seen": "2026-11-13T15:03:58Z", "via": "wifi"}
  ],
  "thermostat": {"reachable": true, "mode": "cool", "setpoint_f": 72.0, "temperature_f": 74.3},
  "override": false,
  "last_change": {"at": "2026-11-13T14:40:00Z", "setpoint_f": 72.0, "reason": "home"},
  "settings": {"comfort_f": 72, "away_f": 82, "away_minutes": 20}
}
```

- `occupied`: true, false, or null (unknown, for example right after boot or with no enabled people).
- `people`: enabled people only, display names from local config. `state` is `home`, `away` or
  `unknown`; `via` is `wifi`, `bluetooth` or null.
- `thermostat.mode`: lower-case Matter system mode name (`off`, `cool`, `heat`, `auto`, ...) or null.
  `thermostat.reachable` is null until homebody's first thermostat read after start, then true or
  false; readers treat null as "not known yet". False means the controller or thermostat could not be
  read, and the other thermostat fields are then the last known values or null.
- `last_change`: the last command homebody applied (not hand changes), or null; `reason` is `home`
  or `away`.
- `enabled`: automation on or off. Off still tracks and publishes presence but sends no thermostat
  commands. Turning it on acts on the current occupancy at the next tick (subject to pacing and the
  override rule).
- Timestamps are UTC ISO 8601 with a Z, whole seconds.

*Settings API.* homebody listens on `127.0.0.1:8783` (its `[api] port`). nginx proxies
`location /api/homebody/` to it (`client_max_body_size 64k`). `POST /api/homebody/settings` takes a
JSON body with any subset of `enabled` (bool), `comfort_f` (int 60 to 85), `away_f` (int 65 to 90),
`away_minutes` (int 5 to 120); the merged result must keep `comfort_f` < `away_f`.

- The header `X-Snaphouse: 1` is required, else 403 `{"error": "..."}`. Non-JSON, unknown keys, wrong
  types or out-of-range values give 400 `{"error": "<plain sentence>"}`. Other paths 404, other
  methods 405.
- 200 returns the full status document (the same shape as `homebody.json`) after the change.
- Settings changed through the API persist in the daemon's state across restarts and override
  `local.toml`, which stays read-only to homebody.
- A changed `away_minutes` applies to the presence window from the next tick. A changed
  `comfort_f` or `away_f` with occupancy unchanged updates the target: homebody sends the new setpoint
  (paced, no override flagged for its own change), unless a hand-change override is active, which
  holds until the next transition.

*Request origin.* nginx sets `X-Snaphouse-Origin` on every `/api/homebody/` request with
`proxy_set_header`, so a client-supplied value is always overwritten. The value is `home` or
`tunnel`. A request is `home` only when all four Cloudflare headers (`Cf-Connecting-Ip`,
`Cf-Ray`, `Cf-Access-Authenticated-User-Email`, `Cf-Access-Jwt-Assertion`) are empty and its
source address is in `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`, `fc00::/7` or
`fe80::/10`; anything else is `tunnel`. The tunnel connector sits on the LAN (a separate
machine), not on loopback, so its requests arrive from a private address exactly like a phone
at home; the source address alone cannot tell them apart, and the Cloudflare headers are the
discriminator. Any one of them present means `tunnel`, which fails closed. homebody reads only
this header, and a missing header means `tunnel`. The `geo` and `map` live at http level in
`/etc/nginx/conf.d/homebody-origin.conf` (the `homebody_bootstrap` role installs it); the
location block only forwards the result. The Cloudflare Access headers pass through to homebody
untouched. A request marked `tunnel` must also carry the Access marker to command the door.

*Garage door status additions.* `door.last_known_state` (string or null, additive; the status
`version` stays 1) holds the last state seen before the link dropped, so the page can show
"Last seen N min ago (was closed)" while `door.state` is `unknown`.

*Garage door (the `garage` key).* `homebody.json` carries one extra key, `garage`, which is
`null` when the garage module is off (the page then hides the Garage tab). Every key is always
present, null when unknown; timestamps are UTC whole seconds; `door.state` is `unknown` whenever
`link.connected` is false, and the page never shows `closed` unless the link is live. `status.version`
stays 1. The shape:

```json
"garage": {
  "link": {"connected": true, "last_seen": "2026-11-14T18:02:11Z"},
  "door": {
    "state": "closed",
    "since": "2026-11-14T17:40:03Z",
    "since_approximate": false,
    "position": 0,
    "last_known_state": "closed"
  },
  "obstruction": false,
  "lockout": false,
  "light": false,
  "settings": {"left_open_minutes": 15, "away_open_grace_minutes": 5, "alerts": true, "remote": true},
  "recent": [
    {"at": "2026-11-14T17:40:03Z", "event": "closed", "origin": "home", "who": null}
  ]
}
```

`door.state` is one of `closed`, `open`, `opening`, `closing`, `stopped`, `unknown`. `position` is
0 to 100 or null. `recent` holds the last 10 door changes; `who` is null for a home command and, for a
tunnel command, a label from local config or a short tag, never an email or address.

*Garage endpoints.* All POST, JSON bodies, 64 KB cap, `X-Snaphouse: 1` required, served by the same
loopback API:

- `POST /api/homebody/garage/intent` takes `{"action": "open" | "close"}` and returns
  `{"nonce", "expires_in", "confirm_required"}` (the nonce lives 30 s and is single use). The page
  decides whether a second tap is needed from `confirm_required` alone, never from its own guess about
  where it is.
- `POST /api/homebody/garage/command` takes `{"action", "nonce"}` and returns the full status document.
  A command is a request for an end state, not a toggle: `open` when already open is a 200 no-op. It is
  never retried, by the hub or the page; nothing moves a door without a person's tap.
- `POST /api/homebody/settings` takes `garage_left_open_minutes` (0 to 240, 0 turns the alert off),
  `garage_away_open_grace_minutes` (0 to 15), `garage_alerts` (bool) and `garage_remote` (bool, default
  off; turning it on is refused from the tunnel, turning it off is allowed from anywhere).

Errors carry a plain `{"error": "<sentence>"}` the page shows as written:

| Status | Meaning |
|---|---|
| 400 | bad action, or a nonce that is spent, expired or unknown ("That request expired. Start again.") |
| 403 | missing guard header, remote control off, tunnel request without the Access marker, or remote enabling of remote control |
| 409 | obstruction reported, state unknown, or the door already moving the other way |
| 423 | the opener's remote lockout is on |
| 429 | rate limit |
| 503 | the opener link is down |
| 504 | the opener did not confirm movement ("No movement seen yet. Check the card before trying again.") |

After a command the page shows "Sent. Waiting for the door." and reads `homebody.json` every 2 s for up
to 15 s. The page reads the document every 5 s while the Garage tab shows and once a minute elsewhere.

*Garage sample fixture.* The sample document lives in homebody's `tests/fixtures/garage_status_sample.json`,
with a byte copy at `tests/js/fixtures/homebody-garage.json` in this repo. The two must change together:
homebody's test builds the document from the real code and compares it with its sample, and this
repo's `garage-model.test.js` and `site-assets.test.js` read the copy.

With no enabled people in `local.toml`, occupancy stays unknown and nothing is ever commanded. The
hub's `homebody-update` path restarts `homebody-matter homebody`, the same unit names the role
installs. The on-device check `homebody_status.sh` needs the unit active and `/homebody.json` served
as a JSON object (it retries 45 s for the first write, then fails naming the missing part).

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
