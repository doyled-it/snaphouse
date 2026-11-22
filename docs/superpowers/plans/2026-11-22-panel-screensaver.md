# Panel screensaver Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show a slow Aurora canvas over the room picker when the wall panel is idle, tinted by the now-playing cover hues, dismissed by a swallowed first tap.

**Architecture:** Panel-only behavior is switched on by a `?panel=1&...` query the panel role appends to `panel_url`. Three new ES modules (pure aurora math, canvas drawing with a 30 fps cap, pure idle/quiet-hours rules) plus one DOM controller, loaded by `rooms.js` through a dynamic `import()` only when the query is present, so phones load nothing. The backlight daemon gets a `--no-grab` switch driven by a new `grab_wake_tap` setting so one tap wakes and dismisses.

**Tech Stack:** Plain ES modules and `node --test` (no build step), Canvas 2D, Python stdlib daemon with pytest, Ansible and Jinja templates.

**Spec:** `docs/superpowers/specs/2026-11-22-panel-screensaver-design.md` (including "Calls made after the builder check"). Spike source for the renderer: `/Users/mdoyle/projects/smart-home/screensaver-spike/demo.html`, style `aurora` only (read, not edited, not copied wholesale).

## Global Constraints

- No em dashes anywhere (code, comments, docs, commit messages, UI text).
- No identifying data in the repo; `panel_url` stays in local.yml. `tests/python/test_hosts.py` keeps passing.
- `panel_backlight.dim_pct` stays `15`. Do not change it.
- Canvas about 200 px wide, aspect from the window, scaled up with CSS. One `requestAnimationFrame` loop capped at 30 fps; no rAF while the overlay is hidden or the page is hidden.
- Day idle 5 minutes, night idle 60 s (`panel_backlight.dim_timeout_s`), quiet hours `22:00` to `06:00` (`panel_backlight.quiet_start/quiet_end`). Overlay fade in about 2 s, fade out about 300 ms, hue crossfade about 3 s.
- Without `panel=1` the page creates no overlay, no canvas, no timers, and does not even fetch the screensaver modules.
- The overlay swallows every pointer, touch and key event while up, including the follow-up click. Idle resets on any input. Toasts, sheets, dialogs are untouched underneath.
- Quiet-hours rule is re-evaluated once a minute. At 22:00 a panel idle for 60 s or more shows the overlay at once. At 06:00 an overlay already up stays up.
- Clock is the browser's local time from `HH:MM` strings in the URL.
- The backlight evdev grab code from #184 stays; `grab_wake_tap` only turns it off.
- Kiosk URL: query goes before any `#`, merged with an existing query. A kiosk env change restarts the kiosk.
- CI rules: pytest, node-test, `ansible-playbook --syntax-check`, ansible-lint, shellcheck.

## Review Focus

- Timer-granularity edge: idle exactly at the limit and a clock that crosses 22:00 or 06:00 between ticks (Task 3 tests).
- A finger held down or a drag in progress when the idle limit passes: overlay must not appear (Task 4 test).
- Multi-touch or a second `pointerdown` during the 300 ms fade-out: must stay swallowed and must not re-show (Task 4 test).
- `panel_url` already containing a query and a fragment, or a trailing path: URL must stay valid (Task 6 tests).
- Chroma 0 (nothing playing) or a missing or malformed hue value: aurora must render a faint neutral tint, never `NaN` colors (Task 1 tests).

---

### Task 1: Aurora pure math

**Files:**
- Create: `roles/room_picker/files/site/aurora-math.js`
- Test: `tests/js/aurora-math.test.js`

