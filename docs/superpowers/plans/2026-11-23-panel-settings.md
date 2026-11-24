# Panel settings Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the owner change the wall panel's screen and screensaver settings from the panel itself, saved per panel on the hub and applied live to the page and the backlight daemon.

**Architecture:** The art admin service gains a guarded `POST /api/panel/settings` route backed by a pure `panelsettings.py` module, and publishes `/panel-settings.json`. The page (`panel.js`) reads and writes it through one autosave queue and feeds the screensaver. The backlight daemon reads the same document with its now-playing poll, caches the last good copy on disk, and answers a loopback-only preview and refresh API.

**Tech Stack:** Python 3 stdlib (hub service, daemon), pytest (uv), plain ES modules with `node --test`, Ansible templates (Jinja2, `trim_blocks`), nginx.

**Spec:** `docs/superpowers/specs/2026-11-23-panel-settings-design.md` (mockup beside it: `2026-11-23-panel-layout-mockup.html`, More variant B and "More: Screen").

## Global Constraints

- No identifying data in the repo (no MAC or IP addresses, Wi-Fi details, usernames, names, location). The daemon's local API binds `127.0.0.1` only.
- No em dashes anywhere (code, comments, docs, commit messages, UI text).
- TDD: failing test first, watch it fail for the right reason, then implement.
- Panel text 20 px minimum (rail labels 17 px and tile lines 19 px are the only exceptions), targets 72 px, every panel CSS rule under `html[data-panel]` (`tests/js/panel-css.test.js`).
- Phones never load panel modules: new panel code loads only through the existing `panel.js` dynamic import (`tests/js/rooms-panel-wiring.test.js`).
- Ansible templates render with `trim_blocks`: never end a template line with a block tag such as `{% endif %}`; use inline `{{ x if c else '' }}`. Tests render with `trim_blocks=True`.
- Role defaults, not inventory host vars, are the place to change role behavior (host vars cannot override role `vars/main.yml`).
- Settings table (spec): Brightness 20 to 100 step 5 (default `panel_backlight.full_pct`); Night dim 0 to 100 step 5 (`dim_pct`); Night starts and ends a time in 30 min steps (`quiet_start`, `quiet_end`); Screensaver on or off (`panel_screensaver.enabled`); Starts after one of 30, 60, 120, 300, 600 s (`day_idle_s`); Style `aurora`; Screensaver brightness 5 to 100 step 5, default 40 (new var `panel_screensaver.brightness_pct`). Equal night start and end are refused.
- Combined rule (spec table), after the wait with no touch: music (playing, or paused under 5 min) with screensaver on shows ambient Now Playing at screensaver brightness; music with screensaver off shows no overlay at Brightness; no music by day with screensaver on shows the Style at screensaver brightness; no music by night with screensaver on shows the Style at night dim; no music, screensaver off: Brightness by day, night dim by night. A touch returns to Brightness.
- The wake-tap grab holds only while dimmed to night dim with the screensaver off (`panel_backlight.grab_wake_tap: true` forces it, `--no-grab` still disables it).
- Writes need `X-Snaphouse: 1` and `X-Snaphouse-Origin: home` (tunnel or missing means 403). Unknown panel id is 404. The preview cap is 15 s.

## Review Focus

- A write that changes only night start so it equals the stored night end: refused (400), stored values unchanged.
- A patch with an unknown key, a float where an int belongs, a bool-as-int, or a string wait: refused, nothing stored.
- Corrupt or missing state file on the hub: the document carries defaults for every panel and the service still starts.
- Corrupt cache file or garbage `panel-settings.json` on the panel: the daemon keeps the previous good settings, then the flag defaults; it never crashes.
- Hub unreachable at boot with a cache: the daemon starts from the cache, never from full brightness all night.
- A preview request from another browser origin or with seconds above 15: refused or clamped; a failed local call never breaks the page.
- A page with no `pid` or a document with no entry for it: the page keeps the kiosk URL values and hides the two tiles.

