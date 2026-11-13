# House Page UX Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restructure the room-picker page's navigation, flows and feedback (tabs, Only here, undo, Gallery viewer, autosave, plain language) without removing any capability.

**Architecture:** Pure logic goes into small new ES modules next to the existing ones (`nav-model.js`, `undo.js`, `autosave-queue.js`, `health-model.js`), each with node tests; `rooms.js` stays a thin DOM layer that wires them. One branch, one PR, and the page works after every task.

**Tech Stack:** Plain ES modules and CSS in `roles/room_picker/files/site/`, `node --test`, Ansible templates, pytest (`uv run pytest tests/python`).

**Spec:** `docs/superpowers/specs/2026-11-12-house-page-ux-design.md`

## Global Constraints

- Styling stays Dusk (tokens, panels, slider, motion rules); reduced motion keeps its instant behavior.
- No change to the hub's APIs or the status file schema. Page work is in `roles/room_picker/files/site/`, tests and docs, plus one deploy-side JSON publish.
- Hashes: `#listen`, `#frame` (and `#frame/<id>`), `#gallery`, `#climate`, `#settings`. `#art` and `#art/...` alias to `#gallery`. Listen is the home tab on load.
- Tabs: Listen, Frame, Gallery, Climate, Settings. Climate hidden until the homebody page exists. Frame hidden when the hub lists no frames, using the same signals as today (`frame-control.json` / `frame.json`).
- Current tab has `aria-current="page"`. Bar is fixed, respects the bottom safe-area inset. On the 800x480 kiosk media query the tabs are a vertical left rail.
- Tabs and chips at least 44 px tall on touch. Everything works by keyboard. The toast does not steal focus.
- Undo toast: 8 s (today 7 s), Undo button, `role="status"`, a new toast replaces the previous one.
- Undo for reversible actions (Only here, Everything off, move to trash, move to folder, unpin). Confirm only: empty the trash, forget a phone, delete a folder that still has images.
- Autosave: switches and chips immediately; text and time fields after about 600 ms pause or on blur; "Saved" for about 1.5 s after the write succeeds; failure shows inline with Retry. No "Save" buttons remain. Writes per settings object are serialized (one in flight, latest wins) and each field change merges into the latest known object.
- Only here, Everything off and Undo use `toggleRequest` plus today's group-unmute path; Undo replays the per-room on/off snapshot and never touches volumes. Dragging an off room sends unmute, then the volume.
- Every room shows its own state: "54%", "Off", "Not responding" (controls disabled, said in words). Speaker banner text: "Can't reach the speakers right now. Retrying." and controls disabled until it returns.
- Health banner: only a machine that was healthy and then stopped reporting, or reports a failure, raises it; a never-reported machine is listed in Settings as "Not set up yet".
- Plain language: no snapserver, "pairing service", host names or "needs attention" in UI copy.
- No em dashes anywhere. No identifying data (IPs, MACs, names, the Bluetooth name) committed; `bt_receive_alias` lives only in `/etc/snaphouse/local.yml`.
- 360x740 phone and 800x480 panel: up to four rooms with sliders visible, no scrolling.

## Review Focus

- A daily action got slower (Sonos lesson): volume or room on/off needing a tap or a scroll that main did not need; check taps on 360x740 and 800x480 with four rooms.
- Autosave clobbering: two quick edits to quiet hours and morning weather (or two frames) must not overwrite each other; "Saved" must not show before the write succeeds.
- Undo after state changed elsewhere: Undo for Only here after another phone changed a room, or Undo of trash after the image was restored; Undo must only replay what it recorded and fail quietly.
- Hidden-tab deep links: `#climate` always, and `#frame` / `#frame/<id>` with no frames, must land on Listen without a blank page or a stuck hash; `#art` lands on Gallery.
- The viewer on the 800x480 panel: image, actions and Back all fit and work by touch and keyboard; Back restores grid scroll; swipe does not fight page scroll.

