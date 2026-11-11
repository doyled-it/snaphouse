# Several Frames and a Control Panel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Support any number of named e-ink frames (nightstand, office) with independent rotation and per-frame quiet hours and pins, add a touch control panel host, and a printable Bluetooth antenna perch.

**Architecture:** The hub learns frames from its inventory, republishes each frame's files under `/frames/<id>/`, and scopes frame control and art commands by frame id; frames read their own slice; the page switches between frames; a new `panel` role runs the room picker in a Chromium kiosk under `cage`.

**Tech Stack:** Python stdlib, Ansible, nginx, ES modules with `node --test` (snaphouse); Python, httpx, Pillow (nightstand); `cage` and Chromium on Raspberry Pi OS Lite (panel); OpenSCAD (perch); uv.

**Spec:** `docs/superpowers/specs/2026-11-11-multiframe-panel-design.md` (snaphouse). Its JSON shapes, fallbacks, and the surface table are binding.

## Global Constraints

- No em dashes anywhere. No identifying data in repos or requests (no real IPs, MACs, SSIDs, coordinates; the panel's hub URL comes from untracked local config).
- uv for Python; no new runtime dependency on the hub.
- TDD; full suites before each commit; CI's container-apply and art-share idempotency gates stay green (second apply changed=0); never chown the shared public root; every new nginx route has a test (the frame control rollout missed one).
- Frame ids are inventory keys matching `^[a-z][a-z0-9-]{0,31}$`. Every write needs `X-Snaphouse: 1`.
- Old single-frame paths (`/art-history.json`, `/frame.png`, `/frame.json`) keep serving the nightstand's copies for this release.
- Each task is its own branch and PR; CI green before merge.

## Review Focus

1. A frame that is offline or not yet installed (the office before the Inky arrives): the hub skips it without failing, the page shows it as offline, and the other frame is unaffected.
2. An art command or pin aimed at one frame never moves the other (and a command with no frame still reaches both, for compatibility).
3. A page loaded on the old single-frame contract during rollout (cached JS, or hub updated before frames): nothing crashes; the nightstand keeps working.
4. The panel after a power cut: boots straight into the kiosk with the room picker even if the hub comes up later (retries, no blank screen forever), and the backlight wakes on touch.
5. Unknown or malformed frame ids in any request: 404 or 400, never a write to an arbitrary path.

---

### Task 1: Hub frame inventory and per-frame republish (snaphouse)

**Files:** `vars/hosts.yml` (office gains `nightstand_bootstrap`), `roles/room_picker/templates/house-status-targets.json.j2` (a `frames` list from hosts with `nightstand_bootstrap`), `lib/snaphouse/housestatus.py` (republish each frame's art history and mirror to `/var/lib/snaphouse/public/<service-owned subdir>/frames/<id>/`, keep the old nightstand paths), `roles/room_picker/templates/room_picker.conf.j2` (nginx `location /frames/` serving that subdir with `Cache-Control: no-cache` for mirror files), `docs/cross-repo-features.md` (Art history, Frame mirror), tests.

**Interfaces:** Produces `/frames/<id>/art-history.json`, `/frames/<id>/frame.png`, `/frames/<id>/frame.json`; the targets file gains `"frames": [{"id": "<key>", "base": "http://<key>.local/nightstand/"}]`.

- [ ] Failing tests: targets template lists exactly the frame hosts; republish per frame with the existing caps and validation; an unreachable frame is skipped and the others still publish (Review Focus 1); ids are validated before any path is built (Review Focus 5); nginx template pins the `/frames/` location and headers; old paths still serve the nightstand copy.
- [ ] Implement; pytest, ansible-lint, syntax-check; commit; PR.

### Task 2: Per-frame frame control and scoped art commands (snaphouse)

**Files:** `lib/snaphouse/framecontrol.py` (`frames` map with per-frame `quiet_hours` and `pin`, frame list from the inventory passed to the service), `lib/snaphouse/artlib.py` (art command `frame`), `lib/snaphouse/artadmin.py` (routes take `frame`), the art_share role (service args with the frame ids), `docs/cross-repo-features.md` (Frame control, Art rotation control), tests.

**Interfaces:** `/frame-control.json` = `{"settings": {...shared...}, "frames": {"<id>": {"quiet_hours": {...}|{"enabled": false}|null, "pin": {...}|null}}}` (top-level `pin` removed); `POST /api/frame/pin {"frame", "what", "duration"}`, `POST /api/frame/unpin {"frame"}`, `POST /api/frame/settings {"frame"?, ...}`; `/art-control.json` command gains `"frame": "<id>"|null`; `POST /api/art/command {"action", "piece"?, "frame"?}`.

- [ ] Failing tests: per-frame pin set and cleared without touching the other frame (Review Focus 2); per-frame quiet hours, `{"enabled": false}`, and null falling back to shared; unknown frame 404, malformed id 400 (Review Focus 5); `art` pin reads that frame's own history; existing state migrates (the old top-level pin becomes the nightstand's); art command with and without `frame`.
- [ ] Implement; pytest; commit; PR.

### Task 3: Frames follow their own slice (nightstand)

**Files:** `src/nightstand/config.py` (`[frame] id`, default hostname, validated), `src/nightstand/sources/framecontrol.py` (read `frames[<id>]`, quiet-hours fallback rules, `enabled: false`), `src/nightstand/sources/artcontrol.py` (apply a command only when `frame` is absent or equals the id), `config.example.toml`, `AGENTS.md`, tests.

**Interfaces:** consumes Task 2's shapes; tolerates the old shape (top-level `pin`) during rollout by treating it as the nightstand's (Review Focus 3).

- [ ] Failing tests: own pin and quiet hours used; another frame's pin ignored; quiet hours off; old-shape document still works for the nightstand id; commands scoped (Review Focus 2).
- [ ] Implement; `uv run pytest` and ruff; commit; PR.

### Task 4: The page across frames, plus the panel layout (snaphouse)

**REQUIRED SKILL:** invoke `frontend-design:frontend-design`; keep the existing panel-ink visual system.

**Files:** `roles/room_picker/files/site/index.html`, `rooms.js`, `rooms-model.js`, `style.css`, `tests/js/`.

**Interfaces:** consumes Tasks 1 and 2. Hash `#frame/<id>` selects a frame (default the first in `frames`).

- [ ] Failing node tests: frame switcher model (list from `frames`, offline flag from a missing mirror), per-frame URLs for mirror and history, request bodies always carry `frame`, hash parsing, old-contract documents (no `frames`) fall back to the nightstand-only view (Review Focus 3).
- [ ] Implement the switcher on the Frame screen, per-frame quiet hours in Settings, and the 800x480 landscape layout (two-column Listen, 48 px targets) by media query.
- [ ] Check by eye at 412 px portrait and 800x480 landscape, light and dark, against a local static server with sample documents (never the household hub); screenshots in the report.
- [ ] `node --test tests/js` and pytest; commit; PR.

### Task 5: The panel host (snaphouse)

**Files:** `vars/hosts.yml` (`panel` host: roles `common`, `web`, `selfupdate`, `panel`), `roles/panel/` (tasks, templates for a `snaphouse-kiosk.service` running `cage -- chromium --kiosk --noerrdialogs --disable-pinch --overscroll-history-navigation=0 <url>` as an unprivileged kiosk user with autologin on tty1, a backlight service implementing the spec's dim/wake schedule, `verify.d/panel.sh`), `local.example.yml` (`panel_url` placeholder), `tests/container/` (container apply includes `panel` with hardware tasks skipped), tests, README section.

**Interfaces:** `panel_url` from untracked local config (default `http://house.local/`); backlight levels and dim timeout in `vars/all.yml`; `panel_rotation` (0, 90, 180, 270; default 0) in `vars/hosts.yml` applied to the display output and the touch input mapping together, so touches land where they look.

- [ ] Failing tests: role tasks and unit templates pinned (Restart=always, After=network-online, a pre-start wait loop for the hub URL with retries, Review Focus 4); verify script checks the kiosk service; backlight schedule logic tested as a small Python module with an injected clock and a fake sysfs.
- [ ] Implement; pytest, ansible-lint, syntax-check, shellcheck; container apply green for `panel`; commit; PR.

### Task 6: Printable Bluetooth antenna perch (snaphouse)

**Files:** `hardware/bt-antenna-perch/perch.scad` (parametric: adapter body size, USB-A plug size, extension cable diameter, mount style wall-screw or shelf-clip, all as top-level variables with defaults for the TP-Link UB500 Plus and a common USB 2.0 extension), `hardware/bt-antenna-perch/perch.stl` (rendered with the defaults), `hardware/bt-antenna-perch/README.md` (print settings for a Bambu Lab printer: PLA or PETG, 0.2 mm layers, no supports by design, how to measure and change the variables).

- [ ] Install OpenSCAD with Homebrew if missing; render the defaults to STL from the command line (`openscad -o perch.stl perch.scad`) and confirm it renders without warnings and is a single manifold body.
- [ ] Look up the UB500 Plus dimensions from TP-Link's published specs; where a dimension is not published, choose a generous default with a clearance variable and say so in the README.
- [ ] Commit the source, STL, and README; PR. The owner's test print is the acceptance check.

## After the tasks (controller, with the owner)

Owner: the Inky on the office Pi 5; the display on the Pi 3; flash the Pi 3 (Raspberry Pi OS Lite 64-bit, hostname `panel`). Controller: deploy hub, install nightstand on the office Pi (frame id `office`), bootstrap `panel`, verify each; then the cross-repo completeness audit.
