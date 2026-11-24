# Panel settings design

Date: 2026-11-23. Roadmap item: `panel-settings` (the `devices` epic).
Mockup: `2026-11-23-panel-layout-mockup.html` (More, variant B, and the "More: Screen" pages).

## Why

The owner wants to change the wall panel's screen settings from the panel itself. Today every
one of them lives in `vars/all.yml` and reaches the panel at apply time: the page reads
`idle`, `nidle`, `qs` and `qe` from the kiosk URL once at load, and `snaphouse-backlight`
takes `--full-pct`, `--dim-pct`, `--dim-timeout` and the quiet hours as startup flags. Any
change needs a PR, a self-update and a kiosk restart. The owner also wants to tune the night
dim level in a dark room by eye, which today means editing a value and waiting for a deploy
each time.

A second panel is on the roadmap, so settings are per panel from the start.

## Goals

- Two new More tiles, **Screen** and **Screensaver**, each opening its own page (owner chose
  variant B from the mockup). More becomes a 2 by 3 grid.
- **Screen** page: Brightness, Night dim, Night starts, Night ends.
- **Screensaver** page: On/Off, Preview, Starts after, Style, Brightness.
- Changes save on their own (no Save button) and take effect on the panel without a restart
  or a deploy.
- Tapping or stepping a brightness value shows that brightness on the real backlight for 3 s,
  then returns to normal. Preview shows the chosen screensaver at its brightness until a tap.
- Settings are stored per panel on the hub. The values in `vars/all.yml` become the defaults
  a panel uses until someone changes them on that panel.

## Non-goals

- New screensaver styles (facts, album mosaic, other visuals). That is the separate roadmap
  item `panel-screensavers`. This design only makes the Style picker ready for them: it lists
  Aurora plus a dashed "More soon" placeholder that does nothing.
- Editing panel settings from a phone. The pages exist on the panel only, like the rest of
  the panel layout.
- Changing the music rule. While music plays, or was paused less than 5 minutes ago, the
  screensaver is the ambient Now Playing view and the night dim is skipped, as approved for
  `panel-now-playing`. Style picks what shows otherwise.

## Anti-goals

- A panel that goes dark and cannot be woken, or that stays at full brightness all night
  because the hub was unreachable (the hub reboots nightly).
- A settings write from outside the house: tunnel requests must not change a panel.
- Text that wraps on the panel. Panel minimums hold: text 20 px, targets 72 px.

## The settings

| Setting | Page | Values | Default (from vars) |
|---|---|---|---|
| Brightness | Screen | 20 to 100 %, steps of 5 | `panel_backlight.full_pct` (100) |
| Night dim | Screen | 0 to 100 %, steps of 5 | `panel_backlight.dim_pct` (15) |
| Night starts | Screen | a time, steps of 30 min | `panel_backlight.quiet_start` (22:00) |
| Night ends | Screen | a time, steps of 30 min | `panel_backlight.quiet_end` (06:00) |
| Screensaver | Screensaver | On or Off | `panel_screensaver.enabled` (true) |
| Starts after | Screensaver | 30 s, 1, 2, 5 or 10 min | `panel_screensaver.day_idle_s` (60) |
| Style | Screensaver | `aurora` (more later) | `aurora` |
| Screensaver brightness | Screensaver | 5 to 100 %, steps of 5 | 40 (new var) |

Night starts and Night ends may wrap past midnight, as quiet hours do today. Equal start and
end times are refused (the page's steppers skip over the other value), so night is never
empty or all day. The floors differ on purpose: Brightness stops at 20 so the panel is always
usable when touched, the screensaver can go to 5, and the night dim to 0. Night dim 0 means
the backlight is off until a touch; the wake tap still works because the daemon brightens on
any input.

"Starts after" is one wait for day and night. It replaces both `day_idle_s` (the page's day
idle) and `dim_timeout_s` (the backlight's night dim delay and the page's `nidle`). Both are 60
today, so nothing changes for the owner on day one.

## How the screen behaves (the combined rule)

After "Starts after" with no touch:

| | Music playing or paused under 5 min | No music, day | No music, night |
|---|---|---|---|
| Screensaver on | ambient Now Playing at screensaver brightness | Style at screensaver brightness | Style at night dim |
| Screensaver off | no overlay, Brightness | no overlay, Brightness | no overlay, night dim |

A touch dismisses any overlay and returns the backlight to Brightness. At night, a touch
brightens to Brightness as today and the wait starts again.

Decisions behind the table:

- **Music at night uses screensaver brightness** (owner, 2026-11-23). This replaces the
  `panel-now-playing` rule that kept the backlight at full during music in quiet hours, so
  `MusicWatch` now selects screensaver brightness instead of full. The music rule still decides
  that the night dim is skipped while music plays.
- **Screensaver off means no overlay at all**, music included (my call: an off switch that
  still shows the song view would read as broken). The ambient view and the Aurora both stay
  hidden; the backlight follows the "Screensaver off" row.
- **By day with the screensaver off, the backlight never dims** (as today). With it on, the
  daemon dims to screensaver brightness after the wait by day too, which is new.
- **The daemon and the page each keep their own idle clock** (my call). Both see the same
  touches (the daemon through evdev, the page through pointer events) and the same wait, so
  they agree to within a second; a short mismatch between overlay and brightness is accepted.
  Neither tells the other when the overlay is up, except during Preview, where the page asks
  the daemon to hold screensaver brightness.
- **The wake-tap grab follows the live setting** (my call). The daemon grabs input (the #184
  behaviour, so the waking tap reaches nothing) only while the screen is dimmed with the
  screensaver off; with the screensaver on, it never grabs, so one tap wakes and dismisses the
  overlay as today. `panel_backlight.grab_wake_tap: true` still forces the grab, as before.

## Approaches considered

- **A. Store on the hub, one set per panel (chosen by the owner).** The art admin service
  (`lib/snaphouse/artadmin.py`) gets a guarded write route and publishes a static document
  that nginx serves, the same shape as `/api/frame/settings` and `frame-control.json`. The
  page reads it; the backlight daemon reads it with its now-playing fetch and keeps a local
  copy. Matches every other setting in the house.
- **B. Store on each panel.** A small settings service per panel and a local file for the
  backlight. Never depends on the hub, but adds a service on every panel, makes the
  hub-served page call the panel's own address, and differs from every other setting. The
  local copy in A covers the one thing B does better.
- **C. Keep settings in the repo and write them by PR from the panel.** Rejected: slow, needs
  GitHub credentials on a wall device, and every change is a deploy.

## Design

### Hub: storage and API

- New module `lib/snaphouse/panelsettings.py` (pure validation and storage, like
  `framecontrol.py`), used by `artadmin.py`.
- `POST /api/panel/settings` with `X-Snaphouse: 1` and a JSON body
  `{"panel": "<id>", ...only the keys that changed}`. It validates every value against the
  table above, merges into that panel's stored settings, saves atomically under the art
  service's state directory, republishes the document and returns that panel's full
  settings. Unknown panel ids are refused (404). The known panels come from the inventory:
  hosts whose roles include `panel`, passed to the service the way the frame list is.
- Published document `/panel-settings.json`, served by nginx with `no-cache`:
  `{"version": 1, "panels": {"<id>": {...the full settings, defaults filled in}}}`. A panel with
  no stored values carries the defaults, so a reader never has to know the defaults itself.
- The defaults come from `vars/all.yml` through the art service's config (templated by the
  role), so the repo stays the source of the defaults.
