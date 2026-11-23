# Panel layout Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the 800x480 wall panel its own per-screen layout (Music, Climate, Garage, More) from the same room picker page, with phones unchanged.

**Architecture:** All panel decisions are pure functions in new model modules (`panel-model.js`, `panel-garage-model.js`, `panel-gallery-model.js`), tested with `node --test`. A new `panel.js` builds the panel-only DOM and is fetched by dynamic `import()` from `rooms.js` only when `?panel=1` is present (the same gate the screensaver uses), so phones never get a panel attribute or panel node. Panel CSS lives in a separate `panel.css`, loaded by `panel.js` the same way, and is scoped under `html[data-panel]`. `rooms.js` gets a small hook surface (state, controllers, send helpers) that `panel.js` consumes, so existing controllers are reused.

**Tech Stack:** Plain ES modules, `node --test`, pytest for role/asset checks, CSS.

**Spec:** `docs/superpowers/specs/2026-11-23-panel-layout-design.md` (mockup beside it).

## Global Constraints

- Panel mode is `?panel=1` in the page URL; styling keys off `html[data-panel]`, never media queries alone.
- 800x480 CSS px, DSF 1. Text never under 20 px on the panel (rail labels 17 px are the one exception). Labels 24 to 28 px, glance numbers 64 to 140 px.
- Primary targets at least 72 px on the short side, at least 10 px between targets; volume columns at least 60 px wide.
- Rail 96 px wide, four items (Music, Climate, Garage, More); strip 48 px tall; now-playing bar 112 px (cover 86 px, transport buttons 84 px square).
- No screen scrolls. No hover, long-press or swipe-only actions.
- No em dashes anywhere. No identifying data. UI copy never says snapserver, pairing service, hub, homebody, Matter, ESPHome, board, ntfy or API.
- Phones unchanged: the `@media (min-width: 720px) and (max-height: 540px)` block in `style.css` stays as is. Panel-only nodes are created only when `panel=1`.
- The screensaver overlay keeps working unchanged.
- Garage on the panel is always two taps (arm 4 s, then send); the second tap posts `garage/intent` and, if `confirm_required`, sends `garage/command` straight away. Phones keep the hub-decided confirm.
- Routes: `#listen` (Music), `#climate`, `#garage`, `#more`; More sub-screens keep `#frame`, `#frame/<id>`, `#gallery`, `#gallery/<id>`, `#settings` (Bluetooth) plus panel-only `#health`. Unknown or hidden hashes fall back to `#listen`.

## Review Focus

- Phone load (no `panel=1`): no `data-panel`, no panel node, `panel.js` and `panel.css` never fetched. Pinned in Task 1.
- Garage link down or state unknown: no fill, word UNKNOWN with "Last seen", button disabled, never "closed". Task 5.
- Garage position `null` and the door stuck "opening": fill follows the 15 s clock, snaps on landing. Task 5.
- A room not responding: column greyed, "Not responding", controls disabled, plus/minus never send. Task 3.
- A fourth room: columns stay equal, flagged when a column would drop under 60 px of fill. Task 3.
- Climate and Garage tabs hidden when unconfigured, and a `#garage` deep link falls back to `#listen`. Task 2.
- Tall art piece the frame cannot show: no show button, phone's sentence instead. Task 7.

---

### Task 1: Panel flag, lazy module, phone invariants

**Files:**
- Create: `roles/room_picker/files/site/panel-model.js`, `roles/room_picker/files/site/panel.js` (skeleton), `roles/room_picker/files/site/panel.css` (empty scope stub)
- Modify: `roles/room_picker/files/site/rooms.js` (near the screensaver import, ~line 2080)
- Test: `tests/js/panel-model.test.js`, `tests/js/rooms-panel-wiring.test.js`, `tests/js/page-imports.test.js` (add `panel.js`, `panel-model.js`)