---

### Task 1: Navigation model and tab bar

**Files:**
- Create: `roles/room_picker/files/site/nav-model.js`, `tests/js/nav-model.test.js`
- Modify: `roles/room_picker/files/site/rooms-model.js` (SCREENS at ~1026, `screenFromHash`), `roles/room_picker/files/site/index.html` (remove drawer and hamburger, add `<nav>`), `roles/room_picker/files/site/style.css` (tab bar, kiosk rail), `roles/room_picker/files/site/rooms.js` (drawer code out, screen switching in), `tests/js/rooms-model.test.js`, `tests/js/page-imports.test.js`, `AGENTS.md`

**Interfaces:**
- Consumes: `screenFromHash`, `SCREENS` from `rooms-model.js`.
- Produces: `SCREENS = ["listen","frame","gallery","climate","settings"]`; `resolveScreen(hash: string, visible: {frame: boolean, climate: boolean}): { screen: string, hash: string }` (aliases `art` to `gallery`; a hidden or unknown screen returns `{screen:"listen", hash:"#listen"}`; `#frame/<id>` kept when frame is visible); `visibleTabs(visible): string[]` in bar order; `TAB_LABELS` map.

- [ ] **Step 1: Write failing tests** in `tests/js/nav-model.test.js`: `resolveScreen("#art", v)` gives `gallery`; `"#art/x"` gives `gallery`; `"#climate"` with `climate:false` gives `listen`; `"#frame"` and `"#frame/office"` with `frame:false` give `listen`; `"#frame/office"` with `frame:true` keeps the hash; `""`, `"#bogus"` give `listen`; `visibleTabs({frame:false,climate:false})` equals `["listen","gallery","settings"]`; with frame true it is four tabs; with both true, five in order Listen, Frame, Gallery, Climate, Settings.
- [ ] **Step 2: Run** `node --test tests/js/nav-model.test.js`. Expected: FAIL (module missing).
- [ ] **Step 3: Implement `nav-model.js`** per the Interfaces; update `SCREENS` in `rooms-model.js` and keep `screenFromHash` working for existing tests (update its expectations for `art`/`gallery`).
- [ ] **Step 4: Run** `node --test tests/js/*.test.*`. Expected: PASS.
- [ ] **Step 5: Wire the DOM.** Replace the drawer with a `<nav aria-label="Pages">` of buttons (icon plus text label, min height 44 px, `aria-current="page"` on the current one), fixed bottom with `padding-bottom: env(safe-area-inset-bottom)`; content gets bottom padding so nothing hides under it. In the existing kiosk media query make the nav a left rail. `rooms.js` calls `resolveScreen` on load and on `hashchange`, rewrites the hash with `history.replaceState` when it was an alias or hidden, hides the Frame tab from the same signals frame features use today, and re-points the code that forces `#art` for the library scroll to `#gallery`. Rename the Art screen element ids/labels to Gallery in markup only (behavior changes in Task 5). Add `nav-model.js` to the `page-imports.test.js` file list if `rooms.js` imports from it.
- [ ] **Step 6: Verify in a browser** (serve `site/` with any static server and a stub `rooms.json`): load `/#art`, `/#climate`, `/#frame` with no frames; at 360x740 and 800x480 the tabs are visible, current tab marked, Back works between tabs.
- [ ] **Step 7: AGENTS.md** gets a "Page navigation" section: tabs, hashes, `#art` alias, hidden Frame and Climate, how to unhide Climate (one flag in `nav-model.js`).
- [ ] **Step 8: Commit** `feat(page): bottom tabs and left rail replace the drawer`.

### Task 2: Undo helper and toast system

**Files:**
- Create: `roles/room_picker/files/site/undo.js`, `tests/js/undo.test.js`
- Modify: `roles/room_picker/files/site/rooms.js` (`showToast` at ~2496), `index.html` (toast region), `style.css`, `tests/js/page-imports.test.js`

