# House page UX redesign

## Why

The Dusk redesign (PR #151) changed how the room-picker page looks. This one changes how it
works: its structure, navigation, flows and feedback. Three research passes informed it
(UX literature, multi-room audio and smart-home app patterns, and an audit of the current
page), followed by interviews with the owner and two rounds of clickable mockups.

Mockup the owner approved: https://claude.ai/artifact/XvvZqaPbzYmFL8f4ZADRUs (private to
the owner; its source is the reference for flow, not for exact styling, which stays Dusk).

## What the owner told us

- **Top task:** change the volume of the room you are in. Mostly one room at a time.
- **Rooms:** usually one room plays alone (living room or bedroom), not the whole house.
- **Biggest annoyance:** too many taps, specifically reaching a room's volume.
- **Users:** the owner (Android Chrome or Firefox) and their partner (iPhone Safari), doing
  the same things.
- **Panel:** the 800x480 panel will be a living-room remote: change volume, switch rooms.
- **Room order:** keep all rooms equal and in a fixed order (no "your room first").
- **One-room play:** yes to a one-tap "Only here" per room.
- **Climate:** plan its place in the navigation now; the page itself comes with homebody.
- **Images:** tapping an image must open it to view, never delete it. The "Art" area is
  renamed **Gallery**, because the frame can show any image, not only art.

## Goals

1. No daily action gets slower, and the common ones get faster (the Sonos 2024 lesson).
2. Every room's volume is reachable from the home screen with zero navigation and no
   scrolling for up to four rooms on a 360x740 phone and on the 800x480 panel.
3. One room alone is one tap ("Only here"); everything off is one tap.
4. Navigation is visible, not hidden in a drawer.
5. One way to save, one place for each setting, plain-language messages.
6. Every capability the page has today still exists after the change.

## Non-goals

- No change to the hub's APIs or the status file schema. Page work is in
  `roles/room_picker/files/site/` plus tests and docs. One small deploy-side addition
  (owner approved): the room_picker role publishes the Bluetooth receive name from
  `/etc/snaphouse/local.yml` (`bt_receive_alias`) into a page-readable JSON file at
  deploy time, so the nothing-playing how-to can name the speaker. The name is never
  committed to the repo; the page is LAN-only.
- No change to the Dusk visual language (tokens, panels, slider, motion rules).
- No music browsing or transport controls (the page has never had them).
- The Climate page itself is not built here; only its slot.

## Anti-goals (a failure even if it works)

- A restructure that removes or hides a daily verb, or adds a tap to volume or room on/off.
- Confirm dialogs for things that can be undone.
- System words in the UI ("snapserver", "service", host names) where a person-facing
  phrase exists.

## Design

### Navigation

- The menu drawer is removed. Phones get a **bottom tab bar**: Listen, Frame, Gallery,
  Climate, Settings. Each tab has an icon and a text label. The current tab is marked
  (`aria-current="page"`) and the URL hash keeps working (`#listen`, `#frame`, `#gallery`,
  `#climate`, `#settings`; the old `#art` redirects to `#gallery`).
- **Climate is hidden** until the homebody page exists (a single flag or the absence of
  its section hides the tab); with it hidden the bar has four tabs.
- **Frame is hidden** when the hub lists no frames (as today's frame features are), and
  the bar adapts.
- On the **800x480 panel** (the existing kiosk media query) the same tabs are a vertical
  rail on the left. Listen is always the home tab on load.
- The bar respects the bottom safe-area inset and stays fixed; content scrolls above it.

### Listen (home)

- Header: the title and an **Everything off** button (disabled when nothing is on).
- **Now playing strip**: compact (cover, title, source line, progress, Paused). It never
  pushes rooms off screen on a 360x740 phone. "Playing in ..." moves into the rooms
  themselves (each room shows its state).
- **Nothing playing**: the strip becomes a short how-to: on your phone, connect to the
  house over Bluetooth (by its Bluetooth name) or pick it in AirPlay, then choose rooms.
- **Rooms**: one row per room in a fixed order: name, state ("54%" when on, "Off" when off,
  "Not responding" when unreachable), an **Only here** chip, the on/off switch, and the
  slider always visible beneath. Only here is hidden on the room that is already the only
  one on.
- **Dragging an off room's slider turns that room on** (the mockup behavior).
- **Only here** turns that room on and every other room off in one action, then shows an
  undo toast ("Playing only in the living room." with Undo, which restores the previous
  on/off set).
- **Everything off** turns all rooms off and shows an undo toast that restores them.
- **Play history** stays below the rooms, unchanged in behavior.
- On the panel, Listen is two columns: now playing on the left, rooms on the right, all
  without scrolling.

### Frame

- The frame switcher (only with two or more frames), the mirror of what is showing, and
  Back / Next stay at the top.
- **Keep showing this** pins the current image until unpinned in one tap. A "For how
  long..." link next to it opens today's full pin choice (what to keep: this image, the
  weather or now playing; for 1 h, 4 h, until morning, until I unpin). Unpin stays.
  **(Controller's call: keeps every pin option while making the common one a single tap.)**
- **Frame history** rows open the image in the Gallery viewer instead of offering Delete.
- **Frame settings** live here, in one section per frame, and nowhere else: quiet hours,
  morning weather (times and weather city), what now-playing shows on the frame
  (Music, Podcasts, Audiobooks) and rotation (change every, folders in rotation). Quiet
  hours appears once per frame; the duplicate global/per-frame split is merged.

### Gallery (was Art)

- The library grid is open by default (no collapsed section), with the folder filter and
  pager as today.
- **Tapping an image opens a viewer**: the image large, its name and folder, position
  ("3 of 41"), swipe left/right or Previous/Next, and actions **Show on the frame**,
  **Keep it there** (pin), **Move to folder**, **Move to trash**. Back returns to the grid
  at the same scroll position. Delete is only in the viewer.
- Upload, folders (create, rename, delete) and Trash (restore, 30-day retention) stay,
  with folder management behind a "Manage folders" row instead of a collapsed block.

### Climate

Tab slot and hash reserved, hidden until homebody's page (who counts as home, pause until,
recent activity) is built in its own project.

