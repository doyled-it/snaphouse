# Frame change feedback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** After a tap on Next, Back or Show on the frame, the page shows at once that it was sent, then the target picture ("Changing to <title>"), then "Now showing <title>", within seconds of the frame finishing its push.

**Architecture:** The frame (nightstand) adds `pending` and `applied_seq` to `frame.json`. A hub one-shot (`snaphouse-frame-follow.service`, started by a systemd path unit on `art-control.json`) republishes the targeted frame's mirror every 2 s until `applied_seq` reaches the command seq or 90 s pass. The page runs a pure state machine over the republished `frame.json` and polls it every 2 s only while a change is in flight.

**Tech Stack:** Python (uv, pytest, stdlib on the hub), plain ES modules (`node --test`), Ansible, systemd. Two repos: `nightstand` (branch `feat/frame-change-feedback`, worktree `/Users/mdoyle/orca/workspaces/nightstand/frame-feedback`) and `snaphouse` (branch `feat/frame-change-feedback`).

**Spec:** `docs/superpowers/specs/2026-11-23-frame-change-feedback-design.md` ("Calls made after the builder check" is binding and wins). Panel sizes: `docs/superpowers/specs/2026-11-23-panel-layout-design.md`.

## Global Constraints

- No em dashes anywhere (code, comments, docs, UI copy, commits, PR bodies).
- No identifying data in either repo (no MACs, IPs, names, locations).
- TDD: failing test first, keep the RED output for the PR body. Conventional Commits, one commit per task. Commit trailer: `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`.
- `pending` shape: `{"seq", "id", "since", "state", "reason"}`; `state` in `changing | waiting | failed`; `reason` in `music | game | pin | settling` (waiting) or null (changing, failed).
- `applied_seq`: non-negative int, absent until the first command push. Confirmation is by seq only (`applied_seq >= seq`), never by clock comparison.
- Follower: poll every 2 s, window 90 s from the latest command, re-reads `art-control.json` each loop (a new command extends the window), no second copy, `frame.png` republished only when `updated_at` changes. Same hardening as the existing republish (no redirects, size caps, shape checks, atomic writes, last good kept). Runs as the same user as the house-status poller (root).
- Page: `frame.json` read every 2 s during a change, back to `FRAME_MS` (30 s) after; `frame.png` fetched only when `updated_at` changes; "Now showing" for 3 s; 90 s timeout message "The frame did not confirm the change. It may be busy or offline."; a missing `pending`/`applied_seq` (old frame) takes the 90 s path.
- Copy (exact): "Sending to <frame>...", "Changing to <title>", "Up next: <title>. The frame shows <music|the game|the pinned screen> right now.", "The frame could not show it. Try again.", "Now showing <title>". Unknown id in the art index: "the next picture", no thumbnail. UI copy never says snapserver, hub, homebody.
- Panel: text at least 20 px, targets at least 72 px, all rules under `html[data-panel]`.
- Tags: hardware tasks `hardware`, service starts `service`. Verify-check installs use one static `dest: /etc/snaphouse/verify.d/<name>` line.

## Review Focus

- A command for another frame (`frame` set to a different id) must not write `pending` or `applied_seq` on this frame. Test in Task 3.
- A command that the frame replays or ignores (seq not greater than last, startup baseline) must not write `pending`. Test in Task 3.
- `pending` with a bool `seq`, negative `seq`, over-long `since`, or unknown `state`/`reason` is dropped alone, never the whole mirror. Test in Task 5.
- Two quick taps: the second command's seq restarts the flow and the first's confirmation (`applied_seq` equal to the first seq) does not end it. Test in Task 8.
- Mirror write failing (read-only public dir) must not stop the frame loop. Test in Task 2.
- Follower with a missing, malformed or seq-less `art-control.json` exits 0 without polling. Test in Task 6.

---

### Task 1: Contract first (snaphouse docs)

**Files:**
- Modify: `docs/cross-repo-features.md` ("Frame mirror file" section near line 191, plus the art command reply note)
- Test: `tests/js/docs-contract.test.js` (add one assertion that the section names `pending` and `applied_seq`)

**Interfaces:**
- Produces: the written contract every later task cites.

