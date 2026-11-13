# Dusk Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restyle the room-picker page as the approved "Dusk" look (dark, cover-tinted, frosted panels) and fix the volume slider, press feedback and in-place updates.

**Architecture:** Pure logic goes into new ES modules next to `rooms-model.js` (volume send/hold state machine, cover hue extraction, haptics table, keyed list planner, visibility-aware timers), each with node tests. `rooms.js` stays the DOM-bound wiring and imports them. Color is two registered CSS custom properties (`--h`, `--h2`) plus a chroma scale on `:root`, set from JS when the cover changes; all other tokens derive from them in `style.css`.

**Tech Stack:** Plain ES modules, plain CSS (OKLCH, `@property`, `linear()` easing), node:test. No framework, bundler or build step.

**Spec:** `docs/superpowers/specs/2026-11-12-dusk-redesign-design.md`. Mockup source (Dusk column is the target; its CSS tokens and slider logic are the reference): `/private/tmp/claude-501/-Users-mdoyle-projects-smart-home/36cb50a3-6de3-4779-9a95-939a7528aa73/scratchpad/house-mockups.html`. If that path is gone, the values quoted in this plan are enough.

Paths below are relative to the worktree root. `SITE` means `roles/room_picker/files/site`.

## Global Constraints

- Page-only change inside `SITE/` plus `tests/js/` and `AGENTS.md`. No hub, API or status-file change.
- Dark only: delete both `prefers-color-scheme` blocks in `style.css`, set `color-scheme: dark` (today `light dark`), make the `theme-color` meta in `index.html` a single dark value.
- No external requests from the page. Fonts are self-hosted woff2 in `SITE/fonts/` with their OFL license. No CDN, no Google Fonts.
- Instrument Sans (variable woff2, latin and latin-ext) replaces Hanken Grotesk. Tabular numbers for volumes, times and counts.
- No framework, bundler or build step. Plain ES modules.
- No em dashes anywhere (code, comments, UI copy, docs, commits). No identifying data (IPs, MACs, names) in the repo.
- Existing pinned tests keep passing. `tests/js/page-imports.test.js` pins that every import in `rooms.js` is exported; every name `rooms.js` imports from a new module must be exported.
- Run JS tests with `node --test tests/js/*.test.*` (the directory form fails on local Node 26). Python tests `uv run pytest tests/python` are expected to be untouched.
- `prefers-reduced-motion: reduce` turns transitions, springs and hue easing off (instant changes).
- Touch targets at least 44 px on touch devices. Tap highlight off. `:focus-visible` shows a ring.
- Motion uses only `transform` and `opacity`. Durations under 300 ms, except the 1.4 s hue drift which never blocks input. No entrance animations on controls.
- Volume: slider sends at most one write per 100 ms with a trailing send of the final value, at most one request in flight per room; incoming status for a room is ignored while it is dragged; after release the user's value is held until the hub reports within 1 of it or 2 s pass.
- Slider element: `role="slider"`, `aria-valuenow`, arrows move 2, Page Up/Down move 10, `touch-action: none`, pointer capture, `pointercancel` is release. Thumb 26 px (grows to 1.2x scale while held). Fill and thumb move by `transform` at most once per `requestAnimationFrame`.
- Haptics (Android only, `navigator.vibrate` feature-detected, only from a user gesture, never from a status update): 5 ms when volume crosses a multiple of 10, 14 ms at 0 and 100, 10 ms room on, 6 ms room off, 8 ms play/pause.
- Color tokens (OKLCH, `h1`=`--h`, `h2`=`--h2`): ground `oklch(0.15 0.03 h1)`; corner glows `radial-gradient(120% 70% at 0% 0%, oklch(0.36 0.09 h1), transparent 60%)` and `radial-gradient(110% 70% at 100% 100%, oklch(0.30 0.09 h2), transparent 60%)`; panel `oklch(0.2 0.02 h1 / .45)`; accent `oklch(0.80 0.11 h1)`; accent-2 `oklch(0.70 0.10 h2)`; ink `oklch(0.93 0.01 h1)`; ink-2 `oklch(0.72 0.02 h1)`; ink-3 `oklch(0.55 0.02 h1)`. Default (no cover) is slate blue, `--h: 255`. Hue transition `1.4s cubic-bezier(.22,1,.36,1)`.
- Panel: radius 24 px, padding 16 px, `backdrop-filter: blur(24px) saturate(1.3)`, shadow `inset 0 1px 0 oklch(1 0 0 / .1), inset 0 0 0 1px oklch(1 0 0 / .06), 0 12px 30px oklch(0 0 0 / .25)`.
- Press: scale about 0.96 on `:active`, 120-140 ms `cubic-bezier(.22,1,.36,1)`. Switch thumb uses `--spring: linear(0, 0.009, 0.035 2.1%, 0.141 4.4%, 0.723 12.9%, 0.938 16.7%, 1.017 19.4%, 1.067, 1.09 24.4%, 1.093 26.1%, 1.08 28.2%, 1.006 34.6%, 0.99 37.4%, 0.983 40.3%, 0.999 50.6%, 1)` over .45s, with a plain cubic-bezier fallback declared first.
- Kiosk is the media query `(min-width: 720px) and (max-height: 540px)`: keep its arrangement, and inside it panels use solid translucent fills with no `backdrop-filter`.
- With no cover, or a greyscale one, tokens are calm and near-neutral, never a random hue.

