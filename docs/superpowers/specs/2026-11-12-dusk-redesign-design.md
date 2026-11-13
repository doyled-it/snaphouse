# Dusk redesign of the room-picker page

## Why

The owner uses the room-picker page every day, mostly on an Android Pixel in Chrome or
Firefox, sometimes on an iPhone, Macs, and the 800x480 control panel in kiosk mode. It
works, but it feels dated and choppy: the volume bars stutter and snap back, and nothing
reacts to a press. The owner wants it dark, calm and soothing, with its color taken from
whatever is playing, and it should feel smooth and tactile.

The owner reviewed three mockups (Glow, Bars, Dusk) and chose **Dusk**: the whole page
background is a dark gradient made from the current cover's colors, with frosted panels
on top and a thin volume slider with a round thumb.

Mockup: https://claude.ai/artifact/UXMQGjtx7GKd4pezR7DrEd (private to the owner). The
source of the mockup is the reference for look and feel; its Dusk column is the target.

## Goals

1. The Dusk look across every screen: Listen, Frame, Art, Settings, the menu drawer,
   dialogs, the toast and the kiosk layout.
2. Color follows the music: the page's two hues come from the current cover and change
   slowly when the cover changes.
3. The volume slider follows the finger, sends while dragging and never snaps back.
4. Every press gets visible feedback, and on Android a light vibration where noted.
5. Updates patch the page in place; nothing visible is rebuilt on a poll.

## Non-goals

- No change to the hub, its APIs or the status file schema. This is a page-only change
  inside `roles/room_picker/files/site/` plus its tests and docs.
