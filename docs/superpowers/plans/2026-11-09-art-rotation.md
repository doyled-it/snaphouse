# Art Rotation Control Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Manage the art library and control the frame's rotation from the room picker: upload, delete to trash, folders, next/previous/show-now, and the interval.

**Architecture:** A hub library module and a small local HTTP admin service own the library at `/srv/snaphouse/art` and publish `/art-index.json`, `/art-control.json`, and thumbnails; nginx proxies `/api/art/`. The room picker drives the API. The frame syncs from the index and follows the control file with a persisted rotation state machine.

**Tech Stack:** Python 3 stdlib (`http.server`, `email` multipart parsing or a small hand parser) plus Pillow (hub extra) in `snaphouse`; ES modules with `node --test` for the page; Python, httpx, Pillow in `nightstand`; Ansible; uv.

**Spec:** `docs/superpowers/specs/2026-11-09-art-rotation-design.md` (snaphouse). Its JSON shapes, API table, and rotation rules are binding.

## Global Constraints

- No em dashes anywhere: code, comments, docs, commit messages, UI text.
- No identifying data in either repo or any request; uploaded images are stripped of all EXIF and metadata before they are stored.
- uv only for Python packages; the hub gains no new runtime dependency (stdlib plus the existing `hub` extra, Pillow).
- TDD: failing test first; focused tests while iterating; the full suite before each commit.
- Every API write requires the header `X-Snaphouse: 1` (403 otherwise). Upload limit 25 MB per file, enforced by nginx and the service.
- Allowed intervals (minutes): 15, 30, 60, 120, 180, 360, 720, 1440. Default 60. Back stack 20. Trash retention 30 days. Thumbnails 320 px long side. Rescan every 60 s. Landscape rule: aspect at least `art.min_aspect` (1.3) on the frame.
- Piece ids are paths relative to the library root, one folder level at most; every id and folder name is validated so nothing can escape the root.
- snaphouse checks: `uv run pytest tests/python`, `node --test tests/js`, `ansible-playbook --syntax-check site.yml`, `ansible-lint`, `shellcheck`. nightstand: `uv run pytest`.
- Each task is its own branch and PR, CI green before merge.

## Review Focus

1. Path traversal and odd names: ids like `../etc/passwd`, `Photos/../../x`, absolute paths, `.trash/...` passed to move or show, folder names with slashes, dots, or unicode lookalikes. All rejected with 400, nothing touched. Pinned in Task 1 (library) and Task 2 (HTTP).
2. Uploads that are not images, are truncated, are huge (decompression bombs), or carry GPS EXIF: rejected or stripped; nothing half-written left in the library. Pinned in Task 1.
3. SMB edits racing the service (a file added or removed by hand between scans): the index catches up on the next rescan and no request crashes on a vanished file. Pinned in Task 1.
4. A frame restarting mid-rotation, or seeing an old command at startup: it resumes its current piece and does not replay the command. Pinned in Task 4.
5. All enabled folders empty or all portrait: the frame shows the placeholder, the page says nothing is in rotation, nothing crashes. Pinned in Tasks 3 and 4.

---

### Task 1: Library core (snaphouse)

**Files:**
- Create: `lib/snaphouse/artlib.py`
- Test: `tests/python/test_artlib.py`

**Interfaces (Produces):**
- `class Library(root: Path, thumbs: Path, state: Path, *, clock=time.time)` with:
  - `scan() -> dict` (the `/art-index.json` document per the spec, thumbnails created or refreshed, stale thumbnails pruned, trash older than 30 days purged)
  - `control() -> dict` (the `/art-control.json` document) backed by `state` (the persisted settings and last command)
  - `upload(filename: str, data: bytes, folder: str, title: str | None, artist: str | None) -> str` returning the new id; raises `ArtError(status, message)`
  - `delete(id) -> str` (trash id), `restore(trash_id) -> str`, `move(id, folder) -> str`, `create_folder(name)`, `rename_folder(old, new)`, `delete_folder(name)`, `set_folder_enabled(name, enabled)`, `set_interval(minutes)`, `command(action, piece=None) -> dict`
  - `class ArtError(Exception)` with `.status` (400, 404, 409, 413) and a page-ready message.