**Interfaces:**
- Produces: `TOAST_MS = 8000`; `snapshotRooms(rooms: {id:string, on:boolean, reachable:boolean}[]): Map<string,boolean>`; `undoPlan(snapshot: Map<string,boolean>, current: {id,on,reachable}[]): {id:string,on:boolean}[]` (only rooms that are reachable now and differ from the snapshot; rooms that vanished are dropped); `createToaster({ now, setTimer, clearTimer, render }): { show(text: string, onUndo?: () => void): void, dismiss(): void }` where a second `show` cancels the first timer and replaces it.

- [ ] **Step 1: Write failing tests**: `undoPlan` returns only changed, reachable rooms and never includes volumes; a room unreachable now is skipped; a room absent now is skipped; the toaster calls the timer with `8000`, a second `show` clears the first timer and renders only the second text, `dismiss` clears the timer, calling Undo dismisses the toast and runs the callback once.
- [ ] **Step 2: Run** `node --test tests/js/undo.test.js`. Expected: FAIL.
- [ ] **Step 3: Implement `undo.js`.**
- [ ] **Step 4: Run** it again. Expected: PASS.
- [ ] **Step 5: Route `showToast` through `createToaster`.** Toast element has `role="status"`, an Undo `<button>` (44 px), is placed above the tab bar and clear of the safe area, and never calls `focus()`. Existing callers (trash, folder move, history delete) keep working with the new 8 s.
- [ ] **Step 6: Commit** `feat(page): 8 s undo toast that replaces the previous one`.

### Task 3: Publish the Bluetooth name

**Files:**
- Create: `roles/room_picker/templates/bluetooth.json.j2`
- Modify: `roles/room_picker/tasks/main.yml`, `roles/room_picker/templates/room_picker.conf.j2` only if the file needs an explicit location, `local.example.yml` (comment only, placeholder), `tests/python/test_room_picker_role.py`, `AGENTS.md`

**Interfaces:**
- Produces: `/var/lib/snaphouse/room-picker/bluetooth.json` containing `{"name": "<bt_receive_alias or house_stream_name>"}`, mirroring the alias expression in `roles/bluetooth/tasks/main.yml:258`. Page-side consumers must treat a missing or malformed file as "no name".

