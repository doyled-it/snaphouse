# Now playing everywhere: design

Date: 2026-11-08. Repos: `snaphouse` (hub) and `nightstand` (frame).

## Why

The owner has been living with the system for a day and found these gaps:

- Titles do not fit on the frame. The cover takes 480 of the 800 px, so long
  music titles and most podcast episode titles get cut off.
- A podcast played from the phone over Bluetooth shows no image. The phone
  sends no art over Bluetooth, and the frame's cover lookup (MusicBrainz) does
  not know podcasts.
- Nothing shows a track's length or how far along it is.
- The room-picker web page shows only the title and artist, and nothing records
  what has played.
- Skipping through several tracks makes the frame flash once per skip. Each
  full refresh of the Spectra 6 panel flashes for roughly 20 to 30 seconds and
  that cannot be shortened (the waveform lives in the panel controller; partial
  refresh is unsupported on this panel and drifts the whites toward blue). The
  only lever is redrawing less often.

The owner plans to add more frames around the house. So the hub, not any one
frame, becomes the single source of now-playing data, artwork included.

## Goals

1. The hub publishes complete now-playing data for whatever is on House,
   whichever source it came from: title, artist, album, kind, duration,
   position, playback status, artwork URL, and the rooms it is playing in.
2. The hub finds artwork for tracks that arrive without any (Bluetooth): Apple's
   podcast search for podcasts, Open Library for audiobooks, MusicBrainz and the
   Cover Art Archive for music.
3. The hub keeps a play history of the last 50 plays.
4. The room-picker page shows a now-playing card (art, title, artist, album, a
   live progress bar with elapsed and total time, and the end time) and the
   play history.
5. The frame uses the new layout (below) for music and long-form audio, shows
   progress for long-form audio, and waits out skips before redrawing.

## Non-goals

- Partial or fast refresh on the panel.
- Playback control (pause, skip, seek) from the web page.
- Play history on the frame.
- Correcting for playback speed. A podcast at 1.5x will finish earlier than the
  end time and bar suggest; neither AirPlay nor AVRCP reports the rate reliably.
- Any of this data leaving the LAN.

## Anti-goals (would count as failure even if it works)

- The frame redrawing more often than every 10 minutes because of progress.
- Anything identifying in a lookup request. Only the track, show, book, or
  artist names go out, with a User-Agent naming the public repo.
- A slow or failing artwork lookup delaying audio, the metadata bridges, or the
  now-playing file.

## Decided by the owner

- **Frame layout "B" for all kinds.** A 320x320 cover in the top-left corner.
  Beside it, from x = 342 to the right margin: the title in Inter Black, up to 4
  lines, shrinking from 52 px down to 28 px until it fits, ellipsized only at
  28 px; then the artist in bold (1 line, 30 px), then the album in regular (1
  line, 26 px). Below the cover, a full-width strip (28 px margins) holds the
  progress line, and the room chips sit at the bottom-left as today. With no
  cover, the text column starts at x = 40 and runs full width, with the same
  strip and chips. Reference renders of the agreed mockup: see "Reference
  mockups" below.
- **Progress on the frame.**
  - Long-form (see "Kind" below) with a known duration and position: a bar
    (black 2 px outline, red fill for elapsed) across the strip, and under it
    `23:10 of 52:00  ·  ends 9:46`. The bar is redrawn every 10 minutes of
    playback, never more often.
  - Music with a known duration: no bar, just `5:22  ·  ends 9:14`. Music never
    redraws mid-track.
  - Unknown duration: no progress line.
- **Wait out skips.** The frame draws a new track only after it has been the
  current track for 20 seconds, so skipping through five tracks causes one
  flash. The owner chose this and declined the other flash reducers (ignoring
  room changes, slower art rotation), so those stay as they are.
- **Play history: the last 50 plays.** A play counts once it has been the
  current track for 30 seconds, so skips do not fill the list.
- **The hub does the artwork lookups**, because more frames are coming. This
  adds lookup hosts to the hub's outbound list (below).
- **Pauses hold for 10 minutes.** A paused track stays on the frame (and in
  `now-playing.json` with `status: paused`) for 10 minutes, then `track`
  becomes `null` and the frame returns to its schedule (art). Resuming within
  the 10 minutes causes no redraw.

## Kind

`long_form` when the duration is 20 minutes (1200 s) or more, or the sender's
genre names a podcast or audiobook (case-insensitive match on "podcast",
"audiobook", "spoken"). Otherwise `music`. A track with no duration and no
telling genre is `music`.

## What the senders give and what must be added

AirPlay (shairport-sync 3.3.8 metadata pipe, `lib/snaphouse/airplay_meta.py`).
Today the bridge forwards `core/minm`, `core/asar`, `core/asal`, the cover
(`ssnc/PICT`, published as a URL under `/cover/`), and the play state. Add:

