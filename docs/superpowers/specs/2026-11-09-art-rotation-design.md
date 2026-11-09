# Art rotation control: design

Date: 2026-11-09. Issue: snaphouse #59. Repos: `snaphouse` (hub) and
`nightstand` (frame).

## Why

The frame rotates through the art collection on a fixed hourly clock, and the
only way to change the collection is the SMB share from a computer. The owner
asked to control the rotation from the room-picker page on a phone:

- delete pieces, and add new artwork or photos;
- skip to the next piece, go back to the previous one, and pick a piece to show
  now;
- put pieces in folders and choose which folders rotate;
- change how often it rotates, and similar settings.

The owner asked for this to be built overnight while asleep, so every design
call below is the controller's, marked "(call)" where it is a judgment the owner
may want to change. The owner also plans more frames, so the hub owns the
library and the settings, and frames follow.

## Goals

1. Manage the library from the room picker: upload, delete (recoverable), move
   between folders, create, rename, and remove empty folders.
2. Control rotation: next, previous, show a chosen piece now, the rotation
   interval, and which folders are in rotation.
3. Every frame follows the hub's library, settings, and commands.

## Non-goals

- Per-frame settings or per-frame commands (every frame follows one rotation for
  now; the contract leaves room to add a frame id later).
- Editing images (crop, rotate, filters).
- Accounts or passwords on the page. It is LAN-only, like the volume controls.
- Any access from outside the LAN.

## Anti-goals

- Losing a piece by a mis-tap. Delete is a move to a trash folder, restorable
  for 30 days.
- Leaking location: uploaded photos keep no EXIF (GPS, camera, timestamps).
- A web page on the internet driving the hub through the owner's browser. Every
  write needs a custom request header a cross-site form cannot send.

## Decisions

### Library layout (call)

The library stays at `/srv/snaphouse/art`, shared over SMB as today. Files at
the top level form the built-in folder "Collection" (the 172 museum pieces live
there). Each subdirectory one level down is a user folder; deeper nesting is not
supported and is ignored. `.trash/` holds deleted pieces (hidden from the
listing). Sidecar captions keep their format (`<image>.txt`: title, artist,
optional https source URL) and move with their image. Pieces are identified by
their path relative to the library root (`met-334896.jpg`,
`Photos/beach-2026.jpg`), validated so an id can never escape the root.

### Rotation rules (call)

- Interval choices on the page: 15 and 30 minutes, 1, 2, 3, 6, 12, and 24
  hours. Default stays 60 minutes.
- Rotation advances when the interval has passed since the last change (any
  change: a rotation step, next, previous, or show), not on fixed clock slots, so
  pressing Next does not get overridden a minute later by a slot boundary.
- Order: shuffled, reshuffled daily, as today.
- Only pieces in enabled folders that pass the existing landscape rule (aspect
  at least `art.min_aspect`, 1.3) rotate. Portrait pieces stay in the library and
  the page marks them "too tall for the frame" (the owner chose landscape-only
  earlier). Show-now on a portrait piece is refused with that message.
- Previous walks back through what this frame actually showed (a back stack of
  the last 20), not backward through the shuffle.
- A show-now piece stays up for one full interval, then rotation resumes.
- Explicit commands (next, previous, show) redraw immediately, even in quiet
  hours: someone pressed the button and is awake (call). Scheduled rotation
  still respects quiet hours.
- Every command costs one full panel refresh (about 20 to 30 seconds of
  flashing); the page says so next to the buttons.

### Hub: library index, control file, and an admin API

A new hub service, `snaphouse-artadmin` (`lib/snaphouse/artadmin.py`,
stdlib `http.server` with Pillow from the hub extra), listens on
`127.0.0.1:8781`. nginx proxies `/api/art/` to it and sets the upload size
limit. It runs as the `art` user that owns the library, so SMB access keeps
working, with systemd hardening.

It maintains two public files (same-origin, nginx exact-match locations) and a
thumbnail directory, all rewritten atomically after each change and on a
periodic rescan (every 60 s and at start) so SMB edits show up too:

`/art-index.json`:

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

`/art-control.json`:

```json
{
  "settings": {"rotate_minutes": 60, "folders_enabled": ["Collection"]},
  "command": {"seq": 7, "action": "next", "piece": null,
              "issued_at": "2026-11-09T06:59:10-08:00"}
}
```

