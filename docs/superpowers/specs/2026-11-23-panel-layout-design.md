# Panel layout design

Date: 2026-11-23. Status: draft for owner approval.

## Intent

The control panel (host `panel`, a Raspberry Pi 5 with the official 7 inch
800x480 touchscreen, Chromium kiosk at device scale factor 1, about 133 ppi)
shows the same room picker page as a phone. The owner says everything is too
small on it. They want the panel treated as a wall device in its own right:
walk up, glance from a metre or two, tap one or two things at arm's length, walk
away.

What people do at the panel, in the owner's order: mostly music, then
temperature, then everything else.

Measured on the panel on 2026-11-23: text is 12 to 18 px (2.5 to 3.5 mm), buttons
48 to 56 px tall, the volume slider thumb about 30 px, six sidebar destinations,
and the Listen screen scrolls (play history). Wall and kiosk guidance (research
notes summarized under Constraints) puts arm's-length labels at 24 to 28 px,
glance numbers at 64 px and up, and primary targets at 78 to 104 px.

## Goals

- On the panel only, a layout per screen sized for the wall, built from the same
  page, data and controllers. Phones see no change.
- Music is the home screen. A now-playing bar on top; each room a tall column
  whose height sets its volume.
- The house state (inside temperature and what the thermostat is doing, the
  garage door) is visible from every screen without a tap.
- Four destinations: Music, Climate, Garage, More.
- No screen scrolls. No hover, long-press or swipe-only actions.

## Non-goals

- No change to the phone layout or to any screen's behavior on phones.
- No new hub, homebody or frame features. The garage door position the design
  uses already reaches the page (see Garage).
- The per-frame gallery (each frame with its own folders, a Gallery home showing
  what each frame shows now) is a separate project that follows this one. The
  panel's Gallery here stays browse and show.
- No redesign of the screensaver or the backlight schedule.

## Anti-goals

- A panel screen that needs scrolling or a second gesture to reach its main
  action.
- Small text "because it fits": nothing under 20 px on the panel.
- Phone-only setup tasks (thermostat away rules, garage alert settings, frame
  quiet hours, weather, folders, uploads, trash) crammed onto the wall.

## Constraints

- Panel mode is already signalled: the kiosk URL carries `panel=1` (with the
  screensaver timing parameters), built by
  `roles/panel/templates/snaphouse-kiosk.env.j2`. `rooms.js` already reads it to
  load the screensaver. Panel styling keys off that flag (for example a
  `data-panel` attribute on the root), not off media queries alone: research
  notes that `pointer: coarse` and `hover: none` misreport on some devices.
- 800x480 CSS px, DSF 1. At 133 ppi, 1 mm is about 5.2 px.
- Sizes on the panel, from the research and the approved mockup:
  - text never under 20 px; labels and names 24 to 28 px; glance numbers 64 to
    140 px;
  - primary targets at least 72 px on their short side (most 78 to 96 px), with
    at least 10 px between targets;
  - volume columns at least 60 px wide.
- Dark theme with at least 4.5:1 contrast for text. Keep the existing palette
  and the cover-derived hues the page already uses.
- Visible tap feedback on press (the page's existing `:active` and haptics
  pattern), no new latency.
- The screensaver overlay (`screensaver.js`) keeps working unchanged on top of
  the new layout.
- Repo rules in AGENTS.md: TDD, no em dashes, no identifying data. Pure layout
  decisions that can be tested (which screens and controls exist in panel mode,
  strip contents, the garage fill level) live in tested model modules, like the
  existing `*-model.js` files.

## Approaches

Considered three; the owner picked B on 2026-11-23.

- **A. Scale up what's there.** Raise the root font size and target sizes under
  the panel flag, same screens. Cheapest, but at the new sizes most screens
  overflow 800x480 and scroll.
- **B. A panel layout per screen, same page (picked).** Same data, controllers
  and screen routing; panel mode gets its own arrangement and sizes per screen,
  and phone-only parts are hidden on the panel. One app to maintain.
- **C. A separate panel page.** A new page that reuses the data modules. Cleanest
  split, but two interfaces to keep in step and every feature lands twice.

## The design, as decided

The approved mockup is committed beside this document as
`2026-11-23-panel-layout-mockup.html` (open it in a browser; the black box is the
panel at 800x480). Its placeholder names, art and numbers are not data.

### Frame of every screen

- A left rail, 96 px wide, with four items stacked full height: Music, Climate,
  Garage, More. Icon 32 px, label 17 px bold (the one exception to the 20 px floor,
  because the icon carries it). The current item is highlighted. Climate and
  Garage hide when that feature is not configured, as their tabs do today.