- Rules from the spec: layout, "Collection" for top-level files, one folder level, sidecars move with images, sanitized upload names, re-encode and strip metadata (apply EXIF orientation first), Pillow `MAX_IMAGE_PIXELS` guard against decompression bombs, atomic writes (temp file plus `os.replace`), rename carries enabled state, show refuses portrait pieces (aspect below 1.3) with 409, `seq` increments by one per command.

- [ ] **Step 1: Failing tests:** scan of a fixture tree (top-level, a folder, a nested folder ignored, sidecars, a corrupt image skipped, `.trash` excluded from pieces); every id/folder validation case in Review Focus 1; upload of a JPEG with GPS EXIF comes back without EXIF and correctly oriented; non-image, truncated, oversized, and bomb uploads raise and leave no file; delete then restore round trip including sidecar and a name collision on restore; move with sidecar; folder create/rename/delete-nonempty (409)/delete-empty; rename carries enabled; interval accepts only the allowed set; command seq increments and show on portrait is 409; trash purge at 30 days with an injected clock; a file deleted between listing and thumbnailing does not raise (Review Focus 3).
- [ ] **Step 2: Run** `uv run pytest tests/python/test_artlib.py -v`, expect FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the full suite, expect PASS.
- [ ] **Step 5: Commit.**

### Task 2: Admin service, deployment, and contracts (snaphouse)

**Files:**
- Create: `lib/snaphouse/artadmin.py`, `roles/art_share/templates/snaphouse-artadmin.service.j2`, `roles/art_share/files/verify.d/artadmin.sh`
- Modify: `roles/art_share/tasks/main.yml`, `roles/art_share/files/art.conf` (proxy `/api/art/` to 127.0.0.1:8781 with `client_max_body_size 26m`; exact-match locations for `/art-index.json`, `/art-control.json`; `/art-thumbs/` served from the public root), `docs/cross-repo-features.md` (both documents as pinned contracts), `AGENTS.md`, `README.md`
- Test: `tests/python/test_artadmin.py`, plus role tests as the existing ones require