**Interfaces:**
- Produces:
  - `RIBBONS`: frozen array of the spike's 3 ribbon definitions (`top`, `k`, `a`, `b`, `p`).
  - `oklchToRgb(L, C, hDeg): [r, g, b]` (0..255 ints, clamped, spike's `oklch`).
  - `shortHueDiff(a, b): number` (spike's `shortDiff`).
  - `smoothstep(x): number`.
  - `mixHues(from, to, f): {h1, h2, chroma}` where `f` is 0..1, hue taken the short way, chroma linear. Non-finite inputs fall back to `DEFAULT_HUES` values (h1 255, h2 285, chroma 0).
  - `tintColor(hues, L, k): [r, g, b]`: the spike's `tint` using chroma `0.17 * chromaScale(hues.chroma)` (imported from `cover-color.js`, so chroma 0 gives a faint tint, not grey).
  - `ribbonColumn(ribbon, x, t, H): {y, alpha}`: the spike's per-column vertical offset (`H * ...`) and alpha (`br * 0.85`, brightness floor 0.12).
  - `ribbonStops(color, alpha): [[pos, "rgba(...)"], ...]`: the four gradient stops (0, 0.55, 0.85, 1 with alphas 0, 0.22, 0.7, 0).

- [ ] **Step 1: Write failing tests** in `tests/js/aurora-math.test.js`: `oklchToRgb(0.68, 0, 255)` returns three equal channels; `shortHueDiff(350, 10) === 20`; `mixHues({h1:350,h2:0,chroma:0},{h1:10,h2:20,chroma:1}, 0.5)` gives `h1` about 0; `mixHues` with `NaN` hue returns finite numbers; `ribbonColumn(RIBBONS[0], 40, 12.5, 120)` is deterministic, `alpha >= 0.12 * 0.85` and `alpha <= 0.85`, and `y` stays within `[H*0.40 - H*0.14, H*0.40 + H*0.14]`; `tintColor({h1:255,h2:285,chroma:0}, 0.68, 0)` has channels that are all finite and not all zero; `ribbonStops` returns four stops with the pinned alphas.
- [ ] **Step 2: Run `node --test tests/js/aurora-math.test.js`.** Expected: FAIL, module not found.
- [ ] **Step 3: Implement the module** by porting the spike's `enc`, `oklch`, `shortDiff`, `smooth`, `tint` and the ribbon column formulas verbatim into the exported functions above. Pure, no DOM.
- [ ] **Step 4: Run the test.** Expected: PASS.
- [ ] **Step 5: Commit** `feat(panel): aurora math module`.

### Task 2: Aurora canvas renderer, 30 fps cap, hue crossfade

**Files:**
- Create: `roles/room_picker/files/site/aurora.js`
- Test: `tests/js/aurora.test.js`

**Interfaces:**
- Consumes: Task 1 exports.
- Produces: `createAurora({ canvas, getSize, raf, caf, nowMs, baseWidth = 200, maxFps = 30 }): { start(): void, stop(): void, setHues(h: {h1,h2,chroma}): void, running(): boolean, frame(nowMs): boolean }`.
  - `canvas.getContext("2d", { alpha: false })` is used; the canvas width is `baseWidth`, height `clamp(round(baseWidth * innerHeight / innerWidth), 60, 400)` from `getSize() -> {width, height}`.
  - `frame(now)` returns `false` and draws nothing when `now - lastDraw < 1000 / maxFps - 2`; otherwise advances time by `min(dt, 0.1)` s, updates the 3 s hue crossfade, clears to black, draws the 3 ribbons in `lighter` blend as 2 px columns (one `fillRect` per column, shared vertical gradient built with `ribbonStops`), applies the vignette last, returns `true`.
  - `start()` schedules one rAF; each frame re-schedules itself; `stop()` cancels it and `running()` is then false. `start()` twice schedules once.
  - `setHues(h)` begins a 3 s smoothstep crossfade from the current mixed hues.

- [ ] **Step 1: Write failing tests** with a recording fake 2D context, fake `raf`/`caf` and a manual clock: two `frame()` calls 16 ms apart draw once; a call 34 ms later draws again; `stop()` calls `caf` and no further `raf` is queued; `start()` after `stop()` resumes; `start()` twice queues one rAF; after `setHues` the colors drawn at +0 s, +1.5 s, +3 s move monotonically toward the target and at +3 s equal it; a 5 s gap between frames advances animation time by at most 0.1 s (no jump); canvas width is 200 and height 120 for an 800x480 `getSize`.
- [ ] **Step 2: Run `node --test tests/js/aurora.test.js`.** Expected: FAIL.
- [ ] **Step 3: Implement `createAurora`** in `aurora.js` (drawing only; all math from `aurora-math.js`).
- [ ] **Step 4: Run the test.** Expected: PASS.
- [ ] **Step 5: Commit** `feat(panel): aurora canvas renderer capped at 30 fps`.

### Task 3: Screensaver rules (pure)

**Files:**
- Create: `roles/room_picker/files/site/screensaver-model.js`
- Test: `tests/js/screensaver-model.test.js`

**Interfaces:**
- Produces:
  - `parsePanelParams(search: string): null | { dayIdleMs, nightIdleMs, quietStart, quietEnd }`. Query names: `panel` (must be `1`), `idle` (day idle seconds, default 300), `nidle` (night idle seconds, default 60), `qs` and `qe` (`HH:MM`, defaults `22:00` and `06:00`). Returns `null` when `panel` is not `1`. Bad numbers or `HH:MM` fall back to the default per field.
  - `inQuietHours(date: Date, params): boolean` (handles windows that wrap midnight; start inclusive, end exclusive; equal start and end means never).
  - `idleLimitMs(date, params): number`.
  - `shouldShow({ idleMs, date, params, inputHeld }): boolean`: true when `!inputHeld && idleMs >= idleLimitMs`.

- [ ] **Step 1: Write failing tests**: `parsePanelParams("")` and `"?panel=0"` are `null`; `"?panel=1"` gives the defaults (300000, 60000, "22:00", "06:00"); `"?panel=1&idle=abc&qs=99:99"` falls back per field; `inQuietHours` at 21:59, 22:00, 05:59, 06:00; `idleLimitMs` is 300000 at 12:00 and 60000 at 23:00; `shouldShow` is false at idle 299999 by day, true at 300000; at 22:00:00 with idle 90 s it is true at once (quiet edge); at 06:00 the limit goes back to 300000 so `shouldShow` is false for idle 90 s (the controller keeps an up overlay, see Task 4); `inputHeld: true` is always false.
- [ ] **Step 2: Run `node --test tests/js/screensaver-model.test.js`.** Expected: FAIL.
- [ ] **Step 3: Implement the module** (pure, `Date` passed in).
- [ ] **Step 4: Run the test.** Expected: PASS.
- [ ] **Step 5: Commit** `feat(panel): screensaver idle and quiet-hours rules`.

### Task 4: Overlay controller

**Files:**
- Create: `roles/room_picker/files/site/screensaver.js`
- Test: `tests/js/screensaver.test.js`

**Interfaces:**
- Consumes: `parsePanelParams`, `shouldShow` (Task 3); `createAurora` (Task 2).
- Produces: `createScreensaver({ doc, win, params, createAurora, now, setInterval, clearInterval, setTimeout }): { setHues(h): void, isUp(): boolean, destroy(): void }`, and `initScreensaver(search = location.search): Controller | null` (returns `null` when `parsePanelParams` is null; wires real globals).
- Behavior, all pinned by tests with a fake DOM (a small event-target stub with `createElement`, `append`, `classList`, `style`):
  - Creates one fixed full-screen `div.screensaver` (hidden, `aria-hidden="true"`) holding one `canvas`, appended to `body`. Fade is CSS opacity driven by class `is-up` (2 s in, 300 ms out); controller keeps timers for the fade end.
  - Idle clock: `pointerdown`, `pointermove`, `pointerup`, `keydown`, `touchstart` on `doc` in capture phase reset `lastInput`. `pointerdown` marks `inputHeld` until `pointerup` or `pointercancel`.
  - A once-a-minute `setInterval` check (plus one right after any input and one at start): when `!up && shouldShow(...)`, show: add `is-up`, `aurora.start()`.
  - While up: capture-phase listeners on `doc` for `pointerdown pointerup pointermove click touchstart touchend mousedown mouseup keydown keyup` call `preventDefault`, `stopPropagation`, `stopImmediatePropagation`. The first `pointerdown` also starts the dismiss: remove `is-up`, then after 300 ms `aurora.stop()` and mark hidden. A swallow window stays active until 400 ms after the last swallowed event, so the trailing `pointerup` and `click` never reach the page.
  - `visibilitychange` to hidden stops aurora; back to visible restarts it only if up.
  - Quiet-hours edge: once up, the overlay stays up regardless of later `shouldShow` results (06:00 case); only a tap dismisses.
  - `setHues(h)` forwards to `aurora.setHues`.
  - `destroy()` removes listeners, timers and the node.

- [ ] **Step 1: Write failing tests**: overlay is created hidden; no show before the limit, shows after 5 min by day and 60 s in quiet hours; an input at 4:59 pushes the show back; `inputHeld` blocks the show; first `pointerdown` while up is `preventDefault`ed and stopped, a `pointerup` and `click` 100 ms later are also stopped and a listener below it never fires; a second `pointerdown` during the 300 ms fade is still swallowed and does not re-show; after 400 ms quiet, a new `pointerdown` reaches a bottom listener and resets idle; aurora `start` is called once on show and `stop` once after dismissal and not on hidden-page when not up; page hidden while up stops aurora and visible restarts it; at 22:00 an already idle panel shows on the next minute tick; at 06:00 an up overlay stays up; `initScreensaver("")` returns `null` and touches no document API.
- [ ] **Step 2: Run `node --test tests/js/screensaver.test.js`.** Expected: FAIL.
- [ ] **Step 3: Implement `createScreensaver` and `initScreensaver`.**
- [ ] **Step 4: Run the test.** Expected: PASS.
- [ ] **Step 5: Commit** `feat(panel): screensaver overlay controller`.

### Task 5: Wire into the page (panel-only)

**Files:**
- Modify: `roles/room_picker/files/site/rooms.js` (near `followCover`, line ~2058, and startup)
- Modify: `roles/room_picker/files/site/style.css` (append `.screensaver` rules)
- Test: `tests/js/rooms-screensaver-wiring.test.js` (static check) and `tests/python/test_room_picker_role.py`

**Interfaces:**
- Consumes: `initScreensaver` (Task 4).

- [ ] **Step 1: Write failing tests**: a node test reads `rooms.js` and asserts it references `./screensaver.js` only inside a dynamic `import(` guarded by a check of `location.search` containing `panel=`, has no static `import ... from "./screensaver.js"`, and that `followCover` forwards hues to the screensaver (`screensaver?.setHues` appears in both the default-hues and the loaded-hues branches). A node test reads `style.css` and asserts a `.screensaver` rule with `position: fixed`, `inset: 0`, black background, a high `z-index` above `.toast` and the nav, `opacity: 0`, `pointer-events: none`, and an `.is-up` rule with `opacity: 1`, `pointer-events: auto`, and a `prefers-reduced-motion` block that shortens the fade.
- [ ] **Step 2: Run `node --test tests/js/rooms-screensaver-wiring.test.js`.** Expected: FAIL.
- [ ] **Step 3: Implement**: in `rooms.js`, `let screensaver = null; if (new URLSearchParams(location.search).get("panel") === "1") import("./screensaver.js").then((m) => { screensaver = m.initScreensaver(location.search); screensaver?.setHues(currentHues); }).catch(() => {});` where `currentHues` holds the last hues passed through `followCover`. Add the CSS (canvas `width:100%; height:100%; display:block`, `image-rendering: auto`).
- [ ] **Step 4: Run the wiring test, then `node --test tests/js`.** Expected: PASS.
- [ ] **Step 5: Commit** `feat(panel): load the screensaver on the panel only`.

### Task 6: Backlight `--no-grab` and `grab_wake_tap`

**Files:**
- Modify: `lib/snaphouse/backlight.py` (`_parse_args`, `run`)
- Modify: `vars/all.yml` (`panel_backlight.grab_wake_tap`, plus new `panel_screensaver` block)
- Modify: `roles/panel/templates/snaphouse-backlight.service.j2`
- Test: `tests/python/test_backlight.py`, `tests/python/test_panel_role.py`

**Interfaces:**
- Produces: CLI flag `--no-grab` on `snaphouse.backlight` (default off). `panel_backlight.grab_wake_tap: false` and `panel_screensaver: { enabled: true, day_idle_s: 300 }` in `vars/all.yml`. `dim_pct` untouched.
- With the flag set, `run` never calls `activity.maintain(dim=True)` (it calls `maintain(dim=False)` so any grab is released), the schedule and touch-wake still work.

- [ ] **Step 1: Write failing tests**: pytest that `_parse_args(["--no-grab"]).no_grab is True` and default is `False`; a `run()` test with a fake `_ActivityClock` asserts `maintain` receives `dim=False` on every pass when `--no-grab`, and `dim=True` when dim without the flag (existing behavior); a template test that the unit's `ExecStart` contains `--no-grab` when `panel_screensaver.enabled` is true and `grab_wake_tap` is false, and omits it when `panel_screensaver.enabled` is false; a test that `vars/all.yml` still has `dim_pct: 15`.
- [ ] **Step 2: Run `uv run pytest tests/python/test_backlight.py tests/python/test_panel_role.py -k "grab or no_grab or dim_pct"`.** Expected: FAIL.
- [ ] **Step 3: Implement.** The unit adds `--no-grab` when `panel_screensaver.enabled and not panel_backlight.grab_wake_tap`. Update the daemon docstring to say the grab is skipped with `--no-grab` because the page overlay swallows the first tap. The backlight digest task already restarts the service on a code change; a unit template change already notifies the restart handler.
- [ ] **Step 4: Run the focused tests, then `uv run pytest tests/python/test_backlight.py tests/python/test_panel_role.py`.** Expected: PASS.
- [ ] **Step 5: Commit** `feat(panel): backlight no-grab switch for the screensaver`.

### Task 7: Kiosk URL with the panel query

**Files:**
- Modify: `roles/panel/templates/snaphouse-kiosk.env.j2`
- Test: `tests/python/test_panel_role.py`

**Interfaces:**
- Produces: `PANEL_URL` renders as `<base>[?|&]panel=1&idle=<day_idle_s>&nidle=<dim_timeout_s>&qs=<quiet_start>&qe=<quiet_end>[#fragment]` when `panel_screensaver.enabled`, else the unchanged URL. Default base is still `http://house.local/`.

- [ ] **Step 1: Write failing tests** (the existing `_render` helper, extended to pass `panel_screensaver` and `panel_backlight` from `vars/all.yml`): plain URL gets `?panel=1&idle=300&nidle=60&qs=22:00&qe=06:00`; a URL with `?x=1` gets `&panel=1...` appended, query kept once; a URL with `#frag` or `?x=1#frag` has the query before the fragment and the fragment last; default URL (no `panel_url`) works; disabled screensaver leaves `PANEL_URL=` identical to today; the existing two env tests still pass. Also a test that the env template task keeps `notify: Restart the kiosk service`.
- [ ] **Step 2: Run `uv run pytest tests/python/test_panel_role.py -k kiosk_env`.** Expected: FAIL.
- [ ] **Step 3: Implement with Jinja** `split('#', 1)` for the fragment and `'?' in base` for the joiner.
- [ ] **Step 4: Run the focused tests, then `tests/container/run.sh` if docker is available (else say it was not run).** Expected: PASS.
- [ ] **Step 5: Commit** `feat(panel): pass screensaver timing to the kiosk page`.

### Task 8: Docs

**Files:**
- Modify: `AGENTS.md` (new "Panel screensaver" section: panel-only query, modules, swallow rule, `--no-grab`, `dim_pct` tuned separately), `docs/cross-repo-features.md` only if a seam changes (none expected; state so in the PR).
- Test: `tests/python/test_agents_md.py` if one exists, else none; run the full suites.

- [ ] **Step 1: Write the AGENTS.md section** (no em dashes; keep it short, link the spec).
- [ ] **Step 2: Run `uv run pytest tests/python` and `node --test tests/js`.** Expected: PASS.
- [ ] **Step 3: Commit** `docs(panel): document the screensaver`.

### Final checks (once)

`uv run pytest tests/python`, `node --test tests/js`, `ansible-playbook --syntax-check site.yml`, `ansible-lint`, `shellcheck` on shell files (report any not installed). Then push, open the PR against `main` with the body listing changes, test results, and the after-merge panel checklist from the spec ("Checks after it ships", plus the CPU pass mark: at most 20 percent on the Pi 3 and 10 percent on the Pi 5). Do not merge.

## Self-review notes

- Spec coverage: panel mode and URL (Tasks 3, 5, 7); overlay and swallow (4); Aurora port, 30 fps (1, 2); hues (2, 5); night grab switch (6); quiet-hour edges (3, 4); no change to `dim_pct` (6); CPU pass mark is a post-merge panel check.
- Interpretation to flag: "page default hues" have chroma 0, so the aurora tint uses the page's `chromaScale` (floor 0.15) and shows a faint blue-grey instead of pure grey.