- A status strip across the top of the content, 48 px tall: inside temperature
  (30 px bold) with the thermostat's mode and target ("cooling to 74"), the garage
  door state ("Garage closed"), and the time on the right. Each part shows only
  when its feature is configured. On More's sub-screens a "More" back button takes
  the strip's left edge and the time hides.

### Music (home)

- Now-playing bar, 112 px tall: cover art 86 px, title (26 px bold) and artist
  (21 px) truncating on one line each, then previous, play/pause, next as 84 px
  square buttons (play/pause in the accent color). These use the existing
  playback commands.
- Nothing playing: the bar shows "Nothing playing" and one line on how to start
  from a phone (AirPlay or Bluetooth, naming the house the way the page does
  today). No transport buttons.
- Below, one column per room, equal widths, filling the rest of the screen. Each
  column: room name (24 px bold) at the top; a tall volume fill on the left that
  sets the volume to the height you tap (in steps of 5), showing the number
  (28 px bold) inside it; a plus and a minus button stacked on the right
  (72 px wide, filling the column height); and an On/Off button across the bottom
  (56 px tall, accent when on). An off room shows its fill greyed.
- Not on the panel: "Only here", "Everything off", and play history. They stay on
  phones.

### Climate

Variant A from the mockup: two cards side by side.

- Left: "Inside now", the inside temperature at 112 px, the mode and target
  ("Cooling to 74", mode in the accent color), and who is home on one line.
- Right: "Comfort, when home", the comfort setpoint at 64 px, and big minus and
  plus buttons filling the card, then "Away rules live on your phone". The
  buttons change the same comfort setting the phone's stepper changes today.
- Not on the panel: away temperature, the wait-before-away setting, the
  automatic-changes switch, "Count this phone as home".

### Garage

Variant A from the mockup, with the owner's fill idea:

- Left card: "Garage door", the state in 88 px capitals (CLOSED, OPEN, OPENING,
  CLOSING, STOPPED), and how long it has been that way.
- Right card: one tall button. Its fill shows how closed the door is: full when
  closed, empty when open, and moving between the two as the door travels,
  driven by the door position the page already receives (`position`, 0 closed to
  100 open, from homebody). While the door moves, show "Opening, 40%" style text.
  When the opener gives no position, the fill moves between full and empty over
  the door's usual travel time while the state is opening or closing, and snaps to
  the final state when it lands.
- The button opens or closes with a confirm: the first tap turns it amber and
  reads "Tap to open" (or "Tap to close") for 4 seconds; a second tap in that
  window sends the command through the existing intent and command flow. With
  no second tap, it goes back.
- Not on the panel: alert settings, remote control from away, the recent list.

### More

A grid of four large tiles (two by two), each with a 44 px icon, a 26 px title and
a 19 px line under it:

- Frames: "Next or back, keep a picture".
- Gallery: the picture count.
- Bluetooth: "Pair a phone, devices".
- House health: "All good" or "N need attention" (amber), from the page's
  existing health data.

### More: Frames

- Frame tabs (56 px tall, 22 px bold) when there is more than one frame, with the
  existing "Not responding" flag.
- The frame's mirror (what it shows now) on the left, filling the height.
- Three stacked buttons on the right: Back, Next picture (accent), Keep this one.
  "Keep this one" uses the existing keep command with its default duration.
- Not on the panel: art history, frame settings.

### More: Gallery (interim, until the per-frame gallery project)

- Three pictures per page, large, with their titles (20 px), and a pager: big
  previous and next buttons and "1 to 3 of 143". No scrolling.
- Tapping a picture opens it large with its title and artist and two buttons:
  "Show on the frame" and "Back to gallery". With more than one frame, the show
  button is one per frame ("Show on Nightstand", "Show on Office").
- Not on the panel: uploads, folders, trash, the folder filter.

### More: Bluetooth

- A full-width "Pair a phone" button (96 px tall), then the paired devices, each a
  row with the name (26 px bold), its state, and Connect or Disconnect and Forget
  buttons (72 px tall). Pairing runs the existing pairing flow at panel sizes.

### More: House health

- The existing "Needs attention" and "Not set up yet" lists at panel sizes.

## Left to the builder

- How panel mode is wired: a root attribute and CSS scoped under it, plus
  panel-only markup or render paths where the structure differs (the room columns,
  the More tiles). Reuse the existing controllers and models.
- How the Gallery pager and the per-frame show buttons map onto the existing
  gallery viewer code.
- Exact spacing within the stated minimums, icons from the page's existing set.
- More than three rooms: columns stay equal and keep at least 60 px of fill; if a
  fourth room would break that, the builder flags it rather than shrinking text.
- Tests: model tests for what panel mode shows (screens, strip parts, garage fill
  and labels, confirm timing); a check that phone mode is unchanged (no panel
  attribute, no panel-only nodes visible). A visual check on the real panel by
  screenshot after merge.