---

## File structure

- Create `lib/snaphouse/panelsettings.py`: pure validation, merge, defaults, atomic storage, document.
- Modify `lib/snaphouse/artadmin.py`: `--panel`, `--panel-state` and default flags, `/api/panel/settings` route, origin guard, publish `panel-settings.json`.
- Modify `roles/art_share/files/art.conf`, `roles/art_share/templates/snaphouse-artadmin.service.j2`, `roles/art_share/files/verify.d/artadmin.sh`, `vars/all.yml`.
- Modify `roles/panel/templates/snaphouse-kiosk.env.j2` and `snaphouse-backlight.service.j2`.
- Modify `lib/snaphouse/backlight.py`: settings model and combined rule, settings watch and cache, shared poll thread, local API.
- Create `roles/room_picker/files/site/panel-settings-model.js` (pure), `panel-settings-controller.js` (store, polling, autosave, local calls), `panel-settings.js` (the two screens).
- Modify `panel-model.js`, `panel-gallery-model.js` (More tiles), `panel-more.js` or `panel.js` (registration), `panel.css`, `screensaver-model.js`, `screensaver.js`, `rooms.js`.
- Tests: `tests/python/test_panelsettings.py`, `test_artadmin.py`, `test_artadmin_role.py`, `test_panel_role.py`, `test_backlight.py`; `tests/js/panel-settings-model.test.js`, `panel-settings-controller.test.js`, `panel-gallery-model.test.js`, `panel-model.test.js`, `screensaver-model.test.js`, `screensaver.test.js`, `panel-css.test.js`, `rooms-panel-wiring.test.js`.
- Modify `AGENTS.md`.

Key names inside a panel's entry (builder's call): `brightness_pct`, `night_dim_pct`, `night_start`, `night_end` (`"HH:MM"`), `screensaver_on` (bool), `screensaver_wait_s`, `screensaver_style`, `screensaver_pct`. Local daemon port 8784 (`backlight_local_port`, next to 8781 to 8783). Local API: `POST /preview` body `{"pct": 0..100, "seconds": 0..15}` (seconds 0 cancels) and `POST /refresh`.

### Task 1: Hub module `panelsettings.py`

**Files:**
- Create: `lib/snaphouse/panelsettings.py`
- Test: `tests/python/test_panelsettings.py`