- [ ] **Step 1: Write the failing test** `docs contract: frame mirror file documents pending and applied_seq` asserting the doc text contains `"pending"`, `"applied_seq"`, `changing`, `waiting`, `failed`, and `snaphouse-frame-follow`.
- [ ] **Step 2: Run** `node --test tests/js/docs-contract.test.js`. Expected: FAIL.
- [ ] **Step 3: Edit the doc**: add the two fields with shape, when written, when cleared, the hub validation rule (call 3), the follower (calls 6, 7), and that `POST /api/art/command` already replies `{"command": {"seq", ...}}`.
- [ ] **Step 4: Run it again.** Expected: PASS.
- [ ] **Step 5: Commit** `docs: frame mirror pending and applied_seq contract`.

### Task 2: nightstand mirror writer carries pending and applied_seq

**Files:**
- Modify: `src/nightstand/app.py` (`AppState`, `_write_mirror`, `_write_mirror_files`, new `_write_mirror_meta`)
- Test: `tests/test_app.py`

**Interfaces:**
- Produces: `AppState.applied_seq: int | None`, `AppState.pending: dict | None` (name clash: the existing `state.pending` is something else, check at `app.py:~720`; if so use `state.mirror_pending`). `_write_mirror(state, public, image, screen, now)` now writes `applied_seq` when set and `pending` when set. `_write_pending(state, public, pending: dict | None, now) -> None` rewrites only `frame.json` (keeps the last `updated_at` and `screen` from `state.mirror_meta`), same atomic write and swallow-and-log-once failure handling as `_write_mirror`.

- [ ] **Step 1: Write failing tests**: `test_mirror_json_has_applied_seq_after_command_push`, `test_mirror_json_omits_pending_after_push`, `test_write_pending_keeps_updated_at_and_screen`, `test_write_pending_failure_does_not_raise_and_logs_once`.
- [ ] **Step 2: Run** `uv run pytest tests/test_app.py -k "mirror or pending" -v`. Expected: FAIL.
- [ ] **Step 3: Implement** per Interfaces. Remember last written `updated_at`/`screen` in `state.mirror_meta`.
- [ ] **Step 4: Run again.** Expected: PASS.
- [ ] **Step 5: Commit** `feat(frame): mirror carries pending and applied_seq`.

### Task 3: nightstand derives pending state from a command

**Files:**
- Modify: `src/nightstand/rotation.py` (`apply` reports the executed command), `src/nightstand/app.py` (`ArtView.seq`, `ArtView.piece_id`, `run_once`)
- Test: `tests/test_rotation.py`, `tests/test_app.py`

**Interfaces:**
- Consumes: Task 2 writers.
- Produces: `ArtView` gains `seq: int | None = None` (set only on the cycle a command ran for this frame and changed or re-targeted the piece). In `run_once`, on a cycle with `art_view.seq`: if `decision.screen == "art"` and it will push, write `pending` `state="changing"`, `reason=None` before `display.show`; if `display.show` raises, write `state="failed"` then re-raise as today; after a good push set `applied_seq = seq` and clear pending via the normal mirror write. If `decision.screen != "art"`, or the push is held (settling), write `state="waiting"` with `reason` from `{music, game, pin, settling}` (now playing -> `music`, game -> `game`, active pin -> `pin`, unsettled identity -> `settling`). `id` is the piece id the rotation moved to; `since` is `now.isoformat()` capped to 40 chars.

- [ ] **Step 1: Write failing tests**: `test_next_command_writes_changing_pending_before_push` (fake display records that frame.json had pending at `show` time), `test_pending_cleared_and_applied_seq_set_after_push`, `test_command_while_music_writes_waiting_music`, `test_command_while_pinned_writes_waiting_pin`, `test_command_during_settle_writes_waiting_settling`, `test_push_error_writes_failed_pending`, `test_command_for_other_frame_writes_no_pending` (Review Focus), `test_replayed_or_baseline_command_writes_no_pending` (Review Focus), `test_newer_command_replaces_pending`.
- [ ] **Step 2: Run** `uv run pytest tests/test_rotation.py tests/test_app.py -k "pending or command" -v`. Expected: FAIL.
- [ ] **Step 3: Implement** per Interfaces; `Rotation.apply` keeps returning bool and gains a read-only `last_applied_seq` set only when a command executed this call.
- [ ] **Step 4: Run again, then `uv run pytest tests/test_app.py tests/test_rotation.py`.** Expected: PASS.
- [ ] **Step 5: Commit** `feat(frame): publish pending target when a command applies`.