**Interfaces:**
- Produces: `isPanel(search: string): boolean` (true only for `panel=1`); `panel.js` exports `initPanel(host)` where `host` is the rooms.js hook object (grown in later tasks), which sets `document.documentElement.dataset.panel = "1"`, injects `<link rel="stylesheet" href="panel.css">`, and builds panel nodes.

- [ ] **Step 1: Failing tests.** `isPanel("?panel=1&idle=60")` true; `isPanel("")`, `"?panel=0"`, `"?panel=true"` false. Wiring test (model on `rooms-screensaver-wiring.test.js`): `rooms.js` source contains `import("./panel.js")` only inside a `panel === "1"` guard, has no static import of `panel.js`, and `index.html` has no `data-panel`, no `panel.css` link and no `id="panel-` node.
- [ ] **Step 2:** Run `node --test tests/js/panel-model.test.js tests/js/rooms-panel-wiring.test.js`; expect FAIL (module missing).
- [ ] **Step 3:** Implement `isPanel`, the skeleton `initPanel`, and the guarded dynamic import in `rooms.js` next to the screensaver one; pass the host object (empty for now).
- [ ] **Step 4:** Same command; expect PASS. Run `node --test tests/js/page-imports.test.js`.
- [ ] **Step 5:** Commit `feat(panel): load the panel layout only when panel=1`.

### Task 2: Panel routes, rail items, strip back target

**Files:**
- Modify: `roles/room_picker/files/site/panel-model.js`
- Test: `tests/js/panel-model.test.js`

**Interfaces:**
- Produces: `PANEL_SCREENS = ["listen","climate","garage","more","frame","gallery","settings","health"]`; `resolvePanelScreen(hash: string, visible: {frame:boolean,climate:boolean,garage:boolean}) -> {screen, hash}` (`#more`, `#health` valid; `#art` aliases `#gallery`; `#frame/<id>` and `#gallery/<id>` kept; hidden or unknown (including the play-history viewer hash) gives `{screen:"listen",hash:"#listen"}`; `#frame` hidden gives `#listen`); `railItems(visible) -> [{screen,label}]` (Music, Climate and Garage only when visible, More always); `railActive(screen) -> "listen"|"climate"|"garage"|"more"` (frame, gallery, settings, health map to `more`); `isSubScreen(screen) -> boolean`; `backHash = "#more"`.

- [ ] **Step 1: Failing tests** for each function with the values above, including `railItems({climate:false,garage:false,frame:true})` equal to Music and More only, and `resolvePanelScreen("#garage", {garage:false})` equal to `#listen`.
- [ ] **Step 2:** Run `node --test tests/js/panel-model.test.js`; expect FAIL.
- [ ] **Step 3:** Implement, reusing `screenFromHash` from `rooms-model.js` where it fits.
- [ ] **Step 4:** Run; expect PASS.
- [ ] **Step 5:** Commit `feat(panel): panel routes and rail model`.

### Task 3: Rail, status strip, shell in the page

**Files:**
- Modify: `panel-model.js`, `panel.js`, `panel.css`, `rooms.js` (host hooks: `getState`, `onScreenChange`, `navVisible`)
- Test: `tests/js/panel-model.test.js`, `tests/js/panel-css.test.js` (new)

**Interfaces:**
- Produces: `stripParts({climate: doc|null, thermostat: thermostatView result|null, garage: garageView result|null, now: Date, locale?: string}) -> {temp: string|null, mode: string|null, garage: string|null, time: string}`. Strings: temp is whole degrees ("72"); mode "cooling to 74", "holding 74", or "Thermostat offline" (temp then null when unreachable); no reading hides temp and mode; garage "Garage closed" / "Garage open" / "Garage unknown" and null when the garage feature is off; time from `toLocaleTimeString(locale, {hour:"numeric",minute:"2-digit"})`. `stripBack(screen) -> boolean` (true on sub-screens, which hides the time).
- `panel.css` cannot be asserted visually, so `panel-css.test.js` parses `panel.css` and asserts: every rule is scoped under `html[data-panel]`; no `font-size` under 20px except the `.p-rail` label rule at 17px; rail width 96px; strip height 48px.