- `ssnc/prgr`: `start/current/end` RTP frame numbers at 44100 Hz. Duration is
  `(end - start) / 44100`, position is `(current - start) / 44100` at the
  moment it arrives. It arrives at track start and on resume or seek.
- `core/astm` (song time in ms) as a duration fallback when `prgr` is absent.
- `core/asgn` (genre).

Bluetooth (BlueZ `org.bluez.MediaPlayer1`, `lib/snaphouse/avrcp_meta.py`).
Today it forwards `Track` (Title, Artist, Album, Duration) and `Status`. Add:

- `Position` (uint32 ms), which BlueZ updates on status changes and seeks, not
  continuously.
- `Track.Genre`.

Both bridges put these into the Snapcast stream properties they already emit:
`metadata.duration` (seconds), `metadata.genre` (a list), and the top-level
`position` property (seconds). Snapcast drops unknown metadata keys, so
anything beyond its known fields must not be relied on.

Bluetooth field mapping ships with the stated default (show from the album,
falling back to the artist; episode from the title) and is confirmed afterward.
Open risk for the builder: which AVRCP fields the owner's podcast and
audiobook apps actually fill (episode in Title, show in Album or Artist, genre
or not) is unknown. Capture a real sample from the owner's phone (with the
owner present, the same way the Vizio and JBL were paired) as a follow-up step
after the bridges ship, and adjust the mapping and the genre list if the sample
disagrees. It is not a gate on the rest of the work.

## The hub's now-playing service

A new long-running hub service, `snaphouse-nowplaying`, in
`lib/snaphouse/nowplaying.py` (stdlib plus what the hub venv already has). It:

1. Follows the House stream through snapserver's JSON-RPC notifications on the
   control port (TCP 1705), with a `Server.GetStatus` on connect and on every
   reconnect.
2. Anchors position. Each time a properties update carries `position`, it
   records `(position, the time it arrived)`. Consumers extrapolate from that
   anchor while the status is playing. Snapserver itself carries no timestamp,
   which is why consumers read this file and not snapserver directly.
3. Resolves artwork for a track with no `artUrl`, in the background, never
   blocking the file write. The file is written first with `art_url: null` and
   rewritten when the art lands. Lookup order:
   - `long_form`, or genre names a podcast: Apple's iTunes Search API,
     `https://itunes.apple.com/search?media=podcast&entity=podcast&term=<show>`
     where the show is the album, falling back to the artist. Prefer the result
     whose `collectionName` matches case-insensitively; use `artworkUrl600`.
   - Genre names an audiobook, or the podcast search found nothing for a
     long-form track: Open Library search by title (the album) and author (the
     artist), then `https://covers.openlibrary.org/b/id/<cover_i>-L.jpg`.
   - Music: MusicBrainz release search by artist and album, then the Cover Art
     Archive front image (port the frame's `sources/coverart.py` logic,
     including its 1 request per second gate and User-Agent).
   - When the genre is present and names neither a podcast nor an audiobook
     (a long DJ mix tagged as music, say), it goes to the music lookup despite
     its length: genre outranks duration for choosing the lookup.
   - No exact match means no art. A result counts only when its name matches
     the searched name case-insensitively after trimming punctuation and
     whitespace (and, for music, the artist matches too). A wrong cover in the
     history is worse than the placeholder.
   - Looked-up images go in their own subdirectory, `/cover/lookup/`, keyed by
     a hash of the lookup and resized to at most 600 px. A miss is cached for 7
     days. The service prunes only its own subdirectory and the AirPlay bridge
     prunes only its own files, so neither deletes the other's.
   - History thumbnails survive pruning: when a play is recorded, the service
     copies its cover into `/cover/history/` and points the play's `art_url`
     there; those files are deleted when their play falls off the 50.
4. Writes `now-playing.json` and `play-history.json` atomically into the hub's
   public web root (`/var/lib/snaphouse/public/`), served same-origin by nginx.
5. Restarts on failure (systemd), survives snapserver restarts, and reloads
   history from disk at start.

`verify.d` gains a check that the service is active and the file exists and
parses.

### `now-playing.json`

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

`track` is `null` when nothing with a title is on House, and also once a track
has been paused (or the House stream idle) for 10 minutes; before that it stays
with `status: paused`. A track with an empty `rooms` list (every room muted or
disconnected) is still published; the frame treats empty `rooms` as nothing
playing, as it does today, while the web page still shows it. `artist`,
`album`, `duration`, `position`, `position_at`, and `art_url` may each be
`null`. `status` is `playing` or `paused`. `source` is `airplay` or
`bluetooth`. `rooms` uses the same rule the frame uses today (connected,
unmuted clients on the House stream, by display name). `art_url` is always a
same-origin path on the hub: the service rewrites the AirPlay bridge's
absolute `http://<host>/cover/...` URL to its path, and the bridge keeps its
current format.

