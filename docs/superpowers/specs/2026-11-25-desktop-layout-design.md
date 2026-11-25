# Desktop layout for the house page

Date: 2026-11-25. Status: approved direction (mockup C with the mixer room controls); this
document records it for planning.

## Why

The owner wants the house page left open on one of the Mac mini's monitors. Today the page is a
560px phone column (`--page: 560px` in `style.css`) centred on a wide screen, with the six-tab bar
stretched across the bottom edge. The owner described the use as "glance and control": something
that sits open all day, shows what is going on in the house at a look, and lets them change it.

## Goals

- At 1100px wide and over, the page becomes a desktop dashboard. Under 1100px nothing changes.
- The Listen screen at desktop width (called Home below) shows, without switching screens: now
  playing, the room volumes, who is home, the thermostat, the garage door and play history.
- Music is the visual focus. Status is quiet unless something needs attention.
- Home fits one viewport with no page scroll, and its columns end on the same bottom edge.
- Every existing control keeps working, and so does every element id (rooms.js finds them by id).

## Non-goals

- Starring a song in Apple Music. That is a separate roadmap item (Apple Music API, MusicKit key,
  user token). Leave room in the now-playing card for a button; do not show a placeholder.
- The wall panel layout (`?panel=1`, `panel.css`, `panel*.js`). It must look and behave exactly as
  before.
- New hub endpoints or data. The desktop layout uses what the page already fetches.
- A screensaver or idle mode on the desktop.

## Anti-goals

- Any change to the phone layout under 1100px.
- A Home screen where the song or the rooms sit below the fold.
- Text that cannot be read from desk distance, or status that shouts when nothing is wrong.

## Approaches considered

- **A, dashboard in equal cards.** A side rail. Now playing and rooms in a main column, people,
  thermostat and garage as cards in a middle column, history on the right. The owner liked the
  content, but the five equal cards gave it no focus, "68°" and "CLOSED" were louder than the song,
  and the ragged column heights left empty patches.
- **B, wider tabbed page.** The same rail, one screen at a time, two columns where a screen has
  room. Smaller, but climate and the garage need a click to see, which misses the glance use.
- **C, music as the hero with a status strip (chosen).** A refinement of A. The owner picked C,
  then picked the "mixer" room controls (vertical faders) over horizontal tiles.

The mockups live outside the repo (they carry album art): `../mockups/desktop-layout/` beside this
checkout, with `desktop.css`, `desktop.js`, a patched `volume-slider.js` and screenshots
(`desk-c-mixer.png` is the approved look). They are throwaway CSS and DOM moving. Use them for
the look, not as code to copy.

## Design

### Shell (every screen at desktop width)

- **Side rail, about 200px, left:** the "House" title, then "Everything off" directly under it,
  then the six destinations as a vertical list, using the tab bar's existing icons and labels. The
  rail replaces the bottom tab bar at desktop width. The existing visibility rules still apply
  (Frame, Climate and Garage show only when available), and so does `aria-current`.
- **"Everything off" shows on every screen** at desktop width. Today rooms.js shows it only on
  Listen. The phone keeps today's rule.
- **Album-tinted background** as today.

### Home (Listen at desktop width)

The top of the main area holds the status strip. Below it, a left zone (about 60%) and a right
zone (about 40%) fill the rest of the viewport height.

**Status strip.** Compact chips, left to right:
- Who is home: a dot and the names.
- Thermostat: "68°, cooling to 74°".
- Garage: "Closed for 12 h 3 min".

Normal values are calm (about 15 to 16px). A chip that needs attention uses the existing tokens:
`--yellow` when the garage is open, `--red` when the garage reports an obstruction or lockout, or
when nobody's presence can be read. Clicking a chip opens its full screen (`#climate`, `#garage`).
A chip is absent when its module is unavailable (the same signals that hide the tabs). The garage
Open/Close button stays on the Garage screen, not on the strip.

**Now playing (left zone, top).** The card shrinks to fit its contents. It holds:
- Square album art, about 420px at a 1200px-tall viewport, scaling with the viewport height.
- Beside the art, a text column with the title (about 40px, clamped to two lines) and
  "artist · album".
- A progress bar spanning the full width of the text column, with the elapsed and total time
  under its left end and "ends 10:47 AM" under its right end.
- The transport controls, left-aligned under the text.

**Rooms (left zone, below now playing, takes the remaining height).** This is a mixer: one
vertical fader per room, side by side, filling the height. Each room shows its name, then a large
percentage (tabular numbers, reads "Off" when the room is off), then the fader, then the on/off
toggle and "Only here" below it. A room that is off dims. The fader is a real vertical slider:
pointer position on the Y axis, Up/Down and Page keys, Home/End, `aria-orientation="vertical"`.
The mockup rotated a horizontal slider with CSS and patched it to read Y. Do not ship that hack;
add vertical support to `volume-slider.js` properly. Phones keep the horizontal sliders.