**Interfaces:**
- Consumes Task 1's `Library`.
- Produces `python -m snaphouse.artadmin --root /srv/snaphouse/art --public-root /var/lib/snaphouse/public --state /var/lib/snaphouse/art-control.json --port 8781`, binding 127.0.0.1 only, `ThreadingHTTPServer` with one lock around library writes, the API table from the spec, the header guard, JSON errors, a 60 s background rescan, and writing both public documents atomically after every change. The unit runs as `art` with `ProtectSystem=strict`, `ReadWritePaths` limited to the library, the public files and thumbs, and the state file, `NoNewPrivileges=yes`, `PrivateTmp=yes`. The public files and thumbs must be readable by nginx; check how the existing public root is owned (the now-playing service also writes there) and grant the narrowest access.
- Idempotency: a second apply reports changed=0 (CI's art-share job enforces it).

- [ ] **Step 1: Failing tests:** an in-process server on an ephemeral port: each endpoint's happy path and error codes; missing `X-Snaphouse` gives 403 on every POST; GET on a POST route gives 405; a 26 MB upload gives 413; multipart with several files; traversal ids give 400 (Review Focus 1); both public documents rewritten after a change.
- [ ] **Step 2: Run**, expect FAIL.
- [ ] **Step 3: Implement** the service, unit, role tasks, nginx config, verify check (unit active, `/art-index.json` parses), and docs.
- [ ] **Step 4: Run** pytest, ansible syntax-check, ansible-lint, shellcheck. CI runs the container apply and the art-share idempotency gate.
- [ ] **Step 5: Commit.** After merge the controller applies on the hub and checks the files and one API call.

### Task 3: Room-picker art manager (snaphouse)

**Files:**
- Modify: `roles/room_picker/files/site/rooms-model.js`, `rooms.js`, `index.html`, `style.css`
- Test: `tests/js/art-manager.test.js`

**Interfaces:**
- Consumes the two documents and the API.
- Produces pure, tested model functions: `parseArtIndex(raw)`, `parseArtControl(raw)`, `piecesInFolder(index, folder)`, `rotationSummary(index, control)` (how many pieces rotate; "nothing in rotation" when zero, Review Focus 5), `intervalOptions()` (labels for the allowed set), and `apiRequest(path, body)` building a `fetch` init that always carries `X-Snaphouse: 1` and JSON (multipart for uploads).
- UI per the spec's "Room picker: the art manager": now showing with Back/Next and the redraw note; interval selector; folder chips with counts and toggles; library grid with per-piece menu (Show now, Move, Delete) and the portrait badge; upload with per-file progress and server error text; folder create/rename/delete; collapsed trash with Restore; the existing art history kept. Refresh both documents after every action and every 30 s. Untrusted strings rendered with `textContent`, image sources only same-origin paths from the documents.

- [ ] **Step 1: Failing node tests** for the model functions, including malformed documents and the empty-rotation summary.
- [ ] **Step 2: Run** `node --test tests/js`, expect FAIL.
- [ ] **Step 3: Implement** model then UI; check by eye in the Orca browser against a local static server with sample documents (never the household hub).
- [ ] **Step 4: Run** `node --test tests/js` and pytest, expect PASS.
- [ ] **Step 5: Commit.**

### Task 4: Frame follows the hub (nightstand)

**Files:**
- Modify: `src/nightstand/sources/artsync.py` (sync from `/art-index.json`), `src/nightstand/art.py` (`ArtLibrary` with folders and a rotation state machine), `src/nightstand/app.py` (read the control file each cycle; commands), `src/nightstand/rules.py` (art key `("art", current_id)`; commands bypass quiet hours), `src/nightstand/config.py`, `src/nightstand/cli.py` (the sync command), `AGENTS.md`
- Create: `src/nightstand/sources/artcontrol.py`, `src/nightstand/rotation.py`
- Test: `tests/test_artsync.py`, `tests/test_rotation.py`, `tests/test_artcontrol.py`, `tests/test_app.py`, `tests/test_rules.py`

**Interfaces:**
- `sync_index(client, index_url: str, base_url: str, dest: Path, enabled: set[str] | None) -> SyncResult`: mirrors pieces of enabled folders (from the control document's `folders_enabled`; all when unknown) with their sidecars, keeping `folder/file` layout, safe-name checked per segment, removes what is no longer listed or enabled; a bad or missing index changes nothing.
- `fetch_control(client, host) -> ArtControl | None` and `ArtControl(rotate_minutes: int, folders_enabled: tuple[str, ...], seq: int | None, action: str | None, piece: str | None)`.
- `class Rotation(state_path: Path, clock)` with `current(eligible: list[ArtPiece], now, interval_minutes) -> ArtPiece | None` and `apply(command: ArtControl, eligible, now) -> bool` (True when it changed the current piece). Persisted JSON state: `current_id`, `shown_at`, `last_seq`, `back` (at most 20 ids). Rules from the spec: advance when `interval` has passed since `shown_at`; daily reshuffle order; next, previous (back stack), show (stays one interval); first sight of a command at startup only records its seq (Review Focus 4); an unknown or ineligible piece id is ignored; no eligible pieces gives `None` (Review Focus 5).
- `ArtPiece` gains `id: str` (path relative to the art dir).
- Commands: when `apply` returns True, the next push happens immediately, even in quiet hours (`should_push` gets a flag or the decision carries `forced=True`); scheduled rotation keeps the quiet-hours rule.

- [ ] **Step 1: Failing tests:** sync from a fixture index (folders, disabled folder removed, sidecar, traversal names refused, bad index changes nothing); rotation advances at the interval measured from the last change; next, previous across the back stack, show then resume after one interval; restart resumes from state; startup with an existing command does not act; ineligible show ignored; empty eligible gives None; command push bypasses quiet hours while scheduled rotation does not; control fallback to local config when the hub file is missing.
- [ ] **Step 2: Run** `uv run pytest`, expect FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `uv run pytest`, expect PASS.
- [ ] **Step 5: Commit.** After merge the frame self-updates; the controller checks a Next from the page reaches the panel.