### Task 4: nightstand docs

**Files:** Modify `AGENTS.md` (frame mirror section: the two fields, when written, the states, that confirmation is by seq), `docs/` contract copy if one exists.

- [ ] **Step 1: Edit** and check with `rg -n "—" AGENTS.md docs` (expect none new).
- [ ] **Step 2: Commit** `docs: pending and applied_seq in the frame mirror`.

### Task 5: hub validator passes pending and applied_seq

**Files:**
- Modify: `lib/snaphouse/housestatus.py` (`select_frame_meta`)
- Test: `tests/python/test_housestatus.py`

**Interfaces:**
- Produces: `select_frame_meta(parsed) -> dict | None` keeps `pending` only when `seq` is a non-negative int (not bool), `id` passes `_safe_piece_id`-style check (reuse the existing piece-id rule; add a small `_valid_piece_id(value)` if `_safe_piece_id` needs a `file`), `since` is a str of at most 40 chars, `state` and `reason` are in the allowed sets (reason may be null only for `changing` and `failed`); otherwise drops `pending` alone. `applied_seq` kept only as a non-negative int. Existing two-field behavior unchanged.

- [ ] **Step 1: Write failing tests**: `test_frame_meta_keeps_valid_pending`, `test_frame_meta_drops_pending_with_bool_seq`, `..._negative_seq`, `..._long_since`, `..._unknown_state`, `..._unknown_reason`, `..._bad_id`, `test_frame_meta_keeps_applied_seq`, `test_frame_meta_drops_bad_applied_seq_only`, `test_frame_meta_drops_unknown_extra_keys`.
- [ ] **Step 2: Run** `uv run pytest tests/python/test_housestatus.py -k frame_meta -v`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run again.** Expected: PASS.
- [ ] **Step 5: Commit** `feat(hub): validate pending and applied_seq in the frame mirror`.

### Task 6: hub follower module

**Files:**
- Create: `lib/snaphouse/framefollow.py`
- Test: `tests/python/test_framefollow.py`

**Interfaces:**
- Consumes: `housestatus.publish_frame_meta`-style hardened fetch (`http_fetch`, `select_frame_meta`, `write_frame_meta`, `fetch_frame_image`, `write_frame_image`, `_valid_frame_id`, `_frame_base`, `_load_config`).
- Produces: `run(control_path: Path, targets_path: Path, frames_root: Path, *, now: Callable[[], float], sleep: Callable[[float], None], fetch=..., window_s=90.0, interval_s=2.0) -> int` and `main(argv) -> int` (module runnable as `python -m snaphouse.framefollow`). Loop: read the control file (`command.seq`, `command.frame`), pick the targeted frame from the targets' `frames` (all frames when `frame` is null), fetch each frame's `frame.json`, write it, fetch and write `frame.png` only when `updated_at` changed, drop a frame from the set once its `applied_seq >= seq`, stop when the set is empty or `now() - latest_command_seen_at >= window_s`. A changed `seq` in the control file restarts the window and the set.

- [ ] **Step 1: Write failing tests** (fake clock, fake sleep, fake fetch): `test_stops_when_applied_seq_reaches_command`, `test_stops_at_90_seconds`, `test_polls_only_the_targeted_frame`, `test_null_frame_polls_every_frame`, `test_png_fetched_only_when_updated_at_changes`, `test_new_command_extends_window`, `test_missing_or_malformed_control_exits_zero_without_polling`, `test_unreachable_frame_keeps_last_good_and_continues`, `test_invalid_frame_id_skipped`.
- [ ] **Step 2: Run** `uv run pytest tests/python/test_framefollow.py -v`. Expected: FAIL (no module).
- [ ] **Step 3: Implement** per Interfaces, stdlib only.
- [ ] **Step 4: Run again.** Expected: PASS.
- [ ] **Step 5: Commit** `feat(hub): frame follower republishes the mirror fast during a change`.

### Task 7: follower units in the room_picker role