- [ ] **Step 1: Failing tests** for `stripParts` (cooling, idle "holding 74", unreachable, null reading, garage off, link down gives "Garage unknown") and the CSS test.
- [ ] **Step 2:** Run both test files; expect FAIL.
- [ ] **Step 3:** Implement `stripParts`; in `panel.js` build the rail (icons from the page's existing set), the strip and the content frame, hide the phone `#tabs`, `.topbar` and banners under `html[data-panel]`, and re-render on status, climate and garage updates through host hooks. Rail taps set `location.hash`. Writes `panel.css` shell rules.
- [ ] **Step 4:** Run; expect PASS.
- [ ] **Step 5:** Commit `feat(panel): rail and status strip`.

### Task 4: Music: now-playing bar and room columns

**Files:**
- Modify: `panel-model.js`, `panel.js`, `panel.css`, `volume-controller.js` (add vertical helper only), `rooms.js` (host: `sendControl`, `setRoomPower`, volume controller factory, playback commands)
- Test: `tests/js/panel-model.test.js`, `tests/js/volume-controller.test.js`

**Interfaces:**
- Produces: `valueFromPointerVertical(clientY, top, height) -> 0..100` (top is 100) in `volume-controller.js`; `stepVolume(current, dir: 1|-1) -> number` (steps of 5, clamped 0..100, snapped to the 5 grid); `nowPlayingBar(track, {btName, speakersDown, bluetoothControls}) -> {mode: "track"|"idle"|"banner", title, artist, howTo, showTransport, playing, progress}`; `roomColumn(room, rooms) -> {name, label: "54"|"Off"|"Not responding", fillPct, greyed, disabled, power}` (off room keeps its remembered volume for `fillPct`, greyed; unreachable room greys all and disables); `columnFit(roomCount, availableWidthPx = 704) -> {ok: boolean}` false when a column would be under 60 px of fill width (flag, never shrink text).
- Consumes: `roomState`, `howToText`, `playbackControls`, `playbackRequest`, `speakerBanner` (existing). Speaker banner replaces the bar at the same height and disables every room control.

- [ ] **Step 1: Failing tests.** Vertical pointer: top 100 gives 100, bottom 0; `stepVolume(52, 1)` is 55, `(98,1)` is 100, `(2,-1)` is 0, `(55,-1)` is 50. `roomColumn` for on, off, and unreachable rooms. `nowPlayingBar` with no track names the Bluetooth speaker when given, never shows transport; with a speaker banner returns mode `banner`. `columnFit(3)` ok, `columnFit(7)` not ok.
- [ ] **Step 2:** Run the two test files; expect FAIL.
- [ ] **Step 3:** Implement. In `panel.js` render the bar (cover, 1-line title and artist, 84 px previous/play/next using `playbackRequest`, thin progress line along the bottom edge) and one column per room: name, vertical fill using `createVolumeController` with the vertical pointer helper, plus and minus at 5 steps through the same controller, On/Off via `setRoomPower`. Omit Only here, Everything off and play history. Fill moves by `clip-path` per animation frame like the phone slider.
- [ ] **Step 4:** Run; expect PASS.
- [ ] **Step 5:** Commit `feat(panel): Music screen with room columns`.

### Task 5: Garage fill and the two-tap button

**Files:**
- Create: `roles/room_picker/files/site/panel-garage-model.js`
- Modify: `panel.js`, `panel.css`, `rooms.js` (host: `garageIntentAndSend(action, {confirmed})`, refactor of `onGarageTap` so a panel call skips the confirm card)
- Test: `tests/js/panel-garage-model.test.js`, `tests/js/page-imports.test.js`

**Interfaces:**
- Produces: `TRAVEL_MS = 15000`; `garageFill(doc, view, now, transitionStartMs) -> {fill: 0..1|null, text: string|null}`: live with numeric `position` gives `1 - position/100` (also while stopped); text "Opening, 40%" / "Closing, 40%" while moving with a position; no position: closed 1, open 0, opening or closing moving linearly over `TRAVEL_MS` from `transitionStartMs` (clamped), snapping to the final state when the state lands; link down or unknown gives `{fill:null,text:null}`. `armState(prev, event, nowMs) -> {armed: boolean, action: "open"|"close"|null, label: string|null, expiresMs: number|null, send: boolean}` with events `{type:"tap", action}`, `{type:"tick"}`: first tap arms for 4000 ms with label "Tap to open" or "Tap to close"; a second tap inside the window sets `send: true` and disarms; a tick after expiry disarms; disabled or unknown never arms.
- Consumes: `garageView` (`word`, `duration`, `lastSeen`, `disabled`), `parseGarageText`.

- [ ] **Step 1: Failing tests:** fills for closed, open, position 40 opening ("Opening, 40%", fill 0.6), position null opening at 7500 ms (0.5) and at 20000 ms (0 clamp, then lands), stopped with position, link down (null, UNKNOWN), unknown state; `armState` first tap, second tap send, tick at 4000 ms disarms, tap at 4001 ms re-arms rather than sends, disabled never arms, switching action (open armed, tap close) re-arms for close.
- [ ] **Step 2:** Run `node --test tests/js/panel-garage-model.test.js`; expect FAIL.
- [ ] **Step 3:** Implement model. In `panel.js` draw left card (88 px capitals word plus duration or "Last seen") and the right tall button (fill by `clip-path`, amber when armed). On `send: true` call the host so the intent response's `confirm_required` is followed by `garage/command` at once. Errors, single send and 2 s polling stay as today. Hide alert settings, remote switch, Recent.
- [ ] **Step 4:** Run; expect PASS.
- [ ] **Step 5:** Commit `feat(panel): Garage screen with fill and two-tap button`.

### Task 6: Climate screen

**Files:**
- Modify: `panel-model.js`, `panel.js`, `panel.css`, `rooms.js` (host: `climateQueue`/comfort save)
- Test: `tests/js/panel-model.test.js`

**Interfaces:**
- Produces: `climateCards(doc, thermostat, people, now) -> {inside: {temp, modeText, modeAccent, who}, comfort: {value, minusDisabled, plusDisabled}}`; `comfortStep(current, dir: 1|-1) -> number|null` clamped to `SETTING_LIMITS.comfort` and below the away value (null when refused). Reuses `thermostatView`, `peopleLines`, `SETTING_LIMITS`, `settingsBody`.

- [ ] **Step 1: Failing tests:** cooling card ("Cooling to 74"), unreachable ("Can't reach the thermostat"), checking state, who line from `peopleLines`, `comfortStep` at the limits and at away minus 1.
- [ ] **Step 2:** Run; expect FAIL.
- [ ] **Step 3:** Implement; `panel.js` draws "Inside now" (112 px temp) and "Comfort, when home" (64 px value, large minus and plus filling the card, "Away rules live on your phone"). Plus and minus write through the existing `autosave-queue.js` comfort path, `POST /api/homebody/settings` with `X-Snaphouse: 1`. Away, wait, switch and "Count this phone" are not built on the panel.
- [ ] **Step 4:** Run; expect PASS.
- [ ] **Step 5:** Commit `feat(panel): Climate screen`.

### Task 7: More: tiles, Frames, Gallery, Bluetooth, House health

**Files:**
- Create: `roles/room_picker/files/site/panel-gallery-model.js`
- Modify: `panel-model.js`, `panel.js`, `panel.css`, `rooms.js` (host: frame list, mirror, keep, art list, show, pairing and devices controllers, health data)
- Test: `tests/js/panel-gallery-model.test.js`, `tests/js/panel-model.test.js`

**Interfaces:**
- Produces: `moreTiles({frames, pictureCount, health}) -> [{id:"frames"|"gallery"|"bluetooth"|"health", title, line, warn}]` (lines: "2 frames", "143 pictures", "Pair a phone, devices", "All good" or "N need attention" with `warn` true; frames and gallery lines short, never wrapping); `galleryPage(items, page, perPage = 3) -> {items, label: "1 to 3 of 143", hasPrev, hasNext}`; `showButtons(piece, frames) -> [{frameId, label}] | {note}` (one "Show on <Name>" per frame when more than one, "Show on the frame" for one; a tall piece the frame cannot show returns the phone viewer's sentence from `gallery-viewer.js`/`rooms-model.js` instead of buttons).
- Consumes: `viewerState`, `classifyMachines`, `healthMessage`, `isReadOnlyPiece`, the existing frame, devices and pair controllers.

- [ ] **Step 1: Failing tests:** tile lines for 1 and 2 frames, health 0 and 2, `galleryPage` pages 1, last partial and empty list, `showButtons` for one frame, two frames, and a tall piece.
- [ ] **Step 2:** Run both files; expect FAIL.
- [ ] **Step 3:** Implement. `panel.js` draws the 2x2 tiles (44 px icon, 26 px title, 19 px line, wording as in the design; the 19 px line is a documented spec number, so `panel-css.test.js` allows `.p-tile-line` at 19px), then sub-screens: Frames (56 px tabs, mirror left, Back, Next picture, Keep this one using the default duration via `keepShowingBody`), Gallery (three per page, pager, large viewer reusing the existing `#gallery/<id>` hash and show action, no uploads, folders or trash), Bluetooth (96 px "Pair a phone", device rows with 72 px Connect/Disconnect/Forget through the existing controllers), House health (existing lists at panel sizes). "More" back button takes the strip's left edge.
- [ ] **Step 4:** Run; expect PASS.
- [ ] **Step 5:** Commit `feat(panel): More screen and its sub-screens`.

### Task 8: Docs and asset checks

**Files:**
- Modify: `AGENTS.md` (new "Panel layout" section; Garage tab section gets the one-line panel exception), `docs/cross-repo-features.md` only if the contract text mentions page behavior, `tests/python/test_room_picker_role.py` or the asset list test so `panel.js`, `panel.css` and the three model files are served (check `tests/js/site-assets.test.js`)
- Test: `tests/js/site-assets.test.js`, `tests/js/docs-contract.test.js`, `uv run pytest tests/python/test_room_picker_role.py`

- [ ] **Step 1:** Add failing asset expectations (the new files are shipped by the role's copy task and referenced from the page); run; expect FAIL.
- [ ] **Step 2:** Update the role's file list if it enumerates files; write the AGENTS.md section (routes, gate, modules, garage two-tap exception, fourth-room flag).
- [ ] **Step 3:** Run the three checks; expect PASS. Commit `docs(panel): document the panel layout`.

### Task 9: Full checks and renders

- [ ] Run `uv run pytest tests/python`, `node --test tests/js`, `ansible-playbook --syntax-check site.yml`, `ansible-lint`, `shellcheck` on shipped scripts (report any not installed).
- [ ] Render each panel screen at 800x480 (`?panel=1&idle=3600&nidle=3600`) and phone mode at 412x915 from a static server with JSON fixtures kept in the scratch dir; env -i HOME scratch for any browser install. Report PNG paths. Skip and say so if costly.
- [ ] Push, open PR against `main` (what changed, tests, what to check on the panel after merge), do not merge.

## Self-review notes

Spec coverage: frame (T3), Music (T4), Climate (T6), Garage and calls 4 and 5 (T5), More and sub-screens (T7), routes call 3 (T2), banners and progress call 7 (T4), strip states call 8 (T3), gallery call 9 (T7), clock call 10 (T3), flag and phone invariants call 2 (T1), landscape block call 1 (no edit, pinned by T1 CSS check that `style.css` is untouched in that block), docs (T8). Interpretation: panel CSS and DOM live in lazily loaded `panel.css` and `panel.js` rather than `style.css` and `index.html`, which makes "no panel node on phones" structural.