## Review Focus

Failure modes the spec implies that no spec sentence tests. Each has a named test in the task that owns it.

1. A request that never settles or rejects (socket drop, HTTP failure) must not leave the volume controller "in flight" forever, or the slider would stop sending. Pinned in Task 2.
2. A hub that never reports the user's value (clamped, group-muted, rounded differently) must not freeze the slider display beyond 2 s. Pinned in Task 2.
3. Covers that are fully transparent, pure black, pure white or greyscale, and 1x1 or empty pixel buffers, must give default hues with zero chroma and never throw or produce NaN. A cover that 404s or cannot be read must fall back to the default hue quietly. Pinned in Task 4.
4. Resuming polling twice in a row (two `visibilitychange` events) must not double the timers; pausing before they started must not throw. Pinned in Task 6.
5. A list update where keys reorder, repeat or disappear (art grid after a delete, devices after a forget) must keep surviving nodes, never duplicate a node, and replace a row whose content signature changed. Pinned in Task 6.

---

## File structure

| File | Responsibility |
|---|---|
| `SITE/volume-controller.js` (new) | Pure send/hold state machine plus pointer and key math |
| `SITE/haptics.js` (new) | Pure vibration table over an injected `navigator` |
| `SITE/volume-slider.js` (new) | DOM slider element built on the two above |
| `SITE/cover-color.js` (new) | Pure pixels-to-hues, plus the small canvas and CSS applier |
| `SITE/keyed-list.js` (new) | Pure keyed-update planner plus a thin DOM applier |
| `SITE/page-timers.js` (new) | Interval registry that pauses while hidden |
| `SITE/fonts/` | Instrument Sans woff2 files, `OFL.txt`, `SOURCE.md` |
| `SITE/style.css`, `SITE/index.html`, `SITE/rooms.js` | Tokens, Dusk styling, wiring |
| `tests/js/*.test.js` | One test file per new module, plus `site-assets.test.js` |

---

### Task 1: Vendored font and dark-only token foundation