- No light theme. The page becomes dark-only (owner's choice: "dark, calm"). That means
  removing the `prefers-color-scheme` blocks in `style.css`, setting `color-scheme: dark`
  (today `light dark`), and making the `theme-color` meta in `index.html` dark.
- No new screens or features. Every existing control keeps its job and its name.
- No framework, bundler or build step. Plain ES modules stay.

## Anti-goals (a failure even if it works)

- Motion that makes the page feel slow: no entrance animations on controls, nothing over
  300 ms that blocks input, no animation the owner has to wait for.
- Color that shouts: covers with loud colors must still give a dark, calm page.
- A kiosk panel that stutters because of blur effects.

## Constraints

- No external requests from the page. Fonts are self-hosted woff2 in `site/fonts/` with
  their OFL license, like Hanken Grotesk today. No CDN, no Google Fonts.
- No em dashes anywhere (code, comments, UI copy, docs, commits).
- No identifying data in the repo.
- TDD with the repo's runners: `node --test tests/js/*.test.*` for page logic,
  `uv run pytest tests/python` for anything Python-side (expected to be none).
- `prefers-reduced-motion: reduce` turns transitions and springs off (instant changes).
- Touch targets at least 44 px on touch devices.
- Existing pinned behavior in `tests/js/` (pair controller deadlines, devices controller,
  rooms model) keeps passing; tests change only where the behavior intentionally changes.

## What the owner decided

- **Direction:** Dusk, dark only.
- **Color from the cover:** yes, it should affect the whole page and feel soothing.
- **Tactile feel and smoothness:** the problems listed below are to be fixed.

## Design

### Color

- Covers are same-origin `/cover/...` images (`coverPathOrNull` in `rooms-model.js`), so
  the page can read their pixels. On each new cover, draw it to a 32x32 canvas, and pick
  a primary and a secondary hue from the pixels, ignoring near-black, near-white and
  low-chroma pixels.
- The pure part (pixels in, `{h1, h2, chroma}` out) lives in a module with node tests.
- Page tokens are OKLCH built from those hues with fixed, low lightness and capped chroma,
  as in the mockup: ground `oklch(0.15 0.03 h1)`, two corner glows from `h1` and `h2`,
  frosted panels `oklch(0.2 0.02 h1 / .45)`, accent `oklch(0.80 0.11 h1)`.
- A greyscale or nearly colorless cover gives near-neutral tokens (chroma scaled down),
  not a random hue.
- With no cover (nothing playing, or a title-only track) the page uses a calm default:
  slate blue, hue 255. **(Controller's call.)**
- Hue changes animate over 1.4 s with registered `@property` custom properties; reduced
  motion makes them instant. Where `@property` is unsupported the hue simply changes
  instantly. A cover that goes away eases back to the default hue the same way.
- Cover and frame-mirror image swaps preload the new image (`new Image()`, decode) before
  swapping `src`, so there is no blank flash.

### Type

Instrument Sans (OFL, self-hosted variable woff2, latin and latin-ext) replaces Hanken
Grotesk, as in the approved mockup. Tabular numbers for volumes, times and counts.
**(Controller's call: the mockup used it and the owner approved the mockup.)**

### Volume slider (the main fix)

Today: a native range input, writes debounced 150 ms trailing-only (nothing is sent while
dragging), and a status push can write the old value back after release (`rooms.js`
around lines 87, 722-763).

New behavior:
- A custom slider element with `role="slider"`, `aria-valuenow`, keyboard arrows (2 per
  press) and Page Up/Down (10), `touch-action: none`, pointer capture, and
  `pointercancel` treated as release. The whole track is the hit area; the thumb is 26 px
  and grows slightly while held.
- The fill and thumb move with `transform` in at most one `requestAnimationFrame` update
  per frame. No `width` or `left` writes per frame.
- Sends while dragging: at most one write every 100 ms, with a trailing send of the
  final value, and at most one request in flight per room (a newer value replaces a
  queued one). Over the WebSocket, `Client.SetVolume` has no per-request ack in the page,
  so "in flight" there means the 100 ms gap; over the HTTP fallback it means the pending
  request's promise.
- No snap-back: while a room is being dragged, incoming status for that room's volume is
  ignored. After release (including the window before the trailing send goes out), the
  page keeps showing the user's value until the hub reports a value within 1 of it, or
  2 s pass, whichever is first.
- The pure part (throttle, in-flight, hold-until-confirmed) is a small state machine with
  node tests, independent of the DOM.

### Press feedback and haptics

- Buttons, chips, list rows and switches scale to about 0.96 on press with a 120-140 ms
  ease-out, and spring back. The tap highlight is off; `:focus-visible` shows a ring.
- Switches move with a spring easing (`linear()` with a cubic-bezier fallback).
- Android only (`navigator.vibrate`, feature-detected, only from a user gesture):
  5 ms each time the volume crosses a multiple of 10, 14 ms at 0 and 100, 10 ms when a
  room is switched on, 6 ms when switched off, 8 ms for play/pause. iOS gets none.

### Smoothness elsewhere

- The now-playing progress bar is drawn with `transform: scaleX` from a single
  `requestAnimationFrame` loop that runs only while something is playing and the tab is
  visible.
- The Bluetooth devices list and the art grid update in place by key: unchanged items
  keep their DOM nodes (no thumbnail reloads, no lost focus or press).
- All polling timers (about ten, started near `rooms.js:370-381`) pause while the tab is
  hidden. The existing `visibilitychange` handler already refreshes on return; it also
  restarts them.
- Haptics fire only from the user's own gesture, never from a status update.
- Motion uses only `transform` and `opacity`. Durations stay under 300 ms, apart from
  the slow hue drift, which never blocks input.

### Layout

Same information architecture as today (sticky top bar, menu drawer, four screens). The
Listen screen follows the Dusk mockup: a frosted now-playing panel (cover, title, source,
progress, controls) and a frosted Rooms panel where each room is a name, a percentage, a
switch, and the slider beneath. Other screens reuse the same panel, row and control
styles. Cards and borders only where an element is a separate object.

### Kiosk panel

The kiosk layout is not a mode but a media query, `(min-width: 720px) and
(max-height: 540px)` (`style.css` around 1493-1510), which the 800x480 panel matches and
so does a phone turned sideways. That layout keeps its current arrangement in the new
look, and inside that query panels drop `backdrop-filter` for solid translucent fills so
the panel's browser does not stutter. Sideways phones lose the blur too, which is fine.
**(Controller's call.)**

## Left to the builder

- Exact styling of the Frame, Art and Settings controls within the tokens above.
- File layout for the new pure modules, following the existing `*-controller.js` and
  `rooms-model.js` pattern.
- How to split the change into tasks and PRs.

## Docs

`AGENTS.md` gains a short section on the slider rules (throttle, hold-until-confirmed)
and the cover-color module, the way it documents the pairing controller. Cross-repo: the
page is snaphouse-only; nightstand is unaffected (N/A).