**Files:**
- Create: `roles/room_picker/files/snaphouse-frame-follow.service` (Type=oneshot, same Environment and venv ExecStart pattern as the house-status service, `ExecStart=... -m snaphouse.framefollow`, same hardening as the poller), `roles/room_picker/files/snaphouse-frame-follow.path` (`PathChanged=` the public `art-control.json`, `Unit=snaphouse-frame-follow.service`)
- Modify: `roles/room_picker/tasks/main.yml` (install both units with `notify: Reload systemd for room_picker`; enable the `.path` unit with `when: common_systemd_init` and tag `service`)
- Test: `tests/python/test_room_picker_role.py`

**Interfaces:**
- Consumes: `framefollow.main` (Task 6). The service must not be wanted by the path unit twice concurrently (a oneshot already running is not started again).

- [ ] **Step 1: Write failing tests**: unit files exist; path unit watches the art-control path that `artadmin` writes (assert against `art_share` template value); service runs the follower module as root with `NoNewPrivileges`, `ProtectSystem` and the same writable path as house-status; tasks install and enable both; no personal data; no em dash.
- [ ] **Step 2: Run** `uv run pytest tests/python/test_room_picker_role.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** the units and tasks.
- [ ] **Step 4: Run** the same tests plus `ansible-playbook --syntax-check site.yml`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(hub): path unit starts the frame follower on every art command`.

### Task 8: page state machine (pure model)

**Files:**
- Create: `roles/room_picker/files/site/frame-change-model.js`
- Test: `tests/js/frame-change-model.test.js`

**Interfaces:**
- Produces (all pure, clock injected via `nowMs` args):
  - `startChange({ frameId, action, seq, targetId, nowMs }) -> change` (`targetId` set for `show`, else null).
  - `advance(change, mirror, nowMs) -> change` where `mirror` is the parsed `frame.json` or null; phases `sent | changing | waiting | failed | done | timeout`.
  - `viewOf(change, { frameLabel, artIndex }) -> { phase, text, thumbId | null, busy: boolean, progress: boolean, ends: boolean }` using the exact copy in Global Constraints.
  - `settled(change, nowMs) -> boolean` (true after "Now showing" has been up 3 s, after `waiting`/`failed` ends, and after `timeout` is shown).
  - `pollMs(change | null, idleMs) -> number` (2000 while in flight, else `idleMs`).
  - `needsPng(prevUpdatedAt, mirror) -> boolean`.
  Rules: `pending.seq === seq` -> changing/waiting/failed by `state`; `applied_seq >= seq` -> done (the new mirror is shown); a `pending` with a lower seq is ignored; 90 s since `nowMs` of start with no confirmation -> timeout; `waiting` and `failed` end the flow with their message; a second `startChange` replaces the first.

- [ ] **Step 1: Write failing tests**: sent shows "Sending to <frame>..."; show goes straight to changing with the target; pending matching seq gives changing with title and thumb; unknown id gives "the next picture" and no thumb; waiting for each of music, game, pin, settling gives the exact sentence; failed gives its sentence; `applied_seq >= seq` gives done and "Now showing <title>" for 3 s then settled; stale `pending` of an earlier seq ignored; 90 s timeout; frame with no `pending` or `applied_seq` reaches timeout (old frame); second tap restarts and the first seq's `applied_seq` does not finish it (Review Focus); `pollMs` 2000 then idle; `needsPng` only on changed `updated_at`.
- [ ] **Step 2: Run** `node --test tests/js/frame-change-model.test.js`. Expected: FAIL.
- [ ] **Step 3: Implement** the module.
- [ ] **Step 4: Run again.** Expected: PASS.
- [ ] **Step 5: Commit** `feat(page): frame change state machine`.

### Task 9: page wiring (commands, polling, art index)

**Files:**
- Modify: `roles/room_picker/files/site/rooms.js` (`runArtCommand`, `showOnFrame`, `refreshFrame`, timers), `roles/room_picker/files/site/rooms-model.js` only if a helper is needed
- Test: `tests/js/rooms-frame-change-wiring.test.js` (source-text wiring pins in the style of `rooms-panel-wiring.test.js`, plus model-level tests already in Task 8)