**Files:**
- Create: `SITE/fonts/instrument-sans-latin.woff2`, `SITE/fonts/instrument-sans-latin-ext.woff2`, `SITE/fonts/SOURCE.md`
- Modify: `SITE/fonts/OFL.txt` (replace with Instrument Sans's), `SITE/style.css:1-100` (font-face, tokens, remove dark block at 62), `SITE/index.html:8-9`
- Delete: `SITE/fonts/hanken-grotesk-*.woff2`
- Test: `tests/js/site-assets.test.js`

**Interfaces:**
- Produces: the CSS custom properties every later task uses: `--h`, `--h2`, `--k` (chroma scale, default 1), `--ground`, `--panel`, `--ink`, `--ink-2`, `--ink-3`, `--accent`, `--accent-2`, `--line`, `--ease-out`, `--spring`, `--dur-press` (130ms), and `--sans`. Old tokens (`--paper`, `--mat`, `--blue`, `--red`, ...) are kept as aliases onto the new ones so unconverted rules keep working until Task 5.

- [ ] **Step 1: Write the failing test** `tests/js/site-assets.test.js`, reading files as text (same style as `page-imports.test.js`):
  - `style.css and index.html make no external requests`: no match of `https?://` in `index.html` except inside `xmlns`/`<a href` already present (assert against the set of URLs present in `git show HEAD:` is overkill; instead assert no `<link` or `url(` with `http`), and `style.css` has no `url(http`.
  - `every url() font in style.css exists on disk` (resolve relative to SITE).
  - `style.css is dark only`: no `prefers-color-scheme`, contains `color-scheme: dark`.
  - `index.html has one dark theme-color meta`: exactly one `theme-color` meta, no `media=` attribute, content matches `#1[0-9a-f]{5}`.
  - `fonts directory carries OFL.txt, SOURCE.md and no Hanken files`.
- [ ] **Step 2: Run** `node --test tests/js/site-assets.test.js`. Expected: FAIL on all but the first.
- [ ] **Step 3: Vendor the font from upstream.** Work in a fresh empty directory under the scratchpad. Download the variable TTF and license from the official Google Fonts repository, path `ofl/instrumentsans/` (files `InstrumentSans[wdth,wght].ttf` and `OFL.txt`; URL-encode the brackets and comma), via `raw.githubusercontent.com/google/fonts/main/ofl/instrumentsans/`. Record the exact upstream commit with `gh api repos/google/fonts/commits/main --jq .sha`. Pin the width axis and keep weight 400-700 with `uv run --with fonttools --with brotli python -I -m fontTools.varLib.instancer <ttf> wdth=100 wght=400:700 -o <out.ttf>`, then subset twice with `pyftsubset --flavor=woff2 --layout-features='*'`: latin `--unicodes=U+0000-00FF,U+0131,U+0152-0153,U+02BB-02BC,U+02C6,U+02DA,U+02DC,U+2000-206F,U+20AC,U+2122,U+2191,U+2193,U+2212,U+2215,U+FEFF,U+FFFD` and latin-ext `--unicodes=U+0100-02BA,U+02BD-02C5,U+02C7-02CC,U+02CE-02D7,U+02DD-02FF,U+0304,U+0308,U+0329,U+1D00-1DBF,U+1E00-1E9F,U+1EF2-1EFF,U+2020,U+20A0-20AB,U+20AD-20C0,U+2113,U+2C60-2C7F,U+A720-A7FF`. Copy the two woff2 and upstream `OFL.txt` into `SITE/fonts/`, delete the Hanken files. Write `SOURCE.md` with: upstream repo and path, commit sha, retrieval date, the exact instancer and subset commands, and the line "Hanken Grotesk was removed in the Dusk redesign".
- [ ] **Step 4: Rewrite the top of `style.css`.** Replace the two `@font-face` rules with Instrument Sans (family `"Instrument Sans"`, `font-weight: 400 700`, `font-display: swap`, same unicode-range split as the old pair). Add `@property --h { syntax: "<number>"; inherits: true; initial-value: 255 }`, the same for `--h2` (initial 285) and `--k` (initial 1). `:root` gets `color-scheme: dark`, the Global Constraints tokens (chroma terms multiplied by `var(--k)`, e.g. `oklch(0.15 calc(0.03 * var(--k)) var(--h))`), `transition: --h 1.4s var(--ease-out), --h2 1.4s var(--ease-out), --k 1.4s var(--ease-out)`, and the old token aliases. Delete the `@media (prefers-color-scheme: dark)` block. `body` gets `background` as the two corner glows over `--ground`, `background-attachment: fixed`, `font-variant-numeric: tabular-nums` on number-bearing classes only (`.np-elapsed`, `.np-ends`, the future `.pct`, counts), and `-webkit-tap-highlight-color: transparent`. In `index.html` replace both `theme-color` metas with one `content="#1b1e2a"`.
- [ ] **Step 5: Run** `node --test tests/js/*.test.*`. Expected: all PASS. Then open the page from a local static server (`python3 -m http.server` in `SITE`) with the Playwright tools; confirm no console errors and no request outside localhost.
- [ ] **Step 6: Commit** `git add -A SITE tests/js/site-assets.test.js && git commit -m "feat(room-picker): Instrument Sans, dark-only tokens"`.

---

### Task 2: Volume controller state machine (pure)

**Files:**
- Create: `SITE/volume-controller.js`
- Test: `tests/js/volume-controller.test.js`

**Interfaces:**
- Produces, all named exports:
  - `createVolumeController({ send, now, setTimer, clearTimer, throttleMs = 100, holdMs = 2000, tolerance = 1 })` where `send(percent: number): void | Promise<void>`, `now(): number`, `setTimer(fn, ms): id`, `clearTimer(id): void`. Returns `{ begin(value), move(value), end(), incoming(hubValue): { show: boolean, value: number }, dragging(): boolean, held(): boolean, dispose() }`.
  - `valueFromPointer(clientX, left, width): number` (integer 0 to 100, clamped, 0 when width is 0).
  - `keyStep(key: string): number | null` (`ArrowRight`/`ArrowUp` 2, `ArrowLeft`/`ArrowDown` -2, `PageUp` 10, `PageDown` -10, `Home`/`End` return `null`; others `null`).
- Semantics: `begin` marks dragging and sends the value at once. `move(v)` records the latest value; if no request is in flight and at least `throttleMs` passed since the last send it sends now, otherwise it keeps one trailing timer that sends the latest value (a newer value replaces a queued one). A `send` that returns a promise counts as in flight until it settles (resolve or reject); one that returns nothing counts as in flight for the throttle gap only. `end()` clears dragging, flushes the pending value through the same throttle rules (so a trailing send always goes out), and starts the hold. `incoming(v)` returns `{show:false, value: latest}` while dragging; while held it returns `show:false` until `|v - latest| <= tolerance` or `now() - heldSince >= holdMs`, at which point the hold clears and it returns `{show:true, value:v}`; otherwise `{show:true, value:v}`.

- [ ] **Step 1: Write failing tests** with a fake clock (`now`, `setTimer`, `clearTimer` over an array of timers and a `tick(ms)` helper). Names and assertions:
  - `first move sends immediately; second within 100 ms waits for the trailing send`: sends `[30]` at t=0, then `move(40)` at t=50 sends nothing, at t=100 sends `[30,40]`.
  - `a newer value replaces the queued one`: moves 40, 50, 60 inside the gap send only 60 as trailing.
  - `trailing send of the final value always goes out on end()`: `move(70)` inside gap then `end()` leads to a send of 70 by t=100.
  - `only one request is in flight`: `send` returns a pending promise; further moves send nothing until it resolves, then exactly one send of the latest value.
  - `a rejected or never-settling-then-rejected request frees the slot` (Review Focus 1): after `reject`, the next move sends.
  - `incoming is ignored while dragging`: `incoming(10)` gives `show:false, value:` the dragged value.
  - `after end the user's value is held until the hub is within 1`: hub 10 holds, hub 31 after user value 30 shows.
  - `hold expires after 2 s even if the hub never agrees` (Review Focus 2): at t=1999 still held, at t=2000 `show:true` with the hub's value.
  - `end() twice is harmless` (pointercancel after pointerup).
  - `dispose clears timers`: no send after dispose.
  - `valueFromPointer clamps and rounds`: `(50, 0, 200)` is 25; `(-5, 0, 200)` is 0; `(500,0,200)` is 100; `(1,0,0)` is 0.
  - `keyStep` table above.
- [ ] **Step 2: Run** `node --test tests/js/volume-controller.test.js`. Expected: FAIL (module missing).
- [ ] **Step 3: Implement** `SITE/volume-controller.js` to the semantics above. Closure state, no classes, no DOM.
- [ ] **Step 4: Run** the same command. Expected: PASS.
- [ ] **Step 5: Commit** `feat(room-picker): volume send/hold state machine`.

---

### Task 3: Custom volume slider, haptics, wired into rooms.js

**Files:**
- Create: `SITE/haptics.js`, `SITE/volume-slider.js`
- Modify: `SITE/rooms.js` (imports at 19 and 78-79; `SLIDER_DEBOUNCE_MS` at 87; `buildCards` 559-600; `render` 722-742; `onToggle` 741-756; `onVolume` 758-763; `sendControl` 696-704), `SITE/style.css` (`.volume` rules at 666-711 and the 48 px coarse block near 1550), `AGENTS.md`
- Test: `tests/js/haptics.test.js`; extend `tests/js/page-imports.test.js` file list with `volume-slider.js`

**Interfaces:**
- Consumes: `createVolumeController`, `valueFromPointer`, `keyStep` (Task 2); tokens (Task 1).
- Produces:
  - `createHaptics(nav = globalThis.navigator): { volume(prev, next), roomOn(), roomOff(), playPause() }`. Each calls `nav.vibrate` only if it is a function, wrapped in try/catch. `volume(prev, next)` vibrates 14 when `next` is 0 or 100 and differs from `prev`; else 5 when `Math.floor(prev/10) !== Math.floor(next/10)`; else nothing. `roomOn()` 10, `roomOff()` 6, `playPause()` 8.
  - `createVolumeSlider({ label, onSend(percent): void | Promise<void>, haptics }): { el, set(percent), setEnabled(bool), applyStatus(percent) }` in `volume-slider.js`. `el` is the `div.volume` with children `.volume-fill` and `.volume-thumb`, plus a sibling-free structure so the caller can append it. `set` is user-facing optimistic render; `applyStatus` asks the controller `incoming()` and renders only when `show` is true.

- [ ] **Step 1: Write failing haptics tests**: `volume vibrates 5 across a ten boundary`, `volume vibrates 14 at 0 and at 100`, `volume does nothing inside a decade`, `roomOn 10, roomOff 6, playPause 8`, `missing navigator.vibrate is a no-op`, `a throwing vibrate is swallowed`.
- [ ] **Step 2: Run** `node --test tests/js/haptics.test.js`. Expected: FAIL. Implement `haptics.js`; rerun, PASS.
- [ ] **Step 3: Implement `createVolumeSlider`** in `volume-slider.js`. `role="slider"`, `tabindex="0"`, `aria-valuemin/max/now`, `aria-label`, `aria-disabled` when disabled. Pointer handlers: `pointerdown` (capture, `.drag` class, `controller.begin`), `pointermove` (only when captured, `controller.move`), `pointerup` and `pointercancel` and `lostpointercapture` (`controller.end`, remove `.drag`). Keydown uses `keyStep`; Home/End go to 0/100; each key press is a `begin`+`end` pair so it sends and holds. Rendering writes `aria-valuenow` and, in one `requestAnimationFrame`, `fill.style.transform = scaleX(v/100)` and `thumb.style.transform = translateX(<px>)` using the track width cached by a `ResizeObserver` (no layout reads per frame). Haptic calls happen only inside pointer and key handlers, via `haptics.volume(prev, next)`. Disabled sliders ignore all input.
- [ ] **Step 4: Wire `rooms.js`.** Replace the `<input type=range>` in `buildCards` with `createVolumeSlider`; the toggle click calls `haptics.roomOn()/roomOff()` after `onToggle` computes `next`. `render` calls `entry.slider.applyStatus(room.volume)` and `setEnabled(room.connected)`; delete `dragging`, `pending`, the debounce `onVolume` and `SLIDER_DEBOUNCE_MS`. `onSend` is `(p) => sendControl(volumeRequest(id, p))`. Change `sendControl` to `return` the socket-path `undefined` as is and return the `postRpc(...)` promise chain on the HTTP path (the catch stays, so the promise always settles). Add the new imports; keep names exported. Add `volume-slider.js` to the `page-imports` file list and run it.
- [ ] **Step 5: CSS for the slider** (replace `.volume` pseudo-element rules): `.volume` is 44 px tall hit area (48 on coarse pointer and kiosk), `touch-action: none; user-select: none; cursor: grab`, track drawn by `::before` 8 px high `oklch(1 0 0 / .14)` radius 8; `.volume-fill` 8 px high, full width, `transform-origin: left`, `background: linear-gradient(90deg, var(--accent-2), var(--accent))`, `will-change: transform`; `.volume-thumb` 26 px circle `var(--ink)`, `box-shadow: 0 2px 8px oklch(0 0 0 / .45)`, `transition: scale .18s var(--ease-out)`, `.volume.drag .volume-thumb { scale: 1.2 }`. `.volume[aria-disabled="true"]` opacity .5 with `transition: opacity .3s var(--ease-out)`.
- [ ] **Step 6: Verify in a browser** with the Playwright tools against a local static server with a stubbed WebSocket (a small inline mock in an `evaluate` call is enough): drag sends repeated values spaced at least 100 ms apart and a final one on release; a stale status during the 2 s hold does not move the thumb; arrow keys step by 2.
- [ ] **Step 7: Docs.** Add `AGENTS.md` section "Volume slider" after the pairing/devices section: the throttle, one-in-flight, ignore-while-dragging and hold-until-within-1-or-2-s rules, that `volume-controller.js` is the pure part with `tests/js/volume-controller.test.js`, and that haptics fire only from gestures.
- [ ] **Step 8: Run** `node --test tests/js/*.test.*`; expected PASS. **Commit** `feat(room-picker): custom volume slider that follows the finger`.

---

### Task 4: Cover color extraction and hue application

**Files:**
- Create: `SITE/cover-color.js`
- Modify: `SITE/rooms.js` (`updateNowPlayingCard` 1627-1661, `renderMirror` 874-930, the cover swap spots at 1516/1749/1637 as found by `codegraph explore "npArtImg src coverPathOrNull"`), `SITE/style.css` (art swatch fallback only if needed), `AGENTS.md`
- Test: `tests/js/cover-color.test.js`

**Interfaces:**
- Produces:
  - `extractHues(rgba: ArrayLike<number>): { h1: number, h2: number, chroma: number }`. `rgba` is canvas `ImageData.data` layout. `h1`,`h2` are degrees in [0,360); `chroma` is in [0,1]. Returns `DEFAULT_HUES` (`{h1: 255, h2: 285, chroma: 0}`) when too few pixels qualify.
  - `DEFAULT_HUES` (exported constant).
  - `chromaScale(chroma): number` returns `0.15 + 0.85 * chroma` (the `--k` value).
  - `applyCoverColor(root, hues)` sets `--h`, `--h2`, `--k` as inline style on `root` (a no-op if unchanged).
  - `loadCoverHues(src, { makeImage, makeCanvas }): Promise<{h1,h2,chroma}>` draws to a 32x32 canvas and calls `extractHues`; resolves `DEFAULT_HUES` on any failure (load error, `getImageData` throws).
  - `swapImage(img, src): Promise<void>` preloads with `new Image()` and `decode()`, then sets `img.src`; on decode failure it still sets `src`.
- Algorithm (the part the signature does not fix): convert sRGB to OKLab; ignore pixels with alpha below 128, L below 0.2, L above 0.92 or OKLab chroma below 0.04; if fewer than 5 percent of pixels remain return `DEFAULT_HUES`. Bin kept pixels into 36 hue bins weighted by chroma; `h1` is the circular mean of the heaviest bin and its neighbours; `h2` is the heaviest bin at least 40 degrees from `h1` with weight at least 25 percent of the top bin, else `(h1 + 30) % 360`. `chroma` is mean kept-pixel chroma divided by 0.12, clamped to 1.

- [ ] **Step 1: Write failing tests** building RGBA arrays by helper `solid(r,g,b,a,count)` and `mix(...parts)`:
  - `a red cover gives a red h1 (about 29 degrees) and chroma above 0.5`
  - `a two-color cover gives h1 for the larger area and a distinct h2`
  - `a single-hue cover gets h2 = h1 + 30`
  - `near-black and near-white pixels are ignored`: half pure white, half teal gives teal's hue.
  - `greyscale gives DEFAULT_HUES with chroma 0` (Review Focus 3)
  - `fully transparent, empty and 1-pixel inputs give DEFAULT_HUES, no NaN` (Review Focus 3)
  - `chromaScale(0) is 0.15 and chromaScale(1) is 1`
  - `loadCoverHues resolves DEFAULT_HUES when the image errors or the canvas throws` using injected fakes.
  - `applyCoverColor sets the three properties on a stub root` (object with `style.setProperty`).
- [ ] **Step 2: Run** `node --test tests/js/cover-color.test.js`. Expected: FAIL.
- [ ] **Step 3: Implement** `cover-color.js` per the signatures. Pure functions first; DOM touches only inside `loadCoverHues`/`swapImage` via injected factories defaulting to `Image` and `document.createElement("canvas")`.
- [ ] **Step 4: Run** the test; expected PASS.
- [ ] **Step 5: Wire `rooms.js`.** When the now-playing cover URL or the frame mirror cover changes (use the existing `state.npArtSrc` check), call `loadCoverHues(url)` then `applyCoverColor(document.documentElement, hues)`; guard against out-of-order resolutions with a token so a slow older cover cannot win. When there is no cover or the track clears, apply `DEFAULT_HUES`. Replace direct `img.src = url` for the now-playing art and the frame mirror image with `swapImage`. Source of truth for color is the now-playing cover; the frame mirror swap only preloads. Add the new imports (they are exported, so `page-imports` passes).
- [ ] **Step 6: Docs.** `AGENTS.md` section "Cover color": `cover-color.js` is pure (pixels to `{h1,h2,chroma}`), tokens are OKLCH from `--h`, `--h2`, `--k`, default hue 255, greyscale scales chroma down, and `@property` unsupported browsers change hue instantly.
- [ ] **Step 7: Verify in a browser** with two local cover images (one warm, one greyscale): background retints over about 1.4 s, greyscale turns near-neutral, no blank flash on swap.
- [ ] **Step 8: Run** `node --test tests/js/*.test.*`; PASS. **Commit** `feat(room-picker): page color follows the cover`.

---

### Task 5: Dusk styling across all screens, press feedback and kiosk

**Files:**
- Modify: `SITE/style.css` (everything below the tokens: top bar 157, drawer 189-250, frames 312, `.btn` 427-442, now playing ~560-600, rooms 623-711, toggle 640, `.btn-ghost` 1384, sheets, toast, grids, forms; reduced-motion block 1482; kiosk block 1501+; coarse-pointer block ~1540), `SITE/index.html` (class hooks only, if a panel wrapper is missing), `SITE/rooms.js` (only `playPause`-free wiring: none expected)
- Test: `tests/js/site-assets.test.js` (extend)

**Interfaces:**
- Consumes: tokens from Task 1, `.volume*` classes from Task 3.
- Produces: shared classes `.panel`, `.row`, `.pct` that Task 6 reuses.

- [ ] **Step 1: Extend the failing test** in `site-assets.test.js`:
  - `every transition and animation is covered by reduced motion`: the `prefers-reduced-motion: reduce` block contains `transition: none` and `animation: none` selectors for `.btn, .toggle, .volume-thumb, .chip, .room-label`-style catch-all (`*, *::before, *::after` is acceptable) and `@property` hue transition disabled via `:root { transition: none }`.
  - `kiosk block drops backdrop-filter`: the text of the `(min-width: 720px) and (max-height: 540px)` block contains `backdrop-filter: none`.
  - `no transition lasts 300ms or more except the hue drift`: regex all `transition:` and `animation:` durations; allow `1.4s` only on `:root`; the existing `tile-flash` 1.6 s animation is exempt by name.
  - `no light-theme tokens remain`: no `#e9e7e0`, `#f4f2ec` literals.
- [ ] **Step 2: Run** the test file. Expected: FAIL.
- [ ] **Step 3: Restyle Listen.** Now playing and Rooms become `.panel` (Global Constraints values). Now-playing: 72 px cover radius 12 beside title (600 18px/1.2 Instrument Sans), source line `--ink-3` 12.5 px, progress track 4 px `oklch(1 0 0 / .12)` with fill `--ink`. Room row: name left, `.pct` right (`--ink-2`, 13 px, tabular, shows the slider value; wire it from `volume-slider`'s render so it updates in the same rAF), slider beneath with 10 px vertical margin, rooms gap 16 px. Disconnected rooms: opacity .5 with `transition: opacity .3s var(--ease-out)`. Toggle becomes the 50x30 switch: track `oklch(1 0 0 / .14)`, on state `--accent`, thumb 24 px with `transition: transform .45s <cubic-bezier fallback>` overridden by `--spring` inside `@supports (transition-timing-function: linear(0, 1))`; keep the 44 px hit area with padding or `::before`. The visible "On"/"Off" text stays for screen readers (visually hidden class) since `render` sets `textContent`.
- [ ] **Step 4: Press feedback everywhere.** One shared rule list for `.btn, .btn-ghost, .toggle, .menu-button, .drawer-link, .frame-tab, .chip, .tile, .row-link`: `transition: transform var(--dur-press) var(--ease-out), background-color .2s var(--ease-out)`, `:active { transform: scale(.96) }`, `-webkit-tap-highlight-color: transparent`, `touch-action: manipulation`. Hover rules wrap in `@media (hover: hover)`. `:focus-visible { outline: 2px solid var(--accent); outline-offset: 3px }`. Primary buttons use `--accent` fill with dark ink; ghost buttons use a `oklch(1 0 0 / .08)` fill.
- [ ] **Step 5: Other screens.** Frame, Art and Settings sections use `.panel` sections with the same row and control styles; inputs, selects and textareas get `oklch(1 0 0 / .08)` fill, 12 px radius, `--ink` text, accent focus ring. Drawer (`dialog`) and the open-in sheet get panel fill and blur with `::backdrop` `oklch(0 0 0 / .5)`; toast uses panel fill. Cards and borders remain only for separate objects (library tiles, history rows get hairline `oklch(1 0 0 / .06)` separators). Art swatch placeholders and `tile-flash` keep working on the new tokens.
- [ ] **Step 6: Kiosk and coarse pointer.** Keep the existing two-column arrangement; inside the kiosk query set `.panel, dialog, .toast { backdrop-filter: none; -webkit-backdrop-filter: none; background: oklch(0.2 0.02 var(--h) / .72) }` and keep the 48 px targets. Replace the reduced-motion block with a catch-all (`*, *::before, *::after { transition: none !important; animation: none !important }` plus `:root { transition: none }`).
- [ ] **Step 7: Run** `node --test tests/js/*.test.*`; PASS.
- [ ] **Step 8: Verify visually** with the Playwright tools at 412x915, 360x740 and 800x480, on each of the four screens, the drawer, the sheet and the toast; screenshot into the scratchpad. Check: no horizontal overflow, text contrast on panels (ink on panel at least 4.5:1), tap targets, and that reduced motion (`browser_emulate_media`) makes the switch jump instantly.
- [ ] **Step 9: Commit** `feat(room-picker): Dusk styling, press feedback, kiosk`.

---

### Task 6: In-place updates, rAF progress, and timers that sleep when hidden

**Files:**
- Create: `SITE/keyed-list.js`, `SITE/page-timers.js`
- Modify: `SITE/rooms.js` (timers 370-381, visibility handler 389-400, `tickNowPlaying` 1673-1694, `renderLibraryGrid` 2022-2043 and `libraryTile` 2044, `renderDevices` 2648-2688, `deviceRow` 2585), `SITE/style.css` (`.np-fill`), `SITE/index.html` (np-fill structure if needed), `AGENTS.md`
- Test: `tests/js/keyed-list.test.js`, `tests/js/page-timers.test.js`

**Interfaces:**
- Produces:
  - `planKeyedUpdate(oldItems: {key: string, sig: string}[], newItems: {key: string, sig: string}[]): { remove: string[], create: string[], replace: string[], order: string[] }`. `order` is the final key order; `replace` lists keys present in both whose `sig` changed; duplicate keys in `newItems` keep the first only.
  - `patchKeyed(parent: Element, items: T[], { key(item): string, sig(item): string, create(item): Node, update?(node, item): void })`: applies the plan to the DOM, keeping surviving nodes (stored in a `WeakMap` per parent from key to `{node, sig}`), replacing only `replace` nodes, calling `update` for unchanged-sig survivors, and moving only nodes whose position differs.
  - `createPageTimers({ setInterval, clearInterval, isHidden }): { add(fn, ms), pause(), resume(), start() }`. `add` registers; `start`/`resume` begin all not-yet-running intervals exactly once; `pause` clears all and is safe before start; `resume` after resume is a no-op. `resume` also invokes no callbacks (the existing `visibilitychange` handler already refreshes).

- [ ] **Step 1: Write failing tests.**
  - keyed-list: `unchanged items produce an empty plan with the same order`; `a new key is created, a missing key is removed`; `reordered keys keep all nodes and change only order`; `a changed sig lands in replace, not remove plus create`; `duplicate keys in the new list are dropped after the first` (Review Focus 5).
  - page-timers (fake `setInterval` returning ids, counters): `start runs every registered interval once`; `pause clears them all`; `resume after pause restarts each exactly once`; `resume twice does not double` (Review Focus 4); `pause before start does not throw`; `add after start begins immediately unless paused`.
- [ ] **Step 2: Run** both files. Expected: FAIL. Implement both modules; rerun, PASS.
- [ ] **Step 3: Timers.** In `rooms.js` replace the eleven `setInterval` calls with `timers.add(...)` and `timers.start()` (use the real `window.setInterval`, `document.hidden`). In the existing `visibilitychange` handler: hidden calls `timers.pause()` and `stopProgressLoop()`; visible calls `timers.resume()` before its existing refresh calls. The socket and its reconnect are left alone.
- [ ] **Step 4: Devices and art grid.** `renderDevices` and `renderLibraryGrid` use `patchKeyed`: key is the device `id` / piece id; `sig` is a string of every field that affects the row's DOM including inline-confirm state, so an open confirm or a busy button replaces only its own row; `update` is omitted when a changed sig means replace. Thumbnails keep their `<img>` because unchanged tiles keep their nodes. Pressed, focused and hovered rows are never rebuilt by a poll that changed nothing.
- [ ] **Step 5: Progress.** Make the fill a full-width `.np-fill` with `transform-origin: left; transform: scaleX(f); will-change: transform` and no CSS transition. Add `startProgressLoop()`/`stopProgressLoop()` around one `requestAnimationFrame` loop that runs only while `state.nowTrack` exists, `status !== "paused"`, the progress is shown and `document.hidden` is false; the loop writes `scaleX` every frame but writes the elapsed/ends text only when the whole second changes. `tickNowPlaying` keeps the text and the visible/hidden toggles; remove its `style.width` write. Paused tracks render a static `scaleX` once.
- [ ] **Step 6: Docs.** `AGENTS.md`: one short paragraph under the page notes covering that polling timers pause while the tab is hidden (`page-timers.js`), lists patch by key (`keyed-list.js`), and the progress bar is a `scaleX` rAF loop.
- [ ] **Step 7: Run** `node --test tests/js/*.test.*`; PASS. Verify in the browser: devices list keeps a focused button through a poll; switching tabs away stops network polling (check `browser_network_requests` count stays flat while hidden, using `evaluate` to dispatch a visibility change if needed).
- [ ] **Step 8: Commit** `feat(room-picker): in-place list updates, rAF progress, pause when hidden`.

---

### Task 7: Whole-page verification and leftovers

**Files:**
- Modify: whatever the checks below turn up (expected: `SITE/style.css`, `SITE/rooms.js`, `AGENTS.md`)
- Test: full suites

**Interfaces:**
- Consumes: everything above. Produces: nothing new.

- [ ] **Step 1: Run** `node --test tests/js/*.test.*` and `uv run pytest tests/python -q`. Expected: all PASS.
- [ ] **Step 2: Grep gates** over the diff against the base branch (`git diff main...HEAD`): no em dash character, no `prefers-color-scheme`, no `hanken`, no `http://` or `https://` in `SITE/*.css|html|js` apart from existing `xmlns` strings, no IPv4 or MAC-shaped strings. Fix any hit.
- [ ] **Step 3: Anti-goal check in the browser** (Playwright, 412x915 and 800x480): no entrance animation on controls; the hue drift never delays a tap; a loud red cover and a saturated green cover both leave the ground at lightness 0.15 and the page calm (screenshot both); kiosk viewport has computed `backdrop-filter: none` on `.panel`.
- [ ] **Step 4: Cross-repo and docs check.** Page is snaphouse-only, nightstand unaffected. Confirm `AGENTS.md` has the slider, cover color and timers/keyed-list text and that every new module has a test file. If the room_picker role copies site files via an explicit file list in `roles/room_picker/tasks/`, add the new modules and fonts to it (`codegraph explore "room_picker site files copy"` or grep `hanken` and `rooms-model.js` across `roles/`); run `uv run pytest tests/python` again if touched.
- [ ] **Step 5: Commit** any fixes `chore(room-picker): Dusk verification fixes`. If nothing changed, skip.

---

## Self-review

- **Spec coverage:** Dusk look on every screen and kiosk (Task 5); color follows cover with `@property`, default 255, greyscale, preload swap (Tasks 1, 4); type (Task 1); slider custom element, throttle, in flight, no snap-back, transform-only rendering (Tasks 2, 3); press feedback, spring switch, haptics (Tasks 3, 5); progress rAF, keyed lists, hidden-tab timers (Task 6); dark-only removals (Task 1); docs (Tasks 3, 4, 6); reduced motion and 44 px targets (Task 5). No spec section without a task.
- **Type consistency:** `createVolumeController`, `valueFromPointer`, `keyStep` defined in Task 2 and consumed by name in Task 3; `extractHues`, `DEFAULT_HUES`, `chromaScale`, `applyCoverColor`, `loadCoverHues`, `swapImage` defined and used in Task 4 only; `planKeyedUpdate`, `patchKeyed`, `createPageTimers` defined and used in Task 6 only; token names from Task 1 match Tasks 3 and 5.
- **Rulings the spec was silent on:** (a) the page has no play/pause control today, so the 8 ms play/pause haptic is implemented in `haptics.js` and tested but not wired; (b) the color source is the now-playing cover, the frame mirror only preloads; (c) the chroma scale `--k` is `0.15 + 0.85 * chroma`; (d) keyboard interaction counts as a gesture and sends with the same throttle.