**Interfaces:**
- Produces: `DEFAULTS_KEYS`, `class PanelError(Exception)` with `.status: HTTPStatus` and `.message: str`, and `class PanelSettings(state_path: Path, panels: list[str], defaults: dict)` with `.document() -> dict` (`{"version": 1, "panels": {id: full settings}}`), `.settings(panel) -> dict`, `.update(panel, patch: dict) -> dict` (returns the panel's full settings; raises `PanelError` 404 unknown panel, 400 invalid), `.require_panel(panel)`. Also `validate_patch(patch) -> dict` and `default_settings(**kwargs) -> dict` helpers.

- [ ] **Step 1: Write failing tests** in `tests/python/test_panelsettings.py`: `test_document_carries_defaults_for_every_panel`, `test_update_merges_only_given_keys_and_persists`, `test_update_returns_full_settings`, `test_unknown_panel_is_404`, `test_rejects_out_of_range_and_off_step_values` (brightness 15, 105, 22; night dim 7, -5, 105; screensaver_pct 0 and 3; wait 45), `test_rejects_wrong_types` (float, bool, string, None), `test_rejects_unknown_key_and_empty_patch`, `test_rejects_bad_time_and_off_step_time` (`"25:00"`, `"22:15"`, `"7:00"`), `test_rejects_equal_start_and_end_after_merge` (patch night_start to the stored night_end), `test_failed_write_stores_nothing`, `test_corrupt_state_file_falls_back_to_defaults`, `test_state_is_written_atomically` (no leftover temp file), `test_stored_panel_not_in_inventory_is_ignored`, `test_style_must_be_aurora`.
- [ ] **Step 2: Run** `uv run pytest tests/python/test_panelsettings.py -q`. Expected: FAIL, `ModuleNotFoundError: snaphouse.panelsettings`.
- [ ] **Step 3: Implement** the module (stdlib only). Validation is strict: `type(v) is int` for numbers (bool refused), `is bool` for `screensaver_on`. Merge, validate the merged object (start != end), then write via temp file plus `os.replace` under a `threading.Lock`. Defaults are validated at construction by the same rules (a bad default raises `ValueError` at startup).
- [ ] **Step 4: Run** the same command. Expected: all PASS.
- [ ] **Step 5: Commit** `feat(panel): hub module for per-panel screen settings`.

### Task 2: Art admin route, origin guard and published document

**Files:**
- Modify: `lib/snaphouse/artadmin.py` (`ArtAdmin.__init__` gets optional `panels: PanelSettings | None = None` and `panel_settings_path: Path | None = None`; `_routes`, `publish`, `handle_post`, `main`)
- Test: `tests/python/test_artadmin.py`

**Interfaces:**
- Consumes: `PanelSettings` from Task 1.
- Produces: `POST /api/panel/settings` body `{"panel": "<id>", ...changed keys}` replies `{"panel": "<id>", "settings": {...full}}`; published `<public>/art/panel-settings.json`; CLI flags `--panel ID` (repeatable), `--panel-state PATH`, `--panel-brightness`, `--panel-night-dim`, `--panel-night-start`, `--panel-night-end`, `--panel-screensaver on|off`, `--panel-wait`, `--panel-screensaver-brightness`.

- [ ] **Step 1: Write failing tests**: `test_panel_settings_write_saves_and_republishes`, `test_panel_settings_reply_is_that_panels_full_settings`, `test_panel_settings_unknown_panel_404`, `test_panel_settings_invalid_value_400_and_document_unchanged`, `test_panel_settings_requires_the_guard_header` (403), `test_panel_settings_refused_from_tunnel_and_missing_origin` (send `X-Snaphouse-Origin: tunnel`, and none: both 403, nothing stored), `test_panel_settings_allowed_from_home`, `test_other_routes_ignore_the_origin_header` (art settings still work with no origin), `test_panel_document_is_published_at_startup_with_defaults`, `test_main_builds_panels_from_flags` (via argument parsing helper).
- [ ] **Step 2: Run** `uv run pytest tests/python/test_artadmin.py -q -k panel`. Expected: FAIL (404 on the unknown endpoint, missing attributes).
- [ ] **Step 3: Implement.** Route `/api/panel/settings` calls `self.panels.update`. In `handle_post`, after the guard-header check and before reading the body, a path in `ORIGIN_REQUIRED_PATHS = {"/api/panel/settings"}` needs `X-Snaphouse-Origin == "home"` else 403 "That request is not allowed." `publish()` also writes `panel-settings.json` when `panels` is set. Existing tests build `ArtAdmin` without the new args and must still pass.
- [ ] **Step 4: Run** the full file `uv run pytest tests/python/test_artadmin.py -q`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(panel): guarded panel settings API and published document`.

### Task 3: nginx locations, unit arguments, vars and verify check

**Files:**
- Modify: `roles/art_share/files/art.conf`, `roles/art_share/templates/snaphouse-artadmin.service.j2`, `roles/art_share/files/verify.d/artadmin.sh`, `vars/all.yml`
- Test: `tests/python/test_artadmin_role.py`, `tests/python/test_hosts.py` (only if a var shape is pinned there)

**Interfaces:**
- Consumes: CLI flags from Task 2.
- Produces: `panel_screensaver.brightness_pct: 40` in `vars/all.yml`; `backlight_local_port: 8784` in `vars/all.yml`; nginx `location /api/panel/` (proxy to 8781, `proxy_set_header X-Snaphouse-Origin $snaphouse_origin;`, `client_max_body_size 64k`) and `location = /panel-settings.json` (root `/var/lib/snaphouse/public/art`, `application/json`, `Cache-Control no-cache`).

- [ ] **Step 1: Write failing tests**: `test_nginx_proxies_panel_api_with_the_origin_header`, `test_nginx_serves_panel_settings_exact_match_no_cache`, `test_unit_passes_a_panel_id_per_inventory_panel` (hosts whose roles include `panel`), `test_unit_passes_the_panel_defaults_from_vars` (rendered with `trim_blocks=True` and `panel_backlight`, `panel_screensaver` in scope), `test_unit_renders_one_line_for_exec_start`, `test_hosts_with_art_share_also_get_the_origin_map` (any host with `art_share` also has `homebody_bootstrap`, which defines `$snaphouse_origin`), `test_verify_check_probes_panel_settings_json`, `test_screensaver_brightness_default_is_40`.
- [ ] **Step 2: Run** `uv run pytest tests/python/test_artadmin_role.py -q`. Expected: FAIL on the new assertions only.
- [ ] **Step 3: Implement** the conf blocks, the unit flags (panel loop in the same style as the frame loop, defaults inline with `{{ 'on' if panel_screensaver.enabled else 'off' }}`), `--panel-state /var/lib/snaphouse/artadmin/panel-settings.json`, the vars, and a verify probe `jq -e '.version == 1 and (.panels | type == "object")'`.
- [ ] **Step 4: Run** `uv run pytest tests/python/test_artadmin_role.py tests/python/test_update_prune.py tests/python/test_hosts.py -q`; and `ansible-playbook --syntax-check site.yml`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(panel): serve and proxy panel settings on the hub`.

### Task 4: Panel role templates (kiosk URL and backlight unit)

**Files:**
- Modify: `roles/panel/templates/snaphouse-kiosk.env.j2`, `roles/panel/templates/snaphouse-backlight.service.j2`
- Test: `tests/python/test_panel_role.py`

**Interfaces:**
- Consumes: `panel_screensaver.brightness_pct`, `backlight_local_port`, `snaphouse_host`.
- Produces: kiosk URL always carries `panel=1&pid=<host>` plus `idle`, `nidle` (both `panel_screensaver.day_idle_s`, the single wait), `qs`, `qe`, and `ss=0` when the screensaver is off; backlight `ExecStart` passes `--panel-id`, `--settings-url ${SNAPHOUSE_HUB_ORIGIN}/panel-settings.json`, `--local-port`, `--allow-origin ${SNAPHOUSE_HUB_ORIGIN}`, `--screensaver on|off`, `--screensaver-pct`, `--dim-timeout` from `day_idle_s`, and `--force-grab` only when `grab_wake_tap` is true. `--no-grab` is no longer passed by the role.

- [ ] **Step 1: Update and add failing tests** in `test_panel_role.py`: replace `test_backlight_unit_drops_the_grab_when_the_screensaver_handles_the_tap` and `..._keeps_the_grab_...` with `test_backlight_unit_has_no_no_grab_flag_by_default`, `test_backlight_unit_forces_the_grab_only_when_grab_wake_tap_is_set`; add `test_kiosk_url_carries_the_panel_id`, `test_kiosk_url_is_panel_mode_even_when_the_screensaver_is_off` (replaces `..._unchanged_when_the_screensaver_is_off`, `ss=0` present), `test_kiosk_url_wait_replaces_idle_and_nidle`, `test_backlight_unit_passes_settings_url_panel_id_port_and_origin`, `test_backlight_unit_passes_the_screensaver_defaults`, `test_backlight_exec_start_is_one_line`.
- [ ] **Step 2: Run** `uv run pytest tests/python/test_panel_role.py -q`. Expected: FAIL on the changed tests only.
- [ ] **Step 3: Implement** both templates using inline expressions only (no line ends in a block tag).
- [ ] **Step 4: Run** the same command. Expected: PASS.
- [ ] **Step 5: Commit** `feat(panel): pass panel id and settings flags to the kiosk and backlight`.

### Task 5: Daemon settings model, combined rule and grab rule

**Files:**
- Modify: `lib/snaphouse/backlight.py` (`Schedule` becomes `Settings`; `target_pct`, `is_dim`, `grab_wanted`, `apply_once`, `_build_schedule`, `_parse_args`, `run`)
- Test: `tests/python/test_backlight.py`

**Interfaces:**
- Produces: `Settings(quiet_start: time, quiet_end: time, full_pct: int, dim_pct: int, wait_s: float, screensaver_on: bool, screensaver_pct: int)` (frozen dataclass) with `in_quiet_hours(now) -> bool`, `target_pct(now, idle_s, music=False, preview_pct=None) -> int`, `night_dimmed(now, idle_s, music) -> bool`; `parse_settings_entry(entry: dict, fallback: Settings) -> Settings` (invalid keys fall back per key); `grab_wanted(*, dimmed: bool, screensaver_on: bool, force_grab: bool, no_grab: bool) -> bool`; new flags `--screensaver`, `--screensaver-pct`, `--force-grab`, `--panel-id`.

- [ ] **Step 1: Write failing tests** (replace the old Schedule tests that encode the old music rule): `test_before_the_wait_the_level_is_brightness_day_and_night`, `test_day_with_screensaver_on_dims_to_screensaver_pct_after_the_wait`, `test_day_with_screensaver_off_never_dims`, `test_night_no_music_dims_to_night_dim_either_way`, `test_music_with_screensaver_on_uses_screensaver_pct_by_day_and_night`, `test_music_with_screensaver_off_uses_brightness_and_skips_night_dim`, `test_preview_pct_wins_over_everything`, `test_grab_only_when_night_dimmed_with_screensaver_off`, `test_grab_never_with_screensaver_on_unless_forced`, `test_force_grab_grabs_when_night_dimmed_with_screensaver_on`, `test_no_grab_flag_never_grabs`, `test_no_grab_when_night_dim_equals_brightness`, `test_night_dim_zero_writes_zero_and_a_touch_wakes`, `test_parse_settings_entry_keeps_valid_keys_and_falls_back_per_key`, `test_parse_settings_entry_rejects_garbage_types_and_equal_times`, `test_music_keeps_the_screen_at_screensaver_brightness_in_quiet_hours` (replaces the old full-brightness test).
- [ ] **Step 2: Run** `uv run pytest tests/python/test_backlight.py -q`. Expected: FAIL (`Settings` missing, old assertions contradicting the new rule).
- [ ] **Step 3: Implement** per the combined rule in Global Constraints. `run` builds the fallback `Settings` from flags and passes `dimmed`, `screensaver_on` to `grab_wanted`.
- [ ] **Step 4: Run** the same command. Expected: PASS (all remaining old tests green; edit them only where they encode the replaced rule).
- [ ] **Step 5: Commit** `feat(panel): backlight decides from per-panel settings and the combined rule`.

### Task 6: Daemon settings watch, cache, shared poll thread and local API

**Files:**
- Modify: `lib/snaphouse/backlight.py`
- Test: `tests/python/test_backlight.py`

**Interfaces:**
- Consumes: `Settings`, `parse_settings_entry` from Task 5; `MusicWatch`.
- Produces: `SettingsWatch(fetch, *, panel_id, fallback: Settings, cache_path: Path | None)` with `.poll()`, `.current -> Settings`; `PreviewState(clock)` with `.set(pct, seconds)` (clamped to 15 s, 0 cancels) and `.active_pct() -> int | None`; `make_local_server(preview, wake, allow_origin, port) -> ThreadingHTTPServer` on `127.0.0.1`; `poll_loop(pollers, interval, wake)`; flags `--settings-url`, `--settings-cache` (default `/var/lib/snaphouse/backlight/settings.json`), `--local-port`, `--allow-origin`.

- [ ] **Step 1: Write failing tests**: `test_settings_watch_applies_a_good_document`, `test_settings_watch_writes_the_cache_only_after_a_good_read`, `test_settings_watch_starts_from_the_cache_when_the_hub_is_down`, `test_settings_watch_keeps_last_good_on_a_failed_or_garbage_read`, `test_settings_watch_ignores_a_corrupt_cache`, `test_settings_watch_with_no_entry_for_the_panel_keeps_fallback`, `test_cache_write_is_atomic_and_failure_is_tolerated`, `test_preview_expires_and_caps_at_15_seconds`, `test_preview_zero_seconds_cancels`, `test_local_api_binds_loopback_only`, `test_local_api_preview_sets_the_preview` (real server on port 0), `test_local_api_refresh_sets_the_wake_event`, `test_local_api_preflight_allows_only_the_hub_origin_with_private_network_header`, `test_local_api_refuses_a_foreign_origin_with_403`, `test_local_api_rejects_bad_bodies_with_400` (pct 101, seconds -1, junk, oversize), `test_poll_loop_polls_music_and_settings_together_and_wakes_on_refresh`.
- [ ] **Step 2: Run** `uv run pytest tests/python/test_backlight.py -q -k "settings_watch or preview or local_api or poll_loop"`. Expected: FAIL (names missing).
- [ ] **Step 3: Implement.** The preflight answers `Access-Control-Allow-Origin: <hub origin>`, `Access-Control-Allow-Private-Network: true`, `Access-Control-Allow-Methods: POST, OPTIONS`, `Access-Control-Allow-Headers: Content-Type`. A request with no `Origin` header (local tools) is allowed; a different `Origin` is 403. `run` applies `preview_pct` from `PreviewState`, wakes its input wait through the `wake` event, and starts the local server only when `--local-port` is given.
- [ ] **Step 4: Run** `uv run pytest tests/python/test_backlight.py -q`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(panel): backlight reads settings live, caches them, and answers a local preview API`.

### Task 7: Page model and More tiles (pure)

**Files:**
- Create: `roles/room_picker/files/site/panel-settings-model.js`
- Modify: `panel-model.js` (`SUB_SCREENS` gains `screen`, `screensaver`; `present` gets `settings`), `panel-gallery-model.js` (`moreTiles` gains `settings` input), `screensaver-model.js`
- Test: `tests/js/panel-settings-model.test.js`, `panel-model.test.js`, `panel-gallery-model.test.js`, `screensaver-model.test.js`

**Interfaces:**
- Produces in `panel-settings-model.js`: `SETTINGS_DEFAULTS`, `WAIT_CHOICES` (`[30, 60, 120, 300, 600]`), `parsePanelSettings(doc, pid, fallback) -> settings | null` (null for a malformed document or no entry; invalid keys fall back per key), `settingsFromParams(params) -> settings`, `checkPatch(current, patch) -> { ok, patch, error }`, `stepPct(value, dir, min) -> number` (steps of 5, clamped), `pctFromFraction(fraction, min) -> number`, `stepTime(value, dir, other) -> "HH:MM"` (30 min steps, wraps, skips `other`), `timeLabel(hhmm) -> "10:00 PM"`, `waitLabel(s) -> "30 s" | "1 min" | ...`, `diffPatch(current, next)`, `tileLines(settings) -> { screen: "Brightness, night", saver: "Aurora after 1 min" | "Off" }`.
- `moreTiles({frames, pictureCount, troubled, settings})`: `settings` is `null` (tiles omitted) or `{ screenLine, saverLine }` adding `{id: "screen", hash: "#screen", title: "Screen"}` and `{id: "screensaver", hash: "#screensaver", title: "Screensaver"}` after House health.
- `resolvePanelScreen(hash, visible)` accepts `#screen` and `#screensaver` when `visible.settings` is true; `railActive` for them is `more`; they are sub-screens (back to More).
- `screensaver-model.js`: `paramsFromSettings(settings, base) -> params` (day and night wait both `screensaver_wait_s * 1000`, quiet hours from the settings, `enabled`, `style`); `parsePanelParams` adds `enabled` (false when `ss=0`); `shouldShow` returns false when `enabled` is false.

- [ ] **Step 1: Write failing tests** with names matching the interface list: e.g. `parsePanelSettings reads the pid entry`, `... returns null for a bad document`, `... falls back per key`, `stepPct steps by 5 and clamps at the floor (20, 0, 5)`, `stepTime moves 30 minutes, wraps midnight and skips the other time`, `timeLabel formats 12-hour`, `waitLabel`, `checkPatch refuses equal times and off-step values`, `tileLines`, `moreTiles adds Screen and Screensaver only with settings`, `resolvePanelScreen routes #screen and #screensaver only when settings are visible`, `shouldShow is false when the screensaver is off`, `paramsFromSettings uses one wait for day and night`, `parsePanelParams reads ss=0`.
- [ ] **Step 2: Run** `node --test tests/js/panel-settings-model.test.js tests/js/panel-model.test.js tests/js/panel-gallery-model.test.js tests/js/screensaver-model.test.js`. Expected: FAIL (module missing, assertions).
- [ ] **Step 3: Implement** the module and the three small edits.
- [ ] **Step 4: Run** the same command. Expected: PASS.
- [ ] **Step 5: Commit** `feat(panel): pure model for the Screen and Screensaver settings`.

### Task 8: Controller, screens, CSS and screensaver wiring

**Files:**
- Create: `roles/room_picker/files/site/panel-settings-controller.js`, `roles/room_picker/files/site/panel-settings.js`
- Modify: `panel.js` (create the controller from `host`, register the two screens, report the More tile lines), `panel-more.js` (icons for the two tiles), `panel.css` (rows 72 px, brightness bar with minus and plus, time stepper, chips, veil, dashed "More soon" chip, all under `html[data-panel]`), `screensaver.js` (`setSettings`), `rooms.js` (host `settingsChanged`, pass `location.search` fallbacks)
- Test: `tests/js/panel-settings-controller.test.js`, `tests/js/screensaver.test.js`, `tests/js/panel-css.test.js`, `tests/js/rooms-panel-wiring.test.js`

**Interfaces:**
- Consumes: Task 7 model; `createAutosave` from `autosave-queue.js`; `host.timers`; `host.settingsChanged(settings)`.
- Produces: `createPanelSettings({ pid, fallback, fetchJson, postJson, localCall, timers, onChange, onSaveState }) -> { settings(), available(), edit(patch), retry(), previewBrightness(pct), previewScreensaver(on), refresh() }`. `edit` applies the change locally at once, queues one write (`{ delayMs: 0 }`), then calls `localCall("/refresh")` after a good write. `previewBrightness(pct)` calls `localCall("/preview", { pct, seconds: 3 })`. `previewScreensaver(true)` calls `/preview` with the screensaver pct and 15 s and shows the overlay; `false` cancels. The document is read at load and every 30 s through `page-timers.js` and never overwrites a queue that is pending, saving or failed. `screensaver.setSettings(settings)` updates the wait, quiet hours, enabled and style live; turning it off while up dismisses the overlay without the Now playing jump; Style `aurora` is the only value.

- [ ] **Step 1: Write failing tests**: controller: `reads its own entry by pid and applies it`, `falls back to the URL values when the document is missing or malformed`, `has no pid so is unavailable`, `edit applies locally and posts only the changed keys`, `repeated steps merge, latest wins, one write in flight`, `a refused write (400) rolls back to the hub value and reports failed`, `a failed local call is ignored`, `previewBrightness sends pct and 3 seconds`, `previewScreensaver holds then cancels`, `polling skips a queue that is pending`; screensaver: `setSettings changes the wait live`, `setSettings off hides an up overlay and never shows again`, `off means the ambient view never shows either`; css: `settings rows are 72px and nothing is under 20px`, `every new rule is under html[data-panel]`; wiring: `rooms.js loads no panel-settings module statically` (dynamic path only through `panel.js`).
- [ ] **Step 2: Run** `node --test tests/js/panel-settings-controller.test.js tests/js/screensaver.test.js tests/js/panel-css.test.js tests/js/rooms-panel-wiring.test.js`. Expected: FAIL for the new tests only.
- [ ] **Step 3: Implement** the controller and the screens: Screen page rows Brightness (bar with minus, plus and tap or drag on the bar, floor 20, taps preview 3 s), Night dim (floor 0), Night starts, Night ends (steppers with the time label); Screensaver page rows Screensaver On or Off chips plus Preview, Starts after chips, Style (Aurora chip and a dashed disabled "More soon"), Brightness bar (floor 5). When Off, the other rows dim and Preview is disabled. "Saved" and "Couldn't save. Retry" use `saveStatus`. A "Preview. Tap to end." overlay ends on tap or after 15 s.
- [ ] **Step 4: Run** `node --test tests/js`. Expected: PASS (whole suite).
- [ ] **Step 5: Commit** `feat(panel): Screen and Screensaver pages with autosave and live preview`.

### Task 9: Docs

**Files:**
- Modify: `AGENTS.md`
- Test: `tests/js/docs-contract.test.js` (run only), and a grep for em dashes

- [ ] **Step 1: Write** a "Panel settings" section (document and key names, `POST /api/panel/settings` and the origin rule, per-panel storage and defaults from vars, the page's read-and-autosave behavior, the daemon's settings URL, cache, local API on `127.0.0.1:8784`, CORS and Private Network Access, the preview, the combined rule, the grab rule). Update the Panel layout section (More is a 2 by 3 grid with Screen and Screensaver) and the Panel Now playing section (`MusicWatch` now selects screensaver brightness at night, not full) and the Panel screensaver section (wait comes from settings, URL values are the fallback, `ss=0`, no `--no-grab` from the role).
- [ ] **Step 2: Run** `grep -rn $'\xe2\x80\x94' AGENTS.md docs/superpowers/plans/2026-11-23-panel-settings.md` (expect no output) and `node --test tests/js/docs-contract.test.js` (expect PASS).
- [ ] **Step 3: Commit** `docs(panel): document panel settings`.

### Task 10: Full checks, push and PR

- [ ] **Step 1:** `node --test tests/js`, `uv run pytest tests/python`, `ansible-playbook --syntax-check site.yml`, `shellcheck roles/art_share/files/verify.d/artadmin.sh`, `ansible-lint` if installed. Expected: all pass.
- [ ] **Step 2:** `git push -u origin feat/panel-settings`; `gh pr create` titled `feat(panel): Screen and Screensaver settings on the panel` against `main`; `gh pr checks` until green.

## Self-review

- Spec coverage: tiles and pages (7, 8), autosave and "Saved" (8), live apply and previews (6, 8), per-panel storage and defaults from vars (1, 2, 3), origin rule (2, 3), URL fallback (4, 7), combined rule and music-at-night change (5), grab rule (5, 4), local API with CORS and PNA (6), docs (9).
- Calls made where the spec left room: key names, port 8784, local API paths, `--panel` list in the same style as the frame list, tiles hidden when no `pid`, a single `--force-grab` flag replacing the role's `--no-grab`, daemon polls settings and now-playing from one shared thread.