`command` is `null` until the first command. `action` is `next`, `previous`, or
`show` (`piece` is the id for `show`). `seq` only increases; a frame acts on a
`seq` greater than the last one it applied. A frame that starts up and sees an
existing command records its `seq` without acting on it.

The settings and the last command persist in `/var/lib/snaphouse/art-control.json`
(the public copy is rewritten from it). Folder enablement is stored by folder
name; renaming a folder carries its enabled state.

API (all `POST`, JSON in and out, every request must carry the header
`X-Snaphouse: 1`; anything else gets 403, so a cross-site page cannot submit to
it without a CORS preflight the server never approves):

| Path | Body | Effect |
|---|---|---|
| `/api/art/upload` | multipart: `file` (one or more), `folder`, optional `title`, `artist` | Validate and add images |
| `/api/art/delete` | `{"id"}` | Move the image and its sidecar to `.trash/<timestamp>/` |
| `/api/art/restore` | `{"id"}` (a trash id) | Move back to its original place; suffix the name if taken |
| `/api/art/move` | `{"id", "folder"}` | Move image and sidecar to another folder |
| `/api/art/folder/create` | `{"name"}` | New empty folder |
| `/api/art/folder/rename` | `{"from", "to"}` | Rename (not "Collection") |
| `/api/art/folder/delete` | `{"name"}` | Remove an empty user folder |
| `/api/art/folder/enable` | `{"name", "enabled"}` | Toggle rotation for a folder |
| `/api/art/settings` | `{"rotate_minutes"}` | One of the allowed intervals |
| `/api/art/command` | `{"action", "piece"?}` | Issue next, previous, or show |

Errors are JSON `{"error": "<message for the page>"}` with 400, 403, 404, 409,
or 413.

Uploads: JPEG, PNG, or WebP only (decoded with Pillow to prove it), at most 25
MB each (nginx and the service both enforce it), EXIF orientation applied to the
pixels and all metadata stripped, re-encoded (JPEG quality 92, PNG kept as PNG),
stored under a sanitized name derived from the original (lowercase, `[a-z0-9-]`,
a numeric suffix on collisions). A title, when given, goes in a new sidecar;
otherwise the title falls back to the filename as today. Folder names: 1 to 40
characters, letters, digits, spaces, `-` and `_`, not starting with `.`.

Trash entries older than 30 days are purged on the periodic rescan.

Thumbnails: 320 px on the long side, JPEG, keyed by a hash of id, size, and
mtime, under `/var/lib/snaphouse/public/art-thumbs/`, pruned when no piece or
trash entry references them.

### Room picker: the art manager

The existing "On the frame" section grows into the manager (call: one page, no
separate admin page):

- Now showing, with Back and Next buttons and the redraw note.
- Rotation: an interval selector and folder chips (tap to toggle; counts shown).
- Library: a thumbnail grid filtered by folder (default: all), each piece with a
  menu: Show now, Move to folder, Delete. Portrait pieces get the badge and no
  Show now.
- Upload: a button (`accept="image/*"`, multiple) with a folder choice; a
  progress line per file and the server's error text on failure.
- Folders: create, rename, delete-if-empty.
- Trash: a collapsed list with Restore.
- The existing art history list stays.

### Frame: follow the hub

- Sync reads `/art-index.json` instead of the flat autoindex and mirrors every
  piece in an enabled folder into the local art directory, keeping the folder
  structure (each path segment checked with the existing safe-name rule), and
  removes local pieces no longer listed or no longer enabled. Downloads use the
  piece `url`. A missing or bad index changes nothing locally, as today.
- The loop reads `/art-control.json` on each cycle (10 s). Settings there
  override `art.rotate_minutes`; local config is the fallback when the hub file is
  missing or bad.
- Rotation state lives in the frame's state directory (current id, when it was
  shown, last applied `seq`, the back stack), so a restart resumes rather than
  jumping.
- The art redraw key becomes `("art", current_id)`; the art history keeps
  recording what was shown.

## Rollout

1. snaphouse: library core (scan, index, thumbnails, uploads, trash, folders,
   control file) as a library module with tests.
2. snaphouse: the admin service, its unit, nginx, verify check, contracts.
3. snaphouse: the room-picker art manager.
4. nightstand: sync from the index, rotation state machine, commands.

Merge order: 1, 2, then 3 and 4 (either order; the frame keeps working from its
old sync until 4 lands because the old flat listing keeps working for top-level
files).

## Left to the builder

Module boundaries, the exact page layout and styling within the existing
visual language, thumbnail cache bounds, the rescan implementation, and test
fixtures.