**Interfaces:**
- Consumes: Task 8 model. `POST /api/art/command` already replies `{"command": {"seq", ...}}` (confirmed in `artadmin.py`); `postApi` result gives the seq (add `result.data.command.seq` read if not exposed).
- Produces: `state.change` (one per selected frame). On a successful Next, Back or Show the page calls `startChange` with the reply seq and starts a 2 s page-timer reading that frame's `frame.json` through `advance`, fetching `frame.png` only when `needsPng`; the timer is removed when `settled`. Ensures the art index is loaded on the Frames screen. A reply with no seq falls through to `timeout` handling (no crash). Pin's button also shows the short sent state.

- [ ] **Step 1: Write failing wiring tests**: `runArtCommand` and `showOnFrame` call `startChange`; the 2 s timer registers through `page-timers.js` and is cleared on settle; the poll sleeps while hidden (uses `timers.add`); no `setInterval` outside page-timers.
- [ ] **Step 2: Run** `node --test tests/js/rooms-frame-change-wiring.test.js`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `node --test tests/js/*.test.js`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(page): frame commands start a tracked change`.

### Task 10: phone rendering

**Files:**
- Modify: `roles/room_picker/files/site/index.html` (a status element over the mirror), `style.css`, `rooms.js` (`renderChange(view)`)
- Test: `tests/js/frame-change-view.test.js`

**Interfaces:** Consumes `viewOf` (Task 8). Draws: the tapped button busy for the sent state, the overlay line over the mirror ("Sending..."), the target thumbnail dimmed with the text and a slow progress bar (CSS only, respects `prefers-reduced-motion`), then the new mirror with "Now showing" for 3 s. `aria-live="polite"` on the line. Existing phone type scale.

- [ ] **Step 1: Write failing tests**: element and `aria-live` exist in `index.html`; CSS has a reduced-motion rule for the progress bar; `renderChange` is the only writer of that element.
- [ ] **Step 2: Run** the test file. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `node --test tests/js/*.test.js`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(page): show sending, changing and now showing on the Frame tab`.

### Task 11: panel rendering

**Files:**
- Modify: `roles/room_picker/files/site/panel-more.js` (Frame screen), `panel.css`, `panel-dom.js` if a helper is needed
- Test: `tests/js/panel-css.test.js` (extend), `tests/js/panel-frame-change.test.js`

**Interfaces:** Consumes `viewOf`. Panel Frame screen shows the same line and thumbnail; text at least 20 px, Back and Next stay at least 72 px, every rule under `html[data-panel]`; the panel host exposes `frameChange()` returning the current `viewOf` result.

- [ ] **Step 1: Write failing tests**: CSS rules for the change line are all under `html[data-panel]`, font-size at least 20 px, no target under 72 px; `panel-more.js` renders the line from `host.frameChange()`.
- [ ] **Step 2: Run** `node --test tests/js/panel-css.test.js tests/js/panel-frame-change.test.js`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `node --test tests/js/*.test.js`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(panel): frame change feedback on the wall panel`.

### Task 12: snaphouse docs

**Files:** Modify `AGENTS.md` (a "Frame change feedback" section: states, follower, 2 s polling, old-frame path; Page navigation text for the Frame tab), `docs/cross-repo-features.md` (finish what Task 1 started: follower seam and the page's use).

- [ ] **Step 1: Edit** and run `rg -n "—" AGENTS.md docs/cross-repo-features.md docs/superpowers/plans/2026-11-23-frame-change-feedback.md` (expect none).
- [ ] **Step 2: Commit** `docs: frame change feedback`.

### Task 13: full checks and PRs

- [ ] **Step 1:** nightstand: `uv run ruff check` and `uv run pytest`. snaphouse: `uv run pytest tests/python`, `node --test tests/js/*.test.js`, `ansible-playbook --syntax-check site.yml`, `ansible-lint`, `shellcheck` on shipped shell (note any not installed).
- [ ] **Step 2:** Inspect both diffs for stray files, private data, em dashes.
- [ ] **Step 3:** Push both branches; `gh pr create` in each, cross-linked, bodies listing changes, tests with results, deploy order (either order; frames without the fields take the 90 s path), what to check after merge; end with the repo's PR trailer line. Do not merge.

---

## Self-review notes

Spec coverage: frame fields (T2, T3), validator (T5), follower and units (T6, T7), reply seq (T9, already returned), page states and copy (T8, T10, T11), docs (T1, T4, T12). Interpretation to flag: the spec's draft says `art-control.json` is the watched file; the plan watches the public copy artadmin writes (`public/art/art-control.json`).
