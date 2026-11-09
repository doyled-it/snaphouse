# Room Picker Redesign and Art Polish Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A frame-mirror redesign of the room picker with section navigation, a paged library with metadata, deletes from history and the current piece, an "Open in" sheet for play history, and a 1.1 rotation cutoff shared by every frame.

**Architecture:** The frame publishes the image it last pushed and adds ids to its art history; the hub republishes the image and publishes the rotation cutoff; the page is rebuilt in the panel-ink visual system on top of the existing model functions.

**Tech Stack:** Python, Pillow, httpx (nightstand); Python stdlib, Ansible, nginx, plain ES modules with `node --test` (snaphouse); uv.

**Spec:** `docs/superpowers/specs/2026-11-09-art-polish-design.md` (snaphouse). The visual system table, page structure, and data changes are binding.

## Global Constraints

- No em dashes anywhere. No identifying data in repos or requests.
- uv for Python; no new runtime dependencies on the hub; the page loads nothing from outside the LAN (fonts bundled and served by the hub).
- TDD: failing test first; the full suite before each commit.
- Panel inks: Paper `#E9E7E0`, Ink `#2B292A`, Blue `#3C4678`, Red `#AD443C`, Yellow `#CEBC58`, Green `#4A6E4B`. Rotation cutoff default 1.1. Library page size 24. Mirror image 800x480 PNG.
- Keep CI's container-apply and art-share idempotency gates green (a second apply reports changed=0); never chown the shared public root.
- Each task is its own branch and PR; CI green before merge.

## Review Focus

1. The mirror or `frame.json` missing, stale, or malformed: the page hides the mirror or shows "Updated" honestly; nothing breaks.
2. Deleting the piece on the frame: the frame moves to another piece within one cycle (one redraw), and the deleted piece never comes back from a stale state file.
3. Old art-history entries without `id`: delete still works through `file`, and entries in folders are never mis-targeted.
4. Very long titles and artists in tiles, history rows, and the mirror caption on a 360 px phone: no overflow, readable truncation.
5. Dark theme at night: every text and control meets AA contrast, and no pure white glares.

---

### Task 1: The frame publishes its mirror, history ids, and follows the hub cutoff (nightstand)

**Files:** `src/nightstand/app.py` (after a successful push), `src/nightstand/history.py` (entries gain `id`), `src/nightstand/sources/artcontrol.py` (`min_aspect` from `settings`), the eligibility call that uses `art.min_aspect`, `AGENTS.md` (published files), tests.

**Interfaces:** after every successful `display.show`, write `paths.public/frame.png` (the pushed image through `palette.simulate_panel`, 800x480 PNG) and `paths.public/frame.json` (`{"updated_at": iso, "screen": name}`), both atomic and 0644, and never let a write failure stop the loop (log once per outage, like the art history). Art-history entries gain `"id"`. `ArtControl` gains `min_aspect: float | None`; eligibility uses it when present, else `art.min_aspect`.

- [ ] Failing tests: mirror and json written after a push and not after a held cycle; write failure does not raise and logs once; history entry carries `id` (a folder piece too); `min_aspect` from the control file changes eligibility, and a missing value falls back to config.
- [ ] Implement; `uv run pytest` and ruff green; commit; PR.

### Task 2: The hub publishes the cutoff and republishes the mirror (snaphouse)

**Files:** `lib/snaphouse/artlib.py` (settings `min_aspect`, default 1.1, used by the `landscape` flag, published in `/art-control.json`), `lib/snaphouse/housestatus.py` (republish `frame.png` and `frame.json` from the nightstand host same-origin, with the art-history hardening: size cap of 2 MB, PNG signature check, JSON shape check, atomic write), `roles/room_picker/templates/room_picker.conf.j2` (exact-match locations for `/frame.png` and `/frame.json`, served from a subdir the republisher owns), `docs/cross-repo-features.md` (mirror files, history `id`, `settings.min_aspect`), tests.

- [ ] Failing tests: default cutoff 1.1 marks a 1.2 piece landscape and a 1.05 piece not; the control document carries `min_aspect`; the republisher accepts a valid PNG and rejects an oversized or non-PNG body without replacing the last good file; JSON shape checked.
- [ ] Implement; pytest, ansible-lint, syntax-check, shellcheck green; commit; PR. CI's idempotency gate must stay green.

### Task 3: The room picker redesign (snaphouse)

**REQUIRED SKILL:** invoke `frontend-design:frontend-design` before designing, and follow it within the spec's pinned visual system.

**Files:** `roles/room_picker/files/site/index.html`, `rooms.js`, `rooms-model.js`, `style.css`, a bundled OFL font under `site/fonts/` (with its license file), the role task that installs site files if new files need listing, tests in `tests/js/`.

**Interfaces (pure, tested):** `openInLinks(play) -> [{label, url}]` per the spec's per-kind lists (titles and shows URL-encoded; services verified); `pageSlice(pieces, page, size=24)` and `pageCount`; `historyDeleteTarget(entry) -> id` (`id`, else `file` only when it has no folder separator, else null so the button hides); `mirrorState(frameJson, now) -> {visible, updatedLabel}`; theme tokens as CSS custom properties for both schemes.

Page per the spec's "Page structure": top bar with a hamburger that opens a dialog drawer of sections (focus trapped, Escape closes); the frame mirror at 5:3 on a paper mat with caption, Back, Next, and a menu (Show in library, Delete this piece: issues `next` then `delete`, then an "Undo" that restores); now playing; rooms list; collapsed paged library with folder filter and tiles showing thumbnail, title, artist; art history with Delete; play history rows opening the "Open in" sheet; rotation; upload; trash. Keep every existing behavior (rooms, volumes, now-playing ticking, uploads, folder ops, the 30 s refresh that keeps open menus).

- [ ] Write the design token plan from the skill, check it against the spec's principles, then failing node tests for the pure functions.
- [ ] Implement; check by eye at 360 and 412 px wide and a laptop width, in light and dark, against a local static server with sample documents in the Orca browser (never the household hub); screenshots of each state in the report.
- [ ] `node --test tests/js` and pytest green; commit; PR.

## After the tasks (controller)

Deploy hub and frame; move the 83 pieces with aspect below 0.9 to the trash through the API; check the index counts (89 left, 83 landscape at 1.1), the mirror on the page, and one Next.