### Settings

What is left after frame settings move: **Phones** (Pair a phone, paired phones with
Disconnect and Forget). It is short on purpose.

### Saving, undo and confirmation

- **Every setting saves itself**: switches and chips immediately, text and time fields
  after the person stops typing (about 600 ms) or on blur. A small "Saved" appears next to
  the setting for about 1.5 s; a failed save says so inline with a Retry. All "Save"
  buttons are removed.
- **Undo instead of confirm** for anything reversible: Only here, Everything off, move to
  trash, move to folder, unpin. The toast stays 8 s, has an Undo button, and is announced
  to screen readers (`role="status"`).
- **Confirm only the irreversible**: emptying the trash, forgetting a phone, deleting a
  folder that still has images.

### Status and messages

- Each room shows its own state. An unreachable room reads "Not responding", its controls
  are disabled, and it says so in words, not only by dimming.
- If the hub's audio server is unreachable, one banner at the top says "Can't reach the
  speakers right now. Retrying." and the controls are disabled until it returns.
- Messages are rewritten in plain language. Builder's job: find every user-facing string
  that names a system part (snapserver, pairing service, host names, "needs attention") and
  rewrite it as what the person sees and what to do. The pairing steps and the
  nothing-playing how-to name the house's real Bluetooth name, published as above.

### Accessibility and input

Tabs and chips are at least 44 px tall on touch. Everything keeps working by keyboard. The
undo toast does not steal focus. Reduced motion keeps its instant behavior.

## Task targets

| Task | Today | Target |
|---|---|---|
| Change one room's volume | 0 taps, may scroll | 0 taps, never scrolls (up to 4 rooms) |
| Play in one room only | 1 tap per other room | 1 tap (Only here) |
| Everything off | 1 tap per room | 1 tap |
| Pin what's on the frame | 4 taps | 2 taps (tab, Keep showing this) |
| View an image | not possible without options menu | 2 taps (tab, image) |
| Change quiet hours | drawer, Settings, scroll, Save | Frame tab, its switch or time, saves itself |
| Forget a phone | 5 taps | Settings tab, Forget, confirm (3) |

## Resolved after the builder check

- **Hashes:** `SCREENS` in `rooms-model.js` gains `gallery` and `climate`; `#art` (and
  `#art/...`) alias to `#gallery`. `#frame/<id>` keeps selecting a frame. Code that forces
  `#art` for the library scroll is re-pointed to `#gallery`.
- **Frame tab visibility** follows the same signals that show frame features today
  (`frame-control.json` / `frame.json`), not a new one. **(Controller's call.)**
- **Only here, Everything off and Undo** use the existing per-room on/off request
  (`toggleRequest`, plus the group-unmute path today's switch already uses). Undo replays
  the snapshot of every room's on/off; volumes are untouched. No hub change. The live
  group layout is checked during the build. Toasts last 8 s (today 7 s); a new toast
  replaces the previous one.
- **Dragging an off room** sends unmute, then the volume.
- **Move to trash in the viewer** keeps today's rule: if that image is showing on the
  frame now, the frame moves to the next image first. **(Controller's call.)**
- **Show on the frame** keeps today's orientation rule and the viewer says why when an
  image can't be shown.
- **Frame history** rows open the viewer; when opened from history the viewer also offers
  **Remove from history**, so today's history delete keeps a home.
- **Upload's target folder** lives in Gallery with Upload. **Weather city** lives in Frame
  settings.
- **The health line** (a machine needs attention) stays as a banner, reworded in plain
  language, alongside the speaker banner.
- **Pin is two taps only where the hub reports frame control**; without it the pin control
  is absent, as today.
- **Autosave risk:** settings are written today as whole objects with one Save. Autosave
  must not let two quick edits overwrite each other: writes per settings object are
  serialized (one in flight, latest wins), each field change merges into the latest known
  object, and the "Saved" mark appears only after the write succeeds.

## Left to the builder

- Exact component structure and file layout, following the existing modules
  (`*-controller.js`, `rooms-model.js`, `keyed-list.js`).
- How the viewer is built (a `dialog` or an in-tab view), as long as Back and the hash
  behave.
- How to split into tasks and PRs, keeping main deployable after each merge or using one
  branch with a single PR as the Dusk work did.

## Docs

`AGENTS.md` gets the navigation model (tabs, hashes, hidden tabs), the undo-versus-confirm
rule, and the autosave rule. Cross-repo: page-only; nightstand and homebody unaffected
(N/A), except that homebody's future Climate page will fill the reserved tab.