- [ ] **Step 1: Write failing pytest** in `tests/python/test_room_picker_role.py`: the task list contains a template task writing `bluetooth.json`; rendering the template with `bt_receive_alias="Den Speaker"` gives that name, with `""` falls back to `house_stream_name`; the rendered output parses as JSON; no real alias string appears in any tracked file (use a placeholder in the test only).
- [ ] **Step 2: Run** `uv run pytest tests/python/test_room_picker_role.py -q`. Expected: FAIL.
- [ ] **Step 3: Add the template and the task** (mode 0644, `notify: Reload nginx` not needed for a static file but harmless; follow the `rooms.json.j2` task). Use `| to_json` so the name is escaped.
- [ ] **Step 4: Run** the pytest again, then `uv run pytest tests/python -q`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(room_picker): publish the Bluetooth name for the page how-to`.

### Task 4: Listen: rooms, Only here, Everything off, now playing

**Files:**
- Create: `roles/room_picker/files/site/listen-model.js`, `tests/js/listen-model.test.js`
- Modify: `rooms-model.js` (reuse `toggleRequest`, `volumeRequest`), `rooms.js` (room rendering, `keyed-list.js` use), `index.html`, `style.css`, `tests/js/page-imports.test.js`, `AGENTS.md`

**Interfaces:**
- Consumes: `snapshotRooms`, `undoPlan`, `createToaster` (Task 2); `toggleRequest(id, on)`, `volumeRequest(id, percent)`.
- Produces: `onlyHerePlan(rooms, id): {id,on}[]` (target on, every other reachable room off, nothing for rooms already right); `everythingOffPlan(rooms): {id,on}[]`; `roomState(room): {label: string, disabled: boolean, showOnlyHere: boolean}` where label is `"54%"`, `"Off"` or `"Not responding"` and `showOnlyHere` is false when the room is already the only one on or is unreachable; `howToText(btName: string|null): string`; `loadBluetoothName(fetchJson): Promise<string|null>`.

- [ ] **Step 1: Write failing tests**: `onlyHerePlan` on four rooms with two on; on a room already the only one on returns `[]`; unreachable rooms are skipped; `everythingOffPlan` skips rooms already off and is `[]` when none are on (button disabled); `roomState` for each of the three labels and the `showOnlyHere` rule; `howToText("X")` names X, `howToText(null)` falls back to wording without a name; `loadBluetoothName` returns null for a thrown fetch, a non-object, an empty or non-string name, and a name over 64 characters.
- [ ] **Step 2: Run** `node --test tests/js/listen-model.test.js`. Expected: FAIL.
- [ ] **Step 3: Implement `listen-model.js`.**
- [ ] **Step 4: Run** it again. Expected: PASS.
- [ ] **Step 5: Rebuild the room row** in `rooms.js`: name, state label from `roomState`, an "Only here" chip (44 px), the on/off switch, the slider always visible beneath; fixed order; unreachable rooms show "Not responding" with disabled controls. Dragging an off room's slider sends unmute then volume (the existing unmute path first, `volumeRequest` second) and the row flips to on.
- [ ] **Step 6: Wire Only here and Everything off.** Take `snapshotRooms` before sending the plan; send the requests through the existing per-room request path; toast "Playing only in the <room name>." or "Everything is off." with Undo calling `undoPlan(snapshot, currentRooms)`. Header gets the "Everything off" button, disabled when nothing is on. Verify against the live group layout during the build (spec: check, and note the finding in the PR).
- [ ] **Step 7: Compact now playing strip** (cover, title, source line, progress, Paused) that keeps four rooms on screen at 360x740; remove "Playing in ..." (rooms carry state). When nothing plays, the strip shows the how-to from `howToText`, fetching `bluetooth.json` once at load. On the kiosk query Listen is two columns, now playing left, rooms right, no scroll. Play history unchanged below the rooms.
- [ ] **Step 8: Verify in a browser** at 360x740 and 800x480 with four stub rooms: no scrolling, zero taps to reach any slider, Only here and Everything off each one tap, Undo restores.
- [ ] **Step 9: AGENTS.md** gets the Undo-versus-confirm rule and the Only here / Everything off semantics (volumes untouched on Undo).
- [ ] **Step 10: Commit** `feat(page): Only here, Everything off, always-visible sliders`.

### Task 5: Autosave queue and Frame settings consolidated

**Files:**
- Create: `roles/room_picker/files/site/autosave-queue.js`, `tests/js/autosave-queue.test.js`
- Modify: `rooms.js` (`toggleKind` ~1134, `saveFrameTimes` ~1152, `saveFrameQuietHours` ~1221, `saveWeatherCity` ~1275, rotation handlers), `rooms-model.js` (settings validation reused), `index.html`, `style.css`, `tests/js/frame-control.test.js`, `tests/js/page-imports.test.js`, `AGENTS.md`

**Interfaces:**
- Produces: `createAutosave({ write: (obj) => Promise<void>, delayMs = 600, savedMs = 1500, setTimer, clearTimer, onState }): { edit(patch: object): void, flush(): Promise<void>, retry(): void, seed(obj: object): void }`. `onState` receives `"idle" | "pending" | "saving" | "saved" | "failed"`. `seed` sets the latest known object (from a server read) without writing. One queue per settings object (per frame for per-frame settings).

- [ ] **Step 1: Write failing tests** (fake timers and a controllable `write`): two `edit` calls inside 600 ms produce one write with both patches merged; an edit during an in-flight write is not sent until it finishes, then the single next write carries the latest merged object (latest wins, never two in flight); `flush` writes immediately (blur); state goes `saved` only after the write resolves and returns to `idle` after 1500 ms; a rejected write gives `failed`, keeps the pending patch, and `retry` re-sends it merged with any newer edits; `seed` between edits does not drop unsent patches; chips (`edit` with `delayMs` override of 0) write immediately.
- [ ] **Step 2: Run** `node --test tests/js/autosave-queue.test.js`. Expected: FAIL.
- [ ] **Step 3: Implement `autosave-queue.js`.**
- [ ] **Step 4: Run** it again. Expected: PASS.
- [ ] **Step 5: Move frame settings into a per-frame section on the Frame tab** (quiet hours once per frame, merged from the global/per-frame split; morning weather times and weather city; what now-playing shows: Music, Podcasts, Audiobooks; rotation: change every, folders in rotation). Each field uses its frame's autosave queue; switches and chips immediate, text and time fields 600 ms or blur. Show "Saved" next to the setting for 1.5 s and an inline "Couldn't save. Retry" for `failed`. Delete every Save and Clear-city button; validation errors (24-hour times) stay inline and block the write. Remove the frame sections from Settings.
- [ ] **Step 6: Verify in a browser** with a stub server that delays writes: type quiet hours then change a weather time within the delay; the final stored object has both; kill the stub to see Retry.
- [ ] **Step 7: AGENTS.md** gets the autosave rule (one in flight per settings object, latest wins, merge into latest known).
- [ ] **Step 8: Commit** `feat(page): every setting saves itself, frame settings in one place`.

### Task 6: Gallery: grid, viewer, folders, upload

**Files:**
- Create: `roles/room_picker/files/site/gallery-viewer.js`, `tests/js/gallery-viewer.test.js`
- Modify: `rooms.js` (library, tile options, folders, trash, upload), `rooms-model.js`, `index.html`, `style.css`, `tests/js/art-manager.test.js`, `tests/js/page-imports.test.js`

**Interfaces:**
- Consumes: `createToaster` (Task 2); existing art-manager request builders in `rooms-model.js` (trash, restore, move, show, pin).
- Produces: `viewerState(items: Piece[], index: number, source: "grid"|"history"): { position: string, canPrev: boolean, canNext: boolean, actions: string[], showBlockedReason: string|null }` (position like `"3 of 41"`; actions are `["show","pin","move","trash"]` plus `"removeFromHistory"` when source is history; `showBlockedReason` explains a portrait piece that cannot be shown, using today's orientation rule); `stepIndex(index, count, dir: -1|1): number` (clamps, no wrap); `swipeDirection(dx: number, dy: number, thresholdPx = 48): -1|0|1` (0 when mostly vertical so page scroll is not stolen).

- [ ] **Step 1: Write failing tests**: `viewerState` position text, edge flags at first and last, `actions` for grid versus history, `showBlockedReason` set for a too-tall piece and null otherwise; `stepIndex` clamps; `swipeDirection(60,5)` is 1 (next), `(-60,5)` is -1, `(60,80)` is 0, `(10,0)` is 0.
- [ ] **Step 2: Run** `node --test tests/js/gallery-viewer.test.js`. Expected: FAIL.
- [ ] **Step 3: Implement `gallery-viewer.js`.**
- [ ] **Step 4: Run** it again. Expected: PASS.
- [ ] **Step 5: Gallery grid and viewer.** Rename Art to Gallery in all copy; the library grid is open by default (folder filter and pager as today); a tile tap opens the viewer (an in-tab view with its own hash entry such as `#gallery/<file>` so Back closes it, or a `dialog`, but Back must return to the grid at the same scroll position). Viewer: large image, name and folder, position, swipe plus Previous/Next, actions Show on the frame, Keep it there, Move to folder, Move to trash (a move or a trash gives an undo toast; if the image is showing on the frame now the frame advances first, as today). The tile "Options" menu and its Delete are removed. Images must fit on 800x480 with actions reachable without scrolling the page (actions in a bottom row or side column on the kiosk query).
- [ ] **Step 6: Folders and trash.** A "Manage folders" row replaces the collapsed block (create, rename, delete; delete confirms only when the folder still has images). Upload sits with its target-folder select in Gallery. Trash keeps restore and 30-day retention; emptying the trash confirms.
- [ ] **Step 7: Verify in a browser** at 360x740 and 800x480: tap an image opens it, Back returns to the same scroll, swipe and keyboard arrows step, Move to trash shows Undo.
- [ ] **Step 8: Commit** `feat(page): Gallery with an image viewer`.

