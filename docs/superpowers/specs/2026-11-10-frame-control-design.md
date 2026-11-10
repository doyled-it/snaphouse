# Frame control and a screen-based room picker: design

Date: 2026-11-10. Repos: `snaphouse` (hub, page) and `nightstand` (frame).

## Why

After living with the frame and the redesigned page, the owner asked for:

- Control over what takes the frame: the now-playing screen should appear only
  for chosen kinds of audio (today every track takes over the frame).
- A way to keep something on the frame: pin the current art piece or the weather,
  for a while or until unpinned.
- A better organized page than one long scroll behind a hamburger.
- Book covers shown whole: a tall audiobook cover was cropped to a square and lost
  its title.

## Decided by the owner

- **Page:** the hamburger switches between separate screens: Listen (now playing,
  rooms, play history), Frame (the live mirror, Back/Next, pinning, what the frame
  shows), Art (library, folders, upload, trash), and Settings.
- **Now playing on the frame:** music only by default. Podcasts and audiobooks do
  not take the frame. This is a setting with one toggle per kind (music,
  podcasts, audiobooks).
- **Pinning:** pin the current art piece, the weather, or now playing, for 1 hour,
  4 hours, until tomorrow morning, or until unpinned. While pinned, the frame
  ignores its normal schedule.
- **Covers:** a cover that is not square is shown whole inside the cover square,
  centred, with paper-coloured margins; nothing is cropped.

## Kinds of audio

The hub already publishes `kind` (`music` or `long_form`) and `genre`. The frame
derives a category: `audiobook` when the genre names an audiobook or spoken word,
`podcast` when it names a podcast or when the track is `long_form` with no
telling genre (libro.fm sends no genre, so with the owner's defaults a book with
no genre also stays off the frame, which matches the owner's choice), otherwise
`music`.

## Hub: frame settings and pin

The art admin service (`snaphouse-artadmin`, already serving `/api/art/` with the
`X-Snaphouse: 1` guard) also owns frame settings, persisted beside its art state
and published as `/frame-control.json`:

```json
{
  "settings": {
    "now_playing_kinds": ["music"],
    "quiet_hours": {"start": "22:00", "end": "06:00"},
    "morning_weather": {"enabled": true,
                        "weekday": ["06:00", "08:30"],
                        "weekend": ["07:00", "09:00"]}
  },
  "pin": null
}
```

`pin` is `null` or `{"what": "art" | "weather" | "now_playing", "piece": id | null,
"until": iso | null, "seq": n, "set_at": iso}`. `piece` is the art id pinned
(the piece showing when the pin was set). `until` null means until unpinned; the
"until tomorrow morning" choice resolves on the hub to the next morning-window
start for that day of the week. `seq` increases on every pin or unpin so a frame
can tell a change and redraw once.

API (POST, JSON, header guard, JSON errors like the art API):

| Path | Body | Effect |
|---|---|---|
| `/api/frame/settings` | any subset of `settings` | validate and store (times `HH:MM`, kinds from the three, morning windows start before end) |
| `/api/frame/pin` | `{"what", "duration"}` with duration `1h`, `4h`, `morning`, or `forever` | set the pin; for `art` record the current piece from the frame's art history |
| `/api/frame/unpin` | `{}` | clear the pin |

Defaults when no state exists: kinds `["music"]`, quiet hours and morning windows
equal to the frame's current local config values (22:00 to 06:00; 06:00 to 08:30
weekdays; 07:00 to 09:00 weekends).

## Frame: follow the hub

- Read `/frame-control.json` every cycle (like `/art-control.json`); a missing or
  bad file falls back to local config, and to "no pin".
- Now playing takes the frame only when its category is in `now_playing_kinds`;
  otherwise the decision behaves as if nothing were playing (art, weather, game).
- Quiet hours and the morning window come from the hub settings when present.
  Disabling morning weather removes the morning screen.
- Pin: while a pin is active (not expired), the decision is the pinned screen:
  `art` shows the pinned piece (rotation does not advance), `weather` shows the
  weather screen and keeps the forecast fetched every 30 minutes, `now_playing`
  shows now playing for any kind (falling back to art when nothing plays). A pin
  change (new `seq`) forces one redraw even in quiet hours; an expiring pin returns
  to the normal schedule at the next permitted redraw. A live game still wins over
  a pin (call: the game is the one thing allowed to update overnight already).
- Covers: `now_playing.render` fits a non-square cover (aspect off square by more
  than 5%) whole inside the 320 px square on a paper background, centred; square
  covers keep today's cover crop.

## Page: screens

- Hamburger drawer lists Listen, Frame, Art, Settings. Each is its own screen,
  addressed by the URL hash (`#listen`, `#frame`, `#art`, `#settings`) so the
  browser Back button and refresh keep the screen. Listen is the default.
- **Listen:** now playing card, rooms, play history (with Open in).
- **Frame:** the mirror and caption, Back, Next piece, the piece menu, the Pin
  control (what: this art piece, weather, now playing; for: 1 hour, 4 hours, until
  morning, until I unpin), the current pin with an Unpin button, and the art
  history.
- **Art:** library (paged), folders, upload, trash.
- **Settings:** now playing on the frame (three toggles), rotation interval and
  folders in rotation, quiet hours, morning weather (on/off and times).
- Same visual system as today (panel inks, light/dark); same quality floor.

## Non-goals

Per-frame settings (one set for every frame for now), editing the screen priority
order, schedules beyond quiet hours and the morning window.
