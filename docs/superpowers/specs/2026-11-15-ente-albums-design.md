# Ente albums on the frame: design

Date: 2026-11-15. Issue: #172. Status: draft for owner approval.

## Intent

The owner wants the bedroom frame to show their own pictures alongside the museum art: memes
they collect, and photos of friends and family. Both live in Ente Photos (Ente's cloud) as
albums. Adding a picture to one of those albums in Ente should be enough for it to reach the
frame, with no upload step and no visit to the house page.

Success: the owner drops a meme or a family photo into the Ente album on their phone, and some
time later it comes up on the frame in the normal rotation, filling the screen like the art does.
Removing it from the album takes it off the frame.

## Goals

- A hub service keeps a local mirror of the images in the Ente albums shared with a dedicated house account.
- The hub's art library reads that export directly (no second copy of each original beyond the mirror itself) and lists
  each album as a folder in the Gallery, with the existing on/off switch.
- Album pictures rotate on the frame from the same pool as the art, every enabled picture equally
  likely (the current picker's behaviour), cropped to fill the 800x480 panel like the art.
- Album pictures are not held back by the landscape-only rule (`min_aspect`, 1.1 on the hub): a
  tall meme or portrait photo rotates too.
- iPhone photos (HEIC) work everywhere a picture is shown: Gallery grid, Gallery viewer, frame.

## Non-goals

- Videos and the motion part of Live Photos (the still of a Live Photo is used).
- Choosing albums inside the Gallery. The selection is "whatever is shared with the house
  account in Ente"; the Gallery switch then turns each album on or off for the frame.
- Writing anything back to Ente (no uploads, deletes or edits from the house page).
- Face-aware cropping. The frame's existing detail-aware crop is used as is.
- Weighting or turn-taking between folders (owner chose "every picture equal").
- Self-hosted Ente, several Ente accounts, the owner's main account.

## Anti-goals (would be a failure even if it works)

- The hub holding the keys to the owner's whole photo library. It signs in only to a separate,
  free house account that the owner shares the frame albums with.
- Gallery actions on an album picture that the next sync silently undoes (upload into, delete,
  move, rename an album folder). These are refused with a clear message instead.
- Credentials, album names, people's names or photos in the repo, PRs, logs that leave the hub,
  or any web query.
- Breaking the existing art library, its ids, or the frame's art sync for the art folders.

## Owner decisions (from the brainstorm)

1. Sources: both memes and friends-and-family photos come from Ente albums (Ente cloud).
2. Account: a separate free Ente account for the house; the owner shares the albums with it.
3. Tall pictures: crop to fill, same as the art (owner accepted that a meme caption or the top
   of a portrait can be cropped away).
4. Mix: every enabled picture equally likely, one shared pool with the art.
5. Approach: the library reads the Ente export directly rather than copying originals into the
   art library.

## Approaches considered

1. **Copy into the library.** Export to a private staging area, then copy (HEIC converted to
   JPEG, videos skipped) into read-only library folders. Downstream code unchanged; a second copy
   of every photo. Recommended by the controller, not chosen.
2. **Read the export directly (chosen).** The library learns a second, read-only source: the
   Ente mirror root. No second copy of originals; the library, the `/art/` serving path and the
   name rules learn Ente's layout.
3. **Run Ente on the frame.** Rejected: credentials on another device, and it bypasses the
   Gallery and the hub as the single source of truth.

## How it works

### Ente sync service (hub, snaphouse)

- The official Ente CLI (version 0.4.4, Linux amd64 release `ente-cli-v0.4.4-linux-amd64.tar.gz`,
  verified against its pinned SHA-256), installed by Ansible. The hub does not use
  `ente photos export`, which downloads videos too. Its own sync is built on three CLI calls, all
  run with `--no-input`: `ente photos album list --json`, `ente photos file list --album <id>
  --type image --type livephoto --all --json` (`--all` is required, the default limit is 100), and
  `ente photos file download <fileId> --album <albumId> --output <new path>` (the output must not
  exist, so the sync writes a temp name and renames). Videos are never listed or downloaded.
- A dedicated system user owns the CLI home (`ENTE_CLI_HOME`) and the mirror. The CLI vault key is
  made once on the hub with `ente vault key generate`, kept in a file readable only by that user,
  outside the repo (`ENTE_CLI_VAULT_KEY`). Paths follow the repo's `/var/lib/snaphouse`
  conventions.
- Login is a one-time manual step on the hub (`ente account login --app photos --account house`,
  interactive, owner types the house account's password). Nothing about the account goes in the
  repo or in Ansible variables. Until `ente account list --json` shows the account logged in for
  photos, the timer runs and exits cleanly as "not set up" (same pattern as the garage module
  being off until configured).
- A systemd timer runs the sync every 15 minutes (new pictures appear within about 15 minutes plus
  the frame's own art sync interval). Albums shared with the house account appear in the album
  list (their `ownerId` differs from the account's own id). Hidden albums (by `visibility`) are
  skipped.
- Local mirror layout: `<root>/<albumId>/<fileId>.<ext>` plus one small record per album
  (`album.json`: album title, album `updatedAt`, and for each file its `updatedAt`, `hash` and
  extension). A file whose listed `updatedAt` or `hash` changed is downloaded again. A file no
  longer listed is deleted on the next run; an album no longer in the list (unshared) disappears
  with its folder. Ente album and file ids are stable, so renames never change a folder or piece
  id.
- An image downloads as the original. A Live Photo downloads as a ZIP holding the still plus a
  video member; the sync keeps only the image member and never extracts the video.
- Every download goes to a temp name and is renamed into place, and records are replaced
  atomically, so a run that fails partway leaves the mirror consistent. The error is recorded in
  the status record.
- The service writes a small status record (last successful sync time, last error in plain words,
  no account details) that the art admin publishes so the Gallery can show "Synced from Ente 5 min
  ago" or "Ente sync is failing: ..." on album folders.

### Library: Ente albums as read-only folders (snaphouse `artlib.py`, `artadmin.py`)

- The library gains an optional second root, the Ente mirror root. Each album directory under it
  becomes a folder in `art-index.json`, marked as coming from Ente (e.g. a `source: "ente"` field;
  exact contract is the builder's call, and the contract docs must be updated).
- Inside an album, only the images the sync downloaded count (the sync lists only `image` and
  `livephoto` files; the library also ignores `album.json`, temp names and hidden files).
- Names: Ente album and file ids are stable, so folder ids are derived directly from them:
  `ente-<albumId>` for the folder and `<fileId>.jpg` for the piece (ids are validated to be short
  alphanumeric strings so they pass the existing validators in the hub, the page
  (`rooms-model.js`) and the frame (`artsync.py`, `art.py`)). The `ente-` prefix is reserved so an
  art folder can never collide with an album. The display title is the album's real name
  (from `album.json`). No existing validator is loosened for the art folders.
- Serving: album pictures must be served same-origin under the `/art/` path family the page and
  the frame already accept (nginx `location` for the mirror root, read-only, no directory listing
  of the export's metadata). Exact URL scheme is the builder's call within that constraint.
- Renditions: the owner expects 200 to 1,000 album pictures in a year. The frame mirrors every
  enabled piece to its SD card and only needs 800x480, and browsers other than Safari cannot show
  HEIC. So for every Ente picture (not only HEIC) the hub produces a derived, downscaled JPEG
  rendition in its cache (long side about 1600 px; exact size is the builder's call), generated
  and pruned like thumbnails today, and the piece's `url` points to the rendition. The page and
  the frame only ever receive these renditions; 1,000 pictures stay within a few hundred MB on the
  frame. Thumbnails come from the same pipeline. Originals are never copied. (Controller's call:
  it keeps HEIC and multi-MB originals away from the browser and the frame.)
- Read-only: upload into, delete, restore, move, rename, and folder delete on an Ente folder or
  piece are refused by the admin API with a plain message ("This picture comes from Ente; change
  it in Ente."). The Gallery hides those actions on Ente folders and pieces. The on/off switch,
  "Show now", pin, and the viewer work as for art.
- New albums appear switched on (controller's call: the owner wants additions to reach the frame
  by themselves), and an album the owner switched off stays off. `folders_enabled` is an
  allow-list, so the control state gains a record of the Ente albums already seen; a migration of
  the control file is acceptable. The switch state persists by folder identity, so a renamed
  album in Ente keeps its state because the folder id derives from Ente's stable album id.
- Removal: the sync itself reconciles. A file no longer in the album's file list is deleted from
  the mirror, so removing a picture from a shared album takes it off the hub and the frame, and
  unsharing an album removes its folder. A failed listing never deletes anything.

### Landscape rule exemption (snaphouse index + nightstand picker)

- Today the hub marks `landscape` per piece using `min_aspect`, and the frame's `ArtLibrary.eligible`
  and `pick` skip anything narrower than `min_aspect`. Pieces from Ente folders must be eligible
  whatever their shape. The index carries the per-folder (or per-piece) flag. The frame works from
  its local mirror, not the index, so the flag must survive the mirror: a sidecar field, a
  reserved folder naming, or a small manifest written by the art sync are all acceptable
  (builder's call), as long as an older frame build ignores it safely and switching an album off
  still removes its pictures from the frame. The Gallery's "in rotation" summary counts them.
- The frame's crop (`screens/art.py`, `detail_crop`) is unchanged: fill and crop, sliding toward
  the busiest region.

### Away access

On the home network `/art/` has no login, so anyone on home Wi-Fi (guests included) can see album
pictures in the Gallery, same as the art. The owner confirmed that is fine. Away from home the
Gallery and `/art/` are reachable through the Cloudflare tunnel behind Cloudflare Access, so only
signed-in people see them. No extra access work.

## Repos touched

- snaphouse: new Ente sync role (CLI install, user, timer, status), artlib/artadmin changes,
  nginx location, Gallery UI (read-only Ente folders, sync status line), contract docs, tests.
- nightstand: picker exemption for Ente pieces, art sync keeps the new flag, tests.
- No homebody changes.

## Install day (owner, once)

1. Create a free Ente account for the house (its own email).
2. In the main Ente account, share the memes album and the friends-and-family album with it.
3. On the hub, log the CLI in as the service user with `ente account login --app photos --account
   house` (controller walks through it; the password is typed by the owner, never stored outside
   the CLI vault).
4. Run the sync once by hand, check the Gallery shows both albums, switch them on.

## Left to the builder

- Exact paths, unit names, the index contract shape for the Ente flag, the URL scheme under
  `/art/`, the id derivation, how the status record reaches the page, the HEIC library choice
  (e.g. `pillow-heif`) and how it is installed with uv.
- Splitting the work into PRs (snaphouse before nightstand, since the frame consumes the index).
