# Frame Control and Screens Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Hub-owned frame settings and pinning, a frame that follows them (now-playing kinds, quiet hours, morning weather, pins, whole covers), and a room picker split into Listen, Frame, Art, and Settings screens.

**Architecture:** The art admin service gains `/api/frame/*` and publishes `/frame-control.json`; the frame reads it each cycle; the page drives it from new screens.

**Tech Stack:** Python stdlib and Ansible (snaphouse hub), plain ES modules with `node --test` (page), Python with httpx and Pillow (nightstand); uv.

**Spec:** `docs/superpowers/specs/2026-11-10-frame-control-design.md` (snaphouse). Its JSON shape, API table, category rule, and pin semantics are binding.

## Global Constraints

- No em dashes anywhere. No identifying data in repos or requests.
- uv for Python; no new runtime dependency on the hub.
- TDD; full suites before each commit; CI's container-apply and art-share idempotency gates stay green (second apply changed=0); never chown the shared public root.
- Every write needs `X-Snaphouse: 1`. Pin durations: `1h`, `4h`, `morning`, `forever`. Kinds: `music`, `podcast`, `audiobook`; default `["music"]`. Defaults for quiet hours 22:00 to 06:00 and morning windows 06:00 to 08:30 weekdays, 07:00 to 09:00 weekends.
- Each task is its own branch and PR; CI green before merge.

## Review Focus

1. A missing, stale, or malformed `/frame-control.json`: the frame keeps working on local config and no pin; the page shows settings as unknown rather than wrong.
2. A pin set during quiet hours redraws exactly once; an expiring pin never causes a redraw storm; pin plus a live game: the game wins.
3. Category edge cases: no genre and `long_form` is a podcast; an audiobook genre on a short track is an audiobook; music with no genre is music.
4. Pinned art piece deleted from the library while pinned: the frame falls back to the schedule, no crash.
5. Tall, wide, tiny, and huge covers all fit inside the square without crossing the text column.

---

### Task 1: Hub frame settings and pin (snaphouse)

**Files:** `lib/snaphouse/artlib.py` or a new `lib/snaphouse/framecontrol.py` (settings, pin, validation, persistence), `lib/snaphouse/artadmin.py` (routes), the art_share role and nginx (`/frame-control.json` exact-match location served from the service-owned public subdir), `docs/cross-repo-features.md` (pinned contract), tests.

- [ ] Failing tests: defaults; settings subset updates and validation errors (bad times, unknown kinds, morning start not before end); pin for each `what` and duration, `morning` resolving to the next morning-window start for that weekday, `art` capturing the current piece from the republished art history; unpin; `seq` increases; header guard; document published after each change; restart reloads state.
- [ ] Implement; pytest, ansible-lint, syntax-check; commit; PR.

### Task 2: The frame follows frame control (nightstand)

**Files:** `src/nightstand/sources/framecontrol.py` (fetch and parse), `src/nightstand/rules.py` (category, kinds filter, pin decision, quiet hours and morning from settings), `src/nightstand/app.py` (read each cycle, forced redraw on pin `seq` change, weather fetch while pinned, art rotation paused while art is pinned), `src/nightstand/screens/now_playing.py` (whole-cover fit), `AGENTS.md`, tests and goldens.

- [ ] Failing tests for every Review Focus item that lives here (1 to 5), plus: kinds filter for each category, morning weather disabled, quiet hours from the hub, forecast fetched every 30 minutes while weather is pinned, pinned art holds the piece across intervals, `now_playing` pin with nothing playing falls back to art, tall-cover golden.
- [ ] Implement; `uv run pytest` and ruff; commit; PR.

### Task 3: Screens in the room picker (snaphouse)

**REQUIRED SKILL:** invoke `frontend-design:frontend-design` and keep the existing panel-ink visual system.

**Files:** `roles/room_picker/files/site/index.html`, `rooms.js`, `rooms-model.js`, `style.css`, `tests/js/`.

- [ ] Failing node tests for pure helpers: hash routing (`#listen` default, unknown hash falls back), pin summary text ("Pinned: weather until 4:12"), settings form validation mirroring the hub, and the request bodies for the new API.
- [ ] Implement the four screens per the spec, moving existing sections without losing behavior (now playing ticking, rooms, play history and Open in, mirror, Back/Next, piece menu, library paging, folders, upload, trash, art history, 30 s refresh that keeps open menus).
- [ ] Check by eye at 360 and 412 px and a laptop width, light and dark, against a local static server with sample documents (never the household hub); screenshots in the report.
- [ ] `node --test tests/js` and pytest; commit; PR.

## After the tasks (controller)

Deploy hub and frame; confirm `/frame-control.json` defaults; play an audiobook and confirm it does not take the frame; pin the weather for 1 hour from the page and watch one redraw; unpin.
