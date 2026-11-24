# Panel Now Playing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The panel's Music tab leads with Now playing (with resume for a paused Bluetooth track while the phone stays connected), the idle screen shows the music, and the backlight does not dim while it does.

**Architecture:** The hub keeps a paused Bluetooth track past the 600 s hold while the phone's player is present, publishing it in a new additive top-level `resume` block so `track` (what frames read) behaves exactly as before. Decisions live in pure, tested model modules (`now-model.js`, additions to `screensaver-model.js`, `backlight.py`'s `MusicWatch`); `panel-music.js`, `screensaver.js` and the backlight loop only draw and wire.

**Tech Stack:** Python 3 stdlib plus pytest (uv); plain ES modules with `node --test`; Ansible templates.

**Spec:** `docs/superpowers/specs/2026-11-23-panel-now-playing-design.md` (mockup beside it; its "Calls made after the builder check" section is binding). Builds on `2026-11-23-panel-layout-design.md` and `2026-11-22-panel-screensaver-design.md`.

## Global Constraints

- No em dashes in code, comments, docs, commit messages or UI text. No identifying data (no addresses, names, hostnames beyond `house.local` placeholders already in repo).
- TDD: failing test first, keep the RED output, then GREEN. Conventional Commits, one commit per task.
- Panel sizes: cover 300 px, title 34 px (2 lines max), artist 24 px, transport 92 px tall, Rooms button 64 px, "Paused on <phone>" amber 20 px; ambient cover 340 px, title 40 px, artist 28 px. No panel text under 20 px except existing exceptions; targets at least 72 px (the Rooms button is the spec's 64 px).
- Return to Now playing after 30 s on Rooms with no touch. Ambient while playing or paused under 5 minutes, any time of day; else Aurora.
- Paused clock is each device's own, started when it first sees the track paused (page and backlight each keep it). Never compare a hub timestamp to a Pi clock.
- Backlight: stdlib only; hub origin from the kiosk's `panel_url` via the unit's environment; read `now-playing.json` every 10 s; on a failed read keep the last answer up to 120 s, then act as if nothing plays.
- A hub restart forgets a paused track (known limit, no persistence).
- Panel Music idle line ends after "connect over Bluetooth"; phones keep their copy. Phone name for "Paused on <phone>" comes from the page's Bluetooth devices list; none connected gives "Paused".
- Frames: `track` in `now-playing.json` keeps today's semantics (null after the 600 s hold).

## Review Focus

- Paused AirPlay track, or Bluetooth track whose player went away, within the 600 s hold: shows on Now playing and counts for the 5-minute rule, never a Play button (AirPlay: "Control from the phone"; Bluetooth without player: no transport).
- Phone disconnects while paused past the hold: the `resume` block clears.
- Track with no art, no artist, no duration (progress hides, no NaN).
- Very long title (two lines, clipped, buttons stay on screen).
- Hub unreachable at the backlight: the 2 minute grace, then normal dimming; garbage JSON counts as a failed read.
- Tap on the ambient screensaver only dismisses, then lands on Now playing.

---

## Files

- Modify `lib/snaphouse/nowplaying.py`: paused-with-player hold, `controllable`, `resume` block.
- Modify `tests/python/test_nowplaying.py`: hold tests.
- Create `roles/room_picker/files/site/now-model.js`, `tests/js/now-model.test.js`: pure Now playing rules (`resolveTrack`, `nowView`, `pausedClock`, `ambientWanted`, `rooms button label`, `ReturnTimer` rule).
- Modify `panel-music.js`, `panel-model.js`, `panel.css`, `panel.js`, `rooms.js`: the Music tab views.
- Modify `screensaver-model.js`, `screensaver.js`, `rooms.js`, `style.css`/`panel.css`: ambient mode.
- Modify `lib/snaphouse/backlight.py`, `tests/python/test_backlight.py`, `roles/panel/templates/snaphouse-backlight.service.j2`, `tests/python/test_panel_role.py`.
- Modify `AGENTS.md`, `docs/cross-repo-features.md`, `tests/js/docs-contract.test.js` if it pins shapes.

### Task 1: Hub keeps a paused Bluetooth track while the phone's player is present

**Files:** Modify `lib/snaphouse/nowplaying.py` (`Tracker._ingest_props`, `_hold_check`, `_core`, `now_playing_doc`); Test `tests/python/test_nowplaying.py`.

**Interfaces:**
- Produces: `track.controllable: bool` (true when the latest House properties carry `canControl` true; Bluetooth with a phone player only). New top-level `resume: dict | null` in the doc: the same track shape as `track` plus `controllable`, present only when the 600 s hold has passed and the phone's player is still present; then `track` is `null`.

- [ ] **Step 1: Failing tests** (names and assertions): `test_paused_bluetooth_stays_while_player_present` (play, pause, tick 601 s, `doc["resume"]["status"] == "paused"`, `doc["track"] is None`); `test_resume_clears_when_player_goes_away` (then properties with `canControl` false, `doc["resume"] is None`); `test_hold_still_clears_sources_without_a_player` (airplay paused 601 s: both `track` and `resume` null); `test_within_hold_track_carries_controllable` (`doc["track"]["controllable"] is True` for bluetooth with player, False for airplay); `test_resume_play_returns_track` (status back to playing: `track` set, `resume` null).
- [ ] **Step 2: Run** `uv run pytest tests/python/test_nowplaying.py -k "resume or controllable or player" -v`, expect FAIL (keys missing).
- [ ] **Step 3: Implement.** Store `self._controllable = bool(properties.get("canControl"))` in `_ingest_props` (update on every properties message). In `_hold_check`, when the hold passes and `self._controllable` and source is bluetooth, do not clear; set `self._held_resume = True`. In `now_playing_doc`, build the track dict once; if `_held_resume` publish it under `resume` and `track: null`; clear `_held_resume` when status returns to playing or `_controllable` goes false (then run the normal clear). Add both to `_core()`.
- [ ] **Step 4: Run** the focused tests, then `uv run pytest tests/python/test_nowplaying.py tests/python/test_nowplaying_runtime.py`, expect PASS.
- [ ] **Step 5: Commit** `feat(nowplaying): keep a paused Bluetooth track while the phone's player is present`.

### Task 2: Pure Now playing model

**Files:** Create `roles/room_picker/files/site/now-model.js`; Test `tests/js/now-model.test.js`.

**Interfaces:**
- `resolveTrack(doc) -> {track, held}`: `doc.track`, else `doc.resume` with `held: true`, else null (extends the page's parser input; Task 3 wires it).
- `pausedClock(prev, track, nowMs) -> {since: number|null}`: `since` set to `nowMs` the first time a track is seen paused, kept while paused, null when playing or no track; a different track identity restarts it.
- `ambientWanted(track, clock, nowMs, limitMs = 300000) -> boolean`: playing, or paused with `nowMs - since < limitMs`.
- `nowView(track, {phoneName, speakersDown}) -> {mode: "none"|"track", title, artist, paused, pausedLine, transport: "buttons"|"airplay"|"none", playing, art}`: `pausedLine` is `"Paused on <phone>"` or `"Paused"`; transport is `buttons` for bluetooth with `controllable`, `airplay` for airplay (text "Control from the phone"), else `none`.
- `roomsButtonLabel(rooms) -> string`: names rooms that are on, joined with ", "; "Rooms" when none.
- `ROOMS_RETURN_MS = 30000`; `returnDue(lastTouchMs, nowMs) -> boolean`.
- `idleLine(btName)` is not here: Task 3 adds `panelHowTo(btName)` to `panel-model.js`.

- [ ] **Step 1: Failing tests** covering each function with the spec values (5 min boundary exact: 299 999 ms true, 300 000 false; held bluetooth without `controllable` gives transport `none`; airplay paused gives `airplay`; no phone gives `"Paused"`; no art/artist/duration give null-safe fields).
- [ ] **Step 2: Run** `node --test tests/js/now-model.test.js`, expect FAIL (module missing).
- [ ] **Step 3: Implement** the functions above.
- [ ] **Step 4: Run**, expect PASS. **Step 5: Commit** `feat(panel): pure rules for the Now playing screen`.

### Task 3: Panel Music tab: Now playing, Paused, Rooms, 30 s return

**Files:** Modify `panel-music.js` (add the Now playing view next to the existing bar and columns, a view switch), `panel-model.js` (`panelHowTo(btName)`, bar tap targets), `panel.css` (under `html[data-panel]`), `rooms.js` (parse `resume` through `resolveTrack`, expose `phoneName` from the devices list, `host.touch` hook, track `held`), `tests/js/panel-model.test.js`, `tests/js/panel-css.test.js`, `tests/js/rooms-panel-wiring.test.js`, `tests/js/now-model.test.js`.

**Interfaces:** Consumes Task 2. `host.state.nowTrack` keeps its shape plus `controllable` and `held`. `host.phoneName()` returns the connected device's name or null.

- [ ] **Step 1: Failing tests:** `panelHowTo("X")` ends `...over Bluetooth.` with no "then"; phone `howToText` unchanged; panel-css test pins 300 px cover, 34 px title, 24 px artist, 92 px transport, 64 px Rooms button, 20 px paused line; wiring test: a held track reaches `nowTrack` and `rooms.js` has no phone-chrome change.
- [ ] **Step 2: Run** the three test files, expect FAIL.
- [ ] **Step 3: Implement.** Music view state `now|rooms`: with a track, entering Music (hash `#listen` or the 30 s return) shows Now playing; the Rooms button and a tap on the screen's rooms control switch to Rooms; a tap on the Rooms bar's art or title switches back; `returnDue` is checked by the existing 500 ms render timer, and any pointerdown on the Rooms view resets `lastTouch`. With no track Music shows the existing idle bar and columns (Rooms). Buttons from `nowView.transport`: `buttons` previous/play-pause/next via `host.playback`, `airplay` shows "Control from the phone", `none` shows nothing. Paused: amber line, art dimmed, Play icon.
- [ ] **Step 4: Run** `node --test tests/js/*.test.js`, expect PASS. **Step 5: Commit** `feat(panel): Now playing leads the Music tab`.

### Task 4: Screensaver mode: ambient Now Playing or Aurora

**Files:** Modify `screensaver-model.js` (`screensaverMode({track, clock, nowMs}) -> "ambient"|"aurora"` using `ambientWanted`), `screensaver.js` (ambient layer: cover 340 px, title 40 px, artist 28 px, thin progress line, hue-tinted background; Aurora canvas paused while ambient), `rooms.js` (feed the track and hues, on dismiss with a track set hash to `#listen` and Now playing view), `panel.css`; Tests `tests/js/screensaver-model.test.js`, `tests/js/screensaver.test.js`, `tests/js/rooms-screensaver-wiring.test.js`.

**Interfaces:** Consumes Task 2. `createScreensaver(... , nowTrack: () => track|null)`; `setHues` also tints the ambient background.

- [ ] **Step 1: Failing tests:** mode is ambient at 03:00 with a playing track; ambient for paused 4 min, aurora at 5 min; aurora with no track; the first pointerdown on the ambient view dismisses and is swallowed (existing 400 ms rule); dismissing with a track sets `#listen` and Now playing; the mode is re-evaluated while up (music stops at the 5 min mark: the layer switches to Aurora); no Aurora rAF loop runs while ambient.
- [ ] **Step 2: Run** the three files, expect FAIL.
- [ ] **Step 3: Implement** (progress line moved once per animation frame only while ambient and visible, as the existing progress rule).
- [ ] **Step 4: Run** `node --test tests/js/*.test.js`, expect PASS. **Step 5: Commit** `feat(panel): the screensaver shows the music while it plays`.

### Task 5: Backlight no-dim rule and hub origin

**Files:** Modify `lib/snaphouse/backlight.py` (`MusicWatch`, `--now-playing-url`, loop wiring), `roles/panel/templates/snaphouse-backlight.service.j2` (`Environment=SNAPHOUSE_HUB_ORIGIN=` derived from `panel_url` scheme and host, no path or query; flag passed only when set), Tests `tests/python/test_backlight.py`, `tests/python/test_panel_role.py`.

**Interfaces:** `MusicWatch(fetch, clock, grace_s=120, ambient_s=300)` with `poll() -> None` (called every 10 s from the loop's existing wait, no thread) and `music_active() -> bool`: true when the last good read shows `track` or `resume` with status playing, or paused under `ambient_s` on the watch's own monotonic clock from first seen paused; a failed read keeps the last answer up to `grace_s` then returns False. `Schedule.target_pct(now, idle_s, music=False)` returns `full_pct` when `music` is true.

- [ ] **Step 1: Failing tests:** playing in quiet hours stays full; paused 4 min full, 5 min dim; failed fetch kept 119 s then dims; garbage JSON counts as failed; `resume` block with paused status counts as paused (clock from first seen); outside quiet hours unchanged; unit renders `SNAPHOUSE_HUB_ORIGIN` from a `panel_url` with path, query and fragment, and renders no origin when `panel_url` is unset.
- [ ] **Step 2: Run** `uv run pytest tests/python/test_backlight.py tests/python/test_panel_role.py -v`, expect FAIL.
- [ ] **Step 3: Implement** with `urllib.request` (timeout 3 s), stdlib only; polling runs in the loop with a 10 s cadence independent of the 2 s interval.
- [ ] **Step 4: Run**, expect PASS. **Step 5: Commit** `feat(panel): no night dim while the music shows`.

### Task 6: Frame check, docs and contract

**Files:** Modify `AGENTS.md` (Panel layout and Panel screensaver sections, backlight, hub hold), `docs/cross-repo-features.md` (now-playing shape: `controllable`, `resume`; frame unchanged), `tests/js/docs-contract.test.js` and any docs test that pins the doc; Test `tests/python/test_nowplaying.py::test_frame_view_unchanged`.

- [ ] **Step 1: Failing test:** after a paused Bluetooth track passes the hold with the player present, `doc["track"] is None` and `doc["rooms"]` unchanged, exactly as the pre-change frame sees it; before the hold the `track` keys are the old keys plus only `controllable`.
- [ ] **Step 2-4:** run, adjust docs, run `uv run pytest tests/python` and `node --test tests/js/*.test.js`. **Step 5: Commit** `docs: panel Now playing, resume and the frame contract`.

## Final checks

Full `uv run pytest tests/python`, `node --test tests/js/*.test.js`, `ansible-playbook --syntax-check site.yml`, `ansible-lint`, `shellcheck` (report any not installed); optional 800x480 screenshots in a scratch dir via a throwaway-HOME browser.

## Self-review notes

Spec coverage: resume hold (T1), Now playing/Paused/Rooms/30 s/AirPlay/idle line (T2, T3), screensaver (T4), backlight (T5), frames check and docs (T6). Open interpretation for the coordinator: past the hold the hub publishes the held track under `resume` rather than `track`, so frames behave exactly as before.