`position` means the position at the moment the properties were emitted. Each
bridge keeps its own anchor (the value it last received and when) and
extrapolates while playing each time it emits, so a properties resend for an
unrelated change (status, art) never carries a stale position. The service
then anchors `(position, arrival time)` on every update.

`started_at` is when the service first saw this track identity playing. After a
service restart mid-track, if the newest history entry has the same identity
and started less than its duration plus 10 minutes ago, it is the same play and
keeps its `started_at`; otherwise `started_at` is the restart time.

### `play-history.json`

```json
{
  "plays": [
    {
      "title": "...", "artist": "...", "album": "...",
      "kind": "music", "source": "airplay",
      "duration": 322.0, "started_at": "2026-11-08T21:01:10-08:00",
      "art_url": "/cover/....jpg"
    }
  ]
}
```

Newest first, at most 50. A play is added once a track has been current for
30 seconds. A track's identity is (title, artist, album); pausing and resuming
the same track is the same play. Its `art_url` is filled in later if the lookup
finishes after the play was recorded.

Both shapes go into `docs/cross-repo-features.md` as pinned contracts.

### Hub outbound list

Add to the hub's documented outbound list (the design spec and AGENTS.md):
`itunes.apple.com`, `musicbrainz.org`, `coverartarchive.org` (and the archive
hosts it redirects to), `openlibrary.org`, `covers.openlibrary.org`. Requests
carry only names and a User-Agent naming the public repo.

## The room-picker page

- **Now-playing card** at the top: cover (or a plain placeholder), title,
  artist, album, and for anything with a duration a progress bar with
  `elapsed / total` and `ends 9:46`, ticking every second client-side from
  `position` and `position_at` while playing and frozen while paused. Reads
  `now-playing.json` (polling every 5 seconds is fine).
- **History** section below: the last 50 plays, newest first, each with a small
  thumbnail, title, artist and album, the clock time it started (with the date
  when not today), and its length. Same visual language as the existing art
  history section.
- Model logic goes in `rooms-model.js` with node tests, as today.

## The frame

- Reads `now-playing.json` from the hub every 10 seconds (a small LAN file
  read; the frame's other sources keep their own cadence), with the same
  timeouts and outage handling as its other hub reads. A failed read degrades to
  nothing playing, as today, so it falls under the settle like any other change instead of building now-playing from snapserver's
  `Server.GetStatus`. Covers come from the hub's `art_url`. The frame's own
  MusicBrainz lookup is removed, and `musicbrainz.org` and `coverartarchive.org`
  leave the frame's allowlist (builder's call recorded here: one lookup place).
- New now-playing screen per the layout above.
- **Redraw key.** Track identity and rooms, as today, plus for `long_form`
  tracks that are playing with a known position, the 10-minute bucket of the
  extrapolated position (`int(position_now // 600)`). Paused tracks freeze the
  bucket.
- **Settle.** When the now-playing track identity changes (including to or from
  nothing playing), the frame holds the current screen until the new state has
  been stable for 20 seconds. Bucket changes and room changes within the same
  track do not wait. A paused track is still the current track (no redraw on
  pause or resume). The split (morning) screen uses the same settle; its layout
  is otherwise unchanged. During quiet hours, settled changes and bucket
  changes simply wait like every other non-game push; at the end of quiet hours
  the key is computed fresh, so the redraw shows the current bucket.
- End time: `now + (duration - position_now)` in local time, formatted like the
  frame's other clock times. With a duration but no position, use
  `started_at + duration`.

## Rollout order

1. snaphouse: bridges (AirPlay `prgr`/`astm`/`asgn`, AVRCP `Position`/`Genre`).
2. snaphouse: `snaphouse-nowplaying` service, both files, art lookups, verify
   check, contracts, outbound list.
3. snaphouse: room-picker card and history.
4. nightstand: switch to `now-playing.json`, new layout, progress, settle.
   Merged only after step 2 is live on the hub, so no fallback to the old
   snapserver path is needed.

## Reference mockups

Throwaway renders made with the frame's own fonts, palette, and panel
simulation, shown to the owner on 2026-11-08: layout B was chosen for both music
and podcasts. The builder reproduces the layout from the numbers in "Decided by
the owner"; the mockup script is not part of either repo.

## Left to the builder

Module boundaries inside each repo, the art cache bound and eviction, the exact
reconnect and backoff policy, how the room-picker card degrades when the file is
missing, test fixtures (record real `prgr` and AVRCP samples where possible),
and golden images for the new frame screen.