### Task 7: Frame tab: keep showing, history, viewer link

**Files:**
- Modify: `rooms.js` (pin form ~Frame screen, art history), `rooms-model.js` (`pinSummary` ~1467, `framePinBody` ~1551, `frameUnpinBody` ~1561), `index.html`, `style.css`, `tests/js/frame-control.test.js`, `tests/js/frame-switcher.test.js`, `tests/js/gallery-viewer.test.js`, `AGENTS.md`

**Interfaces:**
- Consumes: `viewerState(items, index, "history")` (Task 6); `createToaster` (Task 2); `framePinBody`, `frameUnpinBody`.
- Produces: `keepShowingBody(frame: string): object` equal to `framePinBody(frame, "current", "until-unpinned")` semantics, i.e. pin the current image until unpinned; `pinControlState(frameControl: object|null, pin: object|null): {visible: boolean, pinned: boolean, label: string}` (not visible without frame control).

- [ ] **Step 1: Write failing tests**: `keepShowingBody("office")` matches the body today's form sends for "this image / until I unpin" (copy from the existing `framePinBody` test); `pinControlState(null, null).visible` is false; with control and a pin, `pinned` true and the label is the Unpin wording; the history viewer path offers `removeFromHistory`.
- [ ] **Step 2: Run** `node --test tests/js/frame-control.test.js tests/js/gallery-viewer.test.js`. Expected: FAIL.
- [ ] **Step 3: Implement** the two helpers in `rooms-model.js`.
- [ ] **Step 4: Run** the same command. Expected: PASS.
- [ ] **Step 5: Rebuild the Frame tab top**: switcher (two or more frames), mirror, Back / Next, then **Keep showing this** (one tap, becomes Unpin; unpin gives an undo toast) with a "For how long..." link that reveals today's full choice (this image, the weather or now playing; 1 h, 4 h, until morning, until I unpin). Frame history rows open the Gallery viewer (source `history`, with Remove from history, which uses today's history delete request and an undo toast if the old request supports it, otherwise no toast and no confirm since the image stays in the library). Frame settings section from Task 5 sits below.
- [ ] **Step 6: Verify in a browser** with `frame-control.json` present and absent: Keep showing is two taps from load (tab, button); absent, the control is gone.
- [ ] **Step 7: AGENTS.md** gets a line in the navigation section about Frame content and the viewer from history.
- [ ] **Step 8: Commit** `feat(page): one-tap Keep showing and history opens the viewer`.