- The nginx location for `/api/panel/` sets and overwrites `X-Snaphouse-Origin`, and the
  service refuses writes whose origin is not `home` (403), following the homebody contract.
  Any device on the home network may change any panel's settings, as with frame settings
  (my call: the LAN is trusted elsewhere in the house, and there is no per-device identity).
- The panel id is the inventory key (`panel` today). The kiosk URL carries it as `&pid=`.

### Panel page

- `panel.js` reads `/panel-settings.json` at load and every 30 s (registered with
  `page-timers.js`), picks its own entry by `pid`, and falls back to the kiosk URL values when
  the document is missing, malformed or has no entry. After its own write it applies the
  reply at once.
- The screensaver takes its wait, on/off and style from those settings instead of the URL
  parameters (`screensaver-model.js` keeps the rules; the URL values become the fallback).
- Two new screens, `#screen` and `#screensaver`, reached from two new More tiles. The tile
  lines: Screen reads "Brightness, night"; Screensaver reads "<Style> after <wait>" or "Off".
- Writes go through one `autosave-queue.js` queue per panel: chips and steps save at once,
  repeated steps merge (latest wins), "Saved" and "Couldn't save. Retry" as elsewhere.
- Rules are pure and tested in a new `panel-settings-model.js` (parsing the document,
  validation mirroring the hub, step and wrap rules, tile lines). `panel-settings.js` only
  draws and wires.

### Backlight daemon and live preview

- `backlight.py` gains a `--settings-url` (`${SNAPHOUSE_HUB_ORIGIN}/panel-settings.json`) and
  `--panel-id`. The thread that reads now-playing every 10 s also reads the settings and
  applies them live: brightness, night dim, quiet hours, the wait and screensaver on/off, and
  the screensaver brightness from the combined rule above.
- It keeps the last good settings in `/var/lib/snaphouse/backlight/settings.json` and starts
  from that copy, so a hub reboot or an unreachable hub never changes the screen. With no copy
  and no hub it uses the startup flags, which stay as today's defaults.
- For the 3 s preview and an immediate apply, the daemon listens on `127.0.0.1` only (a fixed
  port pinned in `vars/all.yml`) with two calls: preview a brightness for N seconds (capped at
  15 s), and "settings changed, read them now". The page calls it after each brightness tap
  and after each successful write. Because the page is served from the hub, the daemon must
  answer the browser's Private Network Access and CORS preflight for the hub origin only. A
  failed local call is ignored by the page; the 10 s read still applies the change.
- Preview of the screensaver: the page shows the chosen style full screen and asks the daemon
  to hold screensaver brightness until the preview ends (tap, or 15 s).

### Removing the URL and flag duplication

`day_idle_s` and `dim_timeout_s` remain only as defaults. The kiosk URL keeps `idle`, `nidle`,
`qs`, `qe` as the fallback; the backlight keeps its flags as the fallback. A new var,
`panel_screensaver.brightness_pct` (40), is the default for screensaver brightness.

## Docs and tests to update

- `AGENTS.md`: new "Panel settings" section (document, API, origin rule, daemon local API and
  cache), and the Panel layout section's More tiles.
- `docs/cross-repo-features.md` only if another repo reads the document (none does today).
- Tests: pytest for `panelsettings.py` validation, the route, the origin refusal and the
  published document; pytest for the daemon's settings application, cache and local API;
  `tests/js` for `panel-settings-model.js`, the More tiles and the CSS sizes; the panel role
  template tests for `pid`, the new flags and the port.

## Left to the builder

- The exact JSON key names inside a panel's entry.
- The local port number and the daemon's local API paths.
- How the inventory list of panels reaches the art service (follow the frame list).
- Copy beyond what the mockup shows, kept short enough not to wrap.
