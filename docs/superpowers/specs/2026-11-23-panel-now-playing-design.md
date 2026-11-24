# Panel Now Playing design

Date: 2026-11-23. Status: draft for owner approval (builder check done).

## Intent

Music is what people do at the panel most. The owner wants the panel's Music
tab to lead with what is playing, with the room volumes one tap away, and the
idle screen to show the music while it plays instead of the Aurora. They also
want to resume a paused Bluetooth stream from the panel at any time while the
phone stays connected; today the hub forgets a paused track after a 600 s hold,
so the panel drops to "Nothing playing" and hides Play.

The approved mockup is `2026-11-23-panel-layout-mockup.html` beside this file
(Music page: "Screensaver while playing", "Now playing", "Paused", "Rooms",
"Nothing playing"; open it in a browser, the black box is the panel at
800x480). Placeholders are not data.

## Goals

- **Now playing is the Music tab's first screen while there is a track**
  (playing, or paused and still resumable). Big cover (300 px), title (34 px,
  two lines at most), artist (24 px), progress with elapsed and total times,
  previous, play/pause and next (92 px tall), and a Rooms button (64 px) that
  names the rooms that are on ("Office, Bedroom").
- **Rooms** is the existing panel Music layout (now-playing bar plus room
  columns). Tapping the bar's art or title goes back to Now playing. After 30 s
  on Rooms without a touch, the panel goes back to Now playing on its own.
- **Paused**: the same screen with "Paused on <phone name>" (amber, 20 px), the
  art dimmed, and Play in place of Pause. Play sends the existing play command.
- **Resume any time while the phone stays connected**: a paused Bluetooth track
  stays on the hub's now-playing (status paused) for as long as that phone's
  Bluetooth media player is present, instead of clearing after the 600 s hold.
  When the phone disconnects, the track clears as today.
- **AirPlay**: Now playing shows the AirPlay track's art and title with "Control
  from the phone" in place of the buttons (no AirPlay remote control in this
  project).
- **Nothing playing**: the idle line on the panel ends after "connect over
  Bluetooth" (no "then press play" or "then play something"); phones keep their
  copy.
- **The screensaver follows the music**: while a track is playing, or was paused
  less than 5 minutes ago, the idle screen is the ambient Now Playing view (cover
  340 px, title 40 px, artist 28 px, a thin progress line, a background tinted
  by the cover's hues, no controls). Otherwise it is the Aurora, as today. This
  holds at any time of day. The first touch on either only dismisses it and lands
  on Now playing (Music tab) when there is a track.
- **No night dim while the ambient Now Playing shows**: in quiet hours the
  backlight stays at full while a track is playing or was paused less than 5
  minutes ago, and dims as today once the Aurora takes over. Owner reason: music
  playing means nobody is watching TV downstairs, and music in bed means nobody
  is downstairs.

## Non-goals

- No change to phones' Music screen beyond what the hub's resume change implies
  (a paused Bluetooth track stays listed while the phone is connected).
- No AirPlay play, pause or skip.
- No change to the frames' now-playing rules beyond what the hub change implies;
  the builder checks that a long-paused track does not keep a frame on a paused
  now-playing screen it would previously have left (see Left to the builder).

## Anti-goals

- A bright panel at night when no music has played for a while.
- A Play button that does nothing (shown when the phone's player is gone).

## Approaches

The owner chose the screens and rules by eye from the mockup on 2026-11-23. For
resume, considered: (a) keep a paused Bluetooth track on the hub while the
phone's player is present (picked: the panel keeps the title and art, and the
existing play command works); (b) a page-only "Resume" button whenever a phone
is connected, with no title (rejected: no art or title, and it can show for a
phone that has nothing to resume).

## Left to the builder

- Where the 5 minutes since pause is measured (the hub's now-playing has the
  status and its update time; a paused-since stamp may need adding) and how the
  backlight service learns it (it runs on the panel; it can read the hub's
  `now-playing.json` the way the page does, using the same configured hub URL).
- How the hub detects "the phone's Bluetooth media player is present" (the AVRCP
  side already tracks the BlueZ player) and how the hold rule changes in
  `nowplaying.py`, with tests for: paused stays while present, clears on
  disconnect, the 600 s hold still applies to sources without a player.
- Whether the frames' now-playing screen should still stop showing a paused track
  after its old hold (keep the frame's behavior unchanged if the hub change would
  alter it; say how).
- The screensaver mode switch in `screensaver.js` and `screensaver-model.js`
  (Aurora versus ambient Now Playing), keeping the existing wake-only first tap.
- Tests: model tests for the Music tab's screen choice, the 30 s return, the
  5-minute rule, the paused and AirPlay views; hub tests for the hold; backlight
  tests for the no-dim rule. Panel sizes per the panel layout design.

## Calls made after the builder check

Level 1's calls after a builder read the draft; they refine the sections above.

1. **The phone name** in "Paused on <phone>" comes from the page's existing
   Bluetooth devices list (the connected device the panel already shows on its
   Bluetooth screen). No new field in `now-playing.json`. With no connected
   device name, the line reads "Paused".
2. **A hub restart forgets a paused track.** The nightly reboot or a service
   restart clears it, as today; resuming then needs the phone. Recorded as a
   known limit, no persistence.
3. **The backlight service** gets the hub origin from the same `panel_url` the
   kiosk uses (through the unit's environment), reads the hub's
   `now-playing.json` every 10 s, and on a failed read keeps its last answer for
   up to 2 minutes, then behaves as if nothing is playing (normal dimming). It
   stays stdlib-only.
4. **"Paused less than 5 minutes ago" is measured on each device's own clock**
   from the moment it first sees the track paused (the page and the backlight
   service each keep that moment), never by comparing a hub timestamp with a Pi
   clock. A kiosk restart during a pause restarts that count (at most 5 extra
   minutes of the ambient view).
5. **Held tracks without a player** (a paused AirPlay track, or a Bluetooth track
   whose player went away, during the existing 600 s hold) still show on Now
   playing and count for the 5-minute ambient rule, but never show Play: AirPlay
   shows "Control from the phone", a Bluetooth track with no player shows no
   transport.
