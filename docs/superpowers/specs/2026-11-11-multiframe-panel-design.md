# Several frames and a touch control panel: design

Date: 2026-11-11. Repos: `snaphouse` (hub, page, panel) and `nightstand` (frames).

## Why

The owner bought a second Inky Impression 7.3" for the office (on the existing
office Pi 5, which keeps playing audio) and an official 7" Raspberry Pi touch
display (800x480, DSI) with a Pi 3 to use as a wall or counter control panel.
The system assumes one frame in several places: the hub republishes only the
nightstand's art history and mirror, frame settings and pins are global, and art
commands reach every frame.

## Decided by the owner

- Frames rotate art independently: each frame picks its own next piece from the
  shared library.
- Quiet hours and pins are per frame. The art library, folders in rotation, the
  rotation interval, the weather city, and the now-playing kinds stay shared
  (call: kinds were not discussed; shared keeps one place to change them).
- The touch display is a control panel running the room picker, on the Pi 3.
- The office frame runs on the office Pi 5 alongside its audio.

## Frames are named

Each frame has an id: its inventory key (`nightstand`, `office`), carried in the
frame's local config (`[frame] id`, default the hostname). The hub's inventory
marks frame hosts (the hosts whose roles include `nightstand_bootstrap`); the
house-status timer republishes, for every frame host, its `art-history.json`,
`frame.png`, and `frame.json` to `/frames/<id>/...`, with the existing hardening.
The old single paths (`/art-history.json`, `/frame.png`, `/frame.json`) keep
serving the nightstand's copies for one release so nothing breaks mid-rollout
(call), then go away.

## Frame control becomes per frame where it must be

`/frame-control.json` keeps `settings` for the shared values and gains
`frames`: `{"<id>": {"quiet_hours": {...} | null, "pin": {...} | null}}`. A
frame's quiet hours fall back to the shared `settings.quiet_hours` when its own
are null (the office can set none: `{"enabled": false}`), and the top-level
`pin` is removed in favour of per-frame pins. Pin `seq` stays per frame. API:
`/api/frame/pin` and `/api/frame/unpin` take a required `"frame": "<id>"`;
`/api/frame/settings` takes an optional `"frame"` for the per-frame keys
(`quiet_hours`), and the shared keys otherwise. Unknown frame ids get 404.
`art` pins record the piece from that frame's own art history.

`/art-control.json` art commands gain an optional `"frame"`: a command with a
frame id applies only to that frame; with none it applies to every frame
(compatible with today). The page always sends a frame id.

## Frame changes

Read its id; apply the per-frame quiet hours and pin from `frames[<id>]`; apply
art commands only when `frame` is absent or equals its id; everything else as
today. Rotation state is already per device.

## Page changes

The Frame screen gets a frame switcher (Nightstand, Office; from the inventory
list the hub publishes in `/frame-control.json` under `frames`), and its mirror,
caption, history, Back and Next, piece menu, and Pin act on the chosen frame.
Settings shows shared settings plus a per-frame quiet-hours section. The chosen
frame is remembered in the URL hash (`#frame/office`).

Panel layout: at 800x480 landscape the page uses a two-column Listen screen
(now playing beside rooms) with touch targets at least 48 px, chosen by a media
query, not a separate page. The drawer and screens are unchanged.

## The panel host

A new inventory host `panel` (Pi 3, Raspberry Pi OS Lite 64-bit) with roles
`common`, `web`, `selfupdate`, and a new `panel` role:

- A kiosk: `cage` (a single-app Wayland compositor) running Chromium full screen
  on the official display, opening the hub's room picker by its reserved IP
  (`.local` is fine on the LAN, but the IP avoids an mDNS dependency at boot;
  call), restarted automatically if it exits, with no browser chrome, no
  pinch-zoom, and the cursor hidden.
- The display rotated if needed (setting in vars) and the touch input mapped.
- Backlight: dims to a low level during the shared quiet hours and wakes to full
  on touch, then returns to dim after 60 s idle; full brightness otherwise
  (`/sys/class/backlight/*`).
- The panel plays no audio (call; it can join as a speaker later).
- Self-updating and verified like the other hosts; its verify checks that the
  kiosk service is active.

## Rollout

1. Hub: frame inventory, per-frame republish, per-frame frame control and art
   commands, contracts.
2. Frames: frame id, per-frame settings, frame-scoped commands.
3. Page: frame switcher, per-frame settings, panel layout.
4. Panel role and inventory host.
5. Owner hardware: Inky on the office Pi 5; display on the Pi 3; flash and
   bootstrap the Pi 3 as `panel`. Controller: install nightstand on the office
   Pi (frame id `office`), bootstrap the panel, verify.

## Non-goals

Per-frame art libraries or rotation intervals, audio on the panel, the Zero W.

## Surface per repo (cross-repo completeness, design time)

| Slice | snaphouse | nightstand |
|---|---|---|
| Producer and contract | inventory frame list; `/frames/<id>/` republish (housestatus); `/frame-control.json` `frames` map and per-frame pin/quiet hours (framecontrol, artadmin routes); `/art-control.json` command `frame`; nginx locations for `/frames/`; `docs/cross-repo-features.md` (Frame control, Art rotation control, Art history, Frame mirror) | `[frame] id` in local config; frame mirror and art history unchanged in shape (republished per frame by the hub) |
| Consumer, client, types | page model and views: frame switcher, per-frame mirror/history/pin, settings | `sources/framecontrol.py` reads `frames[<id>]`; `sources/artcontrol.py` honours command `frame` |
| UI and docs | Frame and Settings screens, panel layout (frontend-design skill); README room picker section | AGENTS.md (frame id, per-frame settings) |
| Config | `vars/hosts.yml`: office gains `nightstand_bootstrap`; new `panel` host; panel vars (rotation, backlight, kiosk URL) | `config.example.toml` `[frame] id` |
| Deploy | `panel` role (cage, Chromium kiosk, backlight); bootstrap of the Pi 3; verify checks | install on the office Pi 5 via `nightstand_bootstrap`; frame id `office` |
| Tests | pytest (republish per frame, per-frame control, routes, panel role), node (switcher, per-frame views), container apply including `panel` | pytest (per-frame quiet hours and pins, scoped commands) |
| Hardware | printable Bluetooth antenna perch (OpenSCAD source plus STL) in `hardware/` | N/A: no printed parts requested for the frames |