**Play history (right zone).** Full height to the bottom edge, scrolling inside its own card.

**Fit.** At 1100px wide and at least 760px tall, Home fills the viewport with no page scroll, and
the left and right zones end on the same bottom edge. Below 760px tall, the page may scroll rather
than crush the faders (builder's call on the exact threshold).

### Other screens at desktop width

Frame, Gallery, Climate, Garage and Settings open one at a time from the rail. Content can be up
to about 1200px wide, with two columns where a screen has room, as mockup B showed: Climate
(people and thermostat left, automatic settings right), Settings (pairing left, devices right) and
Garage (door left, settings and recent activity right). Frame and Gallery: the builder chooses,
keeping the gallery grid filling the width.

### Data freshness

rooms.js already polls climate every 15 s and the garage every 5 s while their tab shows, and both
once a minute otherwise. On desktop Home both are visible, so they should poll at the "shown" rate
while Home is on screen at desktop width.

### Detection

Desktop mode is a CSS media query (`min-width: 1100px`) plus a JS `matchMedia` listener for the
parts that need script: the shown-rate polling, the "Everything off" rule and the fader
orientation. Resizing the window across 1100px switches cleanly without a reload. `?panel=1`
always wins and never enters desktop mode.

### States and edge cases (orchestrator's calls, settled after the builder check)

- **Banners and toasts float.** The health banner and the speaker banner sit over the top of the
  main area (as in the mockup), and the Undo toast floats at the bottom centre of the main area.
  Neither changes the Home grid's height. The speaker banner still disables the room controls.
- **The now-playing card keeps one height in every state**, set by the art box, so the rooms zone
  never jumps. Nothing playing: an empty art tile with the music icon, and the existing how-to text
  (with the Bluetooth name) in the text column. Paused: art dimmed as on the panel, Play in place of
  Pause. AirPlay: "Control from the phone" in place of the transport. A Bluetooth track with no
  player: no transport. A missing album shows the artist alone, and a missing "ends" time shows
  nothing on the right.
- **Empty play history** keeps its card and the 60/40 split, with one quiet line: "Nothing played
  yet."
- **Thermostat chip** is built from `thermostatView` (temperature, mode, setpoint); no new climate
  fields. Unreachable reads "Can't reach the thermostat" and checking reads "Checking the
  thermostat", both calm. With `override` true the chip adds ", held".
- **Attention colors.** Yellow: the garage is open, opening or closing, or every person reads
  "Not sure yet". Red: the garage reports `obstruction` or `lockout` true (`parseGarageText`), or
  its link is down. Nothing else colors a chip.
- **The rail is the existing `<nav id="tabs">`**, restyled, so `nav-model.js`, `syncTabs` and
  `aria-current` are unchanged. "Everything off" stays its own node, placed in the rail column
  under the title. The 800x480 kiosk rules are scoped to `html[data-panel]` and never apply.
- **Fader steps** match the phone slider: integer 0 to 100, arrows step 1, Page Up/Down step 10,
  the same haptics and the same `volume-controller.js` rules.

## Constraints

- No framework or build step. The site is plain ES modules and CSS served by the hub's
  `room_picker` Ansible role. JS tests run with `node --test tests/js/*.test.js`.
- Keep pure logic (chip text and attention state, the fader value from a pointer position) in
  model modules with unit tests, following the existing `*-model.js` pattern.
- The repo is public: no album covers, names or addresses in commits or test fixtures.
- No em dashes in UI copy or comments. Icons come from the existing inline SVG set; no emoji.
- `style.css` already has a `@media (min-width: 720px) and (max-height: 540px)` block for phones
  held sideways. Desktop rules must not fight it: a window 1100px or wider but under the height
  threshold gets the desktop shell with a scrolling Home, never the sideways-phone layout.
- AGENTS.md gets a short "Desktop layout" section (breakpoint, what Home shows, the vertical
  fader, the polling rule), like the Panel layout section.

## Acceptance (screen checks, recorded with screenshots)

- 1920x1200 and 2560x1440: Home matches the approved mockup's arrangement with no page scroll.
- 1280x800: Home still fits, or scrolls cleanly if under the height threshold.
- 390x844 phone: identical to today.
- `?panel=1` on the panel: unchanged.
- Faders work by drag, click and keyboard; volumes round-trip with the phone page.
- The chip attention states have been checked by forcing each state in a test or fixture.

## Left to the builder

- How the work splits into PRs (a shell PR then a Home PR is one natural split).
- Exact spacing, breakpoints within desktop widths, and Frame and Gallery arrangements.
- Whether the desktop arrangement moves existing nodes with CSS grid alone or relocates them with a
  small amount of JS, as long as ids and listeners survive.