### Task 8: Settings, health banner, plain-language sweep

**Files:**
- Create: `roles/room_picker/files/site/health-model.js`, `tests/js/health-model.test.js`
- Modify: `rooms-model.js` (`healthBanner` ~137 now delegates or is replaced), `rooms.js`, `pair-controller.js`, `devices-controller.js`, `index.html`, `tests/js/rooms-model.test.js`, `tests/js/pair-model.test.js`, `tests/js/devices-model.test.js`, `tests/js/page-imports.test.js`, `AGENTS.md`

**Interfaces:**
- Produces: `classifyMachines(entries: {host: string, status: object|null}[], seen: Set<string>): {troubled: string[], notSetUp: string[]}` where a host with `status` null that is not in `seen` is `notSetUp`; a host in `seen` that is now null, or has `verify !== "pass"`, or `commit !== main_commit`, is `troubled`; `healthMessage(troubled: string[]): string|null`; `speakerBanner(connection): string|null` returning exactly `"Can't reach the speakers right now. Retrying."` when the audio server is unreachable.

- [ ] **Step 1: Write failing tests**: never-reported null host goes to `notSetUp`, not `troubled`; same host after being in `seen` and then null goes to `troubled`; failing `verify` and mismatched commit are `troubled` even if never seen before this load; `healthMessage([])` is null; the message contains no `needs attention` and no host name tokens beyond a generic "A part of the house isn't responding" wording (host names are shown only in Settings); `speakerBanner` exact text; all existing `healthBanner` expectations are updated or removed accordingly.
- [ ] **Step 2: Run** `node --test tests/js/health-model.test.js`. Expected: FAIL.
- [ ] **Step 3: Implement `health-model.js`** and add `seen` bookkeeping in `rooms.js` (a session-level set of hosts that reported once; also treat a host that has ever reported a non-null status in this page session as seen).
- [ ] **Step 4: Run** `node --test tests/js/*.test.*`. Expected: PASS.
- [ ] **Step 5: Settings tab** holds Phones only (Pair a phone, paired phones with Disconnect and Forget; Forget keeps its confirm) plus a "Not set up yet" list of `notSetUp` machines. The speaker banner and the health banner sit at the top of every tab; controls are disabled while the speaker banner shows.
- [ ] **Step 6: Copy sweep.** Grep user-facing strings in `rooms.js`, `index.html`, `rooms-model.js`, `pair-controller.js`, `devices-controller.js` for `snapserver`, `pairing service`, `hub`, `Live updates`, `needs attention`, `Offline`, `Rotation`, `piece`, `Show in library`, `Delete`; rewrite each as what the person sees and what to do (for example "Change every" replaces "Rotation"; "Move to trash" replaces "Delete"). The pairing steps and the how-to name the Bluetooth name via `loadBluetoothName` (Task 4) with a generic fallback. Update tests that pin old strings. Collect the before and after list in the PR description.
- [ ] **Step 7: Verify in a browser:** stub a never-reporting host (no banner, listed under Not set up yet), a failing host (banner), and a down audio server (banner, disabled controls).
- [ ] **Step 8: AGENTS.md** final pass: confirm navigation, undo-versus-confirm and autosave sections are present and accurate; add the health rule.
- [ ] **Step 9: Run the whole suite** `node --test tests/js/*.test.*` and `uv run pytest tests/python -q`. Expected: PASS.
- [ ] **Step 10: Commit** `feat(page): Phones-only Settings, calmer health banner, plain-language copy`.

---

## Self-Review

- Spec coverage: navigation and hashes (T1); toast and undo (T2); Bluetooth publish (T3); Listen, Only here, Everything off, drag-turns-on, compact strip, how-to, panel layout (T4); autosave and frame settings, Saved/Retry (T5); Gallery, viewer, folders, upload, trash, confirm-only-irreversible (T6); Frame tab keep showing, For how long, history viewer, Remove from history (T7); Settings, health, speaker banner, copy sweep, AGENTS.md (T8, plus per-task notes). Climate slot is only the hidden tab and hash (T1).
- Type consistency: `createToaster`, `snapshotRooms`, `undoPlan` (T2) are consumed in T4, T6, T7 with the same signatures; `viewerState` (T6) is consumed in T7; `loadBluetoothName` (T4) is consumed in T8.
- Review Focus lines map to tests: slower daily action (T4 step 8), autosave clobbering (T5 tests), undo after state change (T2 `undoPlan` tests), hidden deep links (T1 tests), viewer on panel (T6 step 7).
