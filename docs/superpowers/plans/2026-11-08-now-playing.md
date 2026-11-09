# Now Playing Everywhere Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The hub publishes complete now-playing data, artwork, and a 50-play history; the room picker and the frame read it, and the frame gets the agreed layout, progress, and a 20 second settle.

**Architecture:** The two snapserver control scripts start passing duration, position, and genre. A new hub service follows the House stream over snapserver's TCP control port, looks up missing art, and writes `now-playing.json` and `play-history.json` to the public web root. The room picker and the frame consume those files instead of building now-playing from snapserver themselves.

**Tech Stack:** Python 3 (stdlib, plus Pillow as the existing hub-only extra) and Ansible in `snaphouse`; plain ES modules with `node --test` for the room picker; Python with httpx and Pillow in `nightstand`; uv everywhere.

**Spec:** `docs/superpowers/specs/2026-11-08-now-playing-design.md` (snaphouse). Read it before any task; its "Decided by the owner" section and the two JSON shapes are binding.

## Global Constraints

- No em dashes anywhere: code, comments, docs, commit messages, UI text.
- No identifying data in either repo or in any request: no IPs, MACs, usernames, real names, or home location. Lookup requests carry only track, show, book, or artist names and a User-Agent naming the public repo URL.
- Python packages through uv only (`uv run`, `uv sync`); no pip, no apt `python3-*`. The hub gains no new runtime dependency: HTTP uses `urllib` from the stdlib, images use the existing `hub` extra (Pillow).
- TDD: failing test first, watch it fail, implement, watch it pass. Focused tests while iterating; the full suite before each commit.
- snaphouse checks: `uv run pytest tests/python`, `node --test tests/js`, `ansible-playbook --syntax-check site.yml`, `ansible-lint`, `shellcheck` on shell. nightstand checks: `uv run pytest`.
- Each task is its own branch and PR in its repo, closing or referencing its issue, with CI green before merge.
- Long-form threshold 1200 s; history threshold 30 s; history size 50; pause hold 600 s; frame settle 20 s; progress bucket 600 s; frame now-playing poll 10 s; art resize max 600 px; miss cache 7 days; MusicBrainz at most 1 request per second.

## Review Focus

1. A properties resend for an unrelated change (status flip, art arriving) after a long play must not move the bar backward. Pinned by Task 1's emit-time extrapolation tests and Task 2's anchor test.
2. A track with a title but no artist and no album (internet radio, some apps) must publish, skip art lookup without error, and render without empty lines. Pinned in Tasks 2, 3, and 8.
3. `now-playing.json` missing, empty, or malformed (old hub, service down, partial write) must leave the frame on its schedule (as nothing playing) and hide the web card, never crash or show stale data. Pinned in Tasks 5 and 6.
4. Pausing for more than 10 minutes and then resuming the same track must not create a second history entry when it resumes within its duration plus 10 minutes of starting. Pinned in Task 2.
5. Extremely long titles, and titles in scripts Inter lacks, must stay inside the title box (shrink, then ellipsize) and never overlap the strip or chips. Pinned in Task 8's bounds test.

---

## snaphouse tasks

### Task 1: Bridges publish duration, position, and genre

**Files:**
- Modify: `lib/snaphouse/airplay_meta.py` (`Player`, `apply_item`, `build_properties`)
- Modify: `lib/snaphouse/avrcp_meta.py` (`track_to_metadata`, `Player`, `build_properties`, `_dbus_events`, the serve loop)
- Test: `tests/python/test_airplay_meta.py`, `tests/python/test_avrcp_meta.py`

**Interfaces:**
- Produces, in the Snapcast properties both bridges emit: `metadata["duration"]` (float seconds) when known, `metadata["genre"]` (list of one string) when known, and top-level `properties["position"]` (float seconds) when known. `position` is the position at emit time.
- Both `Player` classes gain an injectable clock: `Player(clock: Callable[[], float] = time.monotonic)`, and an anchor `(position_s, at_monotonic)`. `build_properties` extrapolates `position = anchor_pos + (clock() - anchor_at)` while `playback_status == "playing"`, clamped to `[0, duration]` when the duration is known, and uses the anchor unchanged when paused or stopped.
- AirPlay: `ssnc/prgr` payload is ASCII `start/current/end` RTP frames at 44100 Hz: duration `(end - start) / 44100`, anchor position `(current - start) / 44100` at arrival; it returns True (emit). `core/astm` (4-byte big-endian ms) sets the duration only when no `prgr` duration exists for the track. `core/asgn` sets the genre. `mdst` clears duration, genre, and the anchor along with the other fields; a real track change (the existing identity logic) also drops the anchor.
- AVRCP: `track_to_metadata` maps `Track["Genre"]` to `metadata["genre"] = [genre]`. `_dbus_events` also yields `("position", ms)` from `MediaPlayer1.Position` (initial state and `PropertiesChanged`); the serve loop anchors it and emits.

- [ ] **Step 1: Write the failing tests**

```python
# test_airplay_meta.py
def test_prgr_sets_duration_and_position():
    clock = FakeClock(100.0)
    p = Player(clock=clock)
    apply_item(p, "ssnc", "prgr", b"1000/45100/1324000", cover_dir=TMP, host="h")
    props = p.properties()
    assert props["metadata"]["duration"] == pytest.approx((1324000 - 1000) / 44100)
    assert props["position"] == pytest.approx(1.0, abs=0.01)

def test_position_extrapolates_at_emit_time_while_playing():
    clock = FakeClock(100.0)
    p = Player(clock=clock)
    apply_item(p, "ssnc", "pbeg", b"", cover_dir=TMP, host="h")
    apply_item(p, "ssnc", "prgr", b"0/0/4410000", cover_dir=TMP, host="h")
    clock.advance(300)
    apply_item(p, "ssnc", "PICT", b"", cover_dir=TMP, host="h")  # unrelated resend
    assert p.properties()["position"] == pytest.approx(300.0)

def test_position_frozen_while_paused():
    ...  # pbeg, prgr at 0, advance 60, pfls, advance 600: position == 60

def test_astm_is_fallback_duration_only():
    ...  # astm 322000 alone gives 322.0; with a prgr duration, astm does not override

def test_asgn_sets_genre_and_mdst_clears_it():
    ...  # genre == ["Podcast"]; after mdst/mden of a new track, no "genre" key

# test_avrcp_meta.py
def test_genre_maps_to_list():
    assert track_to_metadata({"Title": "t", "Genre": "Podcast"})["genre"] == ["Podcast"]

def test_position_anchor_extrapolates_and_freezes():
    ...  # Player(clock), position 10000 ms while playing, advance 5: position == 15.0;
         # status paused, advance 100: position == 15.0
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/python/test_airplay_meta.py tests/python/test_avrcp_meta.py -v`
Expected: FAIL (no `clock` parameter, no `position` key).

- [ ] **Step 3: Implement** per the Interfaces block. Keep `canSeek` and the other capability flags unchanged. The D-Bus half stays `# pragma: no cover`, confirmed on the hub.

- [ ] **Step 4: Run the full suite** `uv run pytest tests/python` and expect PASS.

- [ ] **Step 5: Commit** (two commits are fine, one per bridge).

### Task 2: Now-playing tracker (pure logic)

**Files:**
- Create: `lib/snaphouse/nowplaying.py` (the pure part; Task 4 adds the runtime)
- Test: `tests/python/test_nowplaying.py`

**Interfaces:**
- Consumes: Task 1's properties shape; snapserver `Server.GetStatus` `server` objects (streams, groups, clients) as the frame's `parse_now_playing` reads them today.
- Produces:
  - `classify(duration: float | None, genre: list[str] | None) -> str` returning `"long_form"` or `"music"` (spec "Kind").
  - `TrackKey = tuple[str, str | None, str | None]` (title, artist, album).
  - `class Tracker` with `__init__(self, house_stream: str, history: list[dict], *, hold_s=600, count_after_s=30, max_plays=50)`; `on_server(self, server: dict, now: datetime) -> None`; `on_properties(self, stream_id: str, properties: dict, now: datetime) -> None`; `tick(self, now: datetime) -> None`; `set_art(self, key: TrackKey, art_url: str) -> None`; `wants_art(self) -> tuple[TrackKey, str, list[str] | None] | None` (key, kind, genre) for a current track with no art and no lookup started; `now_playing_doc(self, now: datetime) -> dict`; `history_doc(self) -> dict`; `dirty` flags `now_playing_changed` and `history_changed` that the runtime clears after writing.
  - `rewrite_art_url(url: str | None) -> str | None` turning `http://<anything>/cover/x.jpg` into `/cover/x.jpg`; passes through same-origin paths; anything else becomes `None`.
- Rules (from the spec): follow the `house_stream` id; `source` is the first of the House inputs whose status is `playing` (AirPlay before Bluetooth, the meta order), else the last known; `status` is `playing` only when the House playbackStatus is `playing` and the stream is not idle, else `paused`; after `hold_s` continuously not playing, `track` becomes `None`; `rooms` uses the frame's existing room rule; `position_at` is the arrival time of the last properties update carrying `position`; `started_at` and the restart continuation rule as in the spec; a play is appended (newest first, trimmed to `max_plays`) once a key has been current and playing for `count_after_s` in total, once per play; resuming the same key never adds a second entry when the newest entry has that key and `started_at` within `duration + 600 s`.

- [ ] **Step 1: Write the failing tests**, at least:

```python
def test_classify():
    assert classify(1200, None) == "long_form"
    assert classify(1199, None) == "music"
    assert classify(None, ["Podcast"]) == "long_form"
    assert classify(4000, ["Electronic"]) == "long_form"   # kind by duration
    assert classify(None, None) == "music"

def test_doc_for_playing_track(): ...       # every field of the spec's shape, art_url rewritten
def test_track_without_artist_or_album(): ... # artist/album None, still published (Review Focus 2)
def test_pause_holds_then_clears():          # paused at t, tick t+599 still paused, t+600 -> track None
    ...
def test_play_counted_after_30s_once(): ...  # 29 s -> no entry; 30 s -> one; 300 s -> still one
def test_skip_under_30s_not_counted(): ...
def test_resume_after_long_pause_same_play(): ...  # Review Focus 4
def test_history_trimmed_to_50(): ...
def test_restart_mid_track_keeps_started_at(): ...
def test_stale_position_resend_does_not_reanchor_backward(): ...  # Review Focus 1, with Task 1 semantics
def test_wants_art_once_per_key(): ...
def test_empty_rooms_still_published(): ...
```

- [ ] **Step 2: Run** `uv run pytest tests/python/test_nowplaying.py -v`, expect FAIL.
- [ ] **Step 3: Implement** `classify`, `rewrite_art_url`, and `Tracker`. No I/O in this module part.
- [ ] **Step 4: Run** the full suite, expect PASS.
- [ ] **Step 5: Commit.**

### Task 3: Art lookup

**Files:**
- Create: `lib/snaphouse/artlookup.py`
- Test: `tests/python/test_artlookup.py`, fixtures under `tests/python/fixtures/artlookup/` (trimmed real responses; record them with a script or by hand, no identifying data)

**Interfaces:**
- Produces:
  - `Fetch = Callable[[str], bytes | None]` (GET a URL, `None` on any failure). `def urllib_fetch(url: str) -> bytes | None` is the real one: stdlib `urllib.request`, 10 s timeout, the User-Agent `snaphouse/<version> (+https://github.com/doyled-it/snaphouse)`.
  - `route(kind: str, genre: list[str] | None) -> str` returning `"podcast"`, `"audiobook"`, or `"music"`: genre naming a podcast gives podcast, naming an audiobook or "spoken" gives audiobook, any other genre gives music; with no genre, `long_form` gives podcast (falling back to audiobook on a miss) and `music` gives music.
  - `class ArtLookup` with `__init__(self, cover_root: Path, fetch: Fetch, *, clock=time.time, sleep=time.sleep, max_files=300, miss_ttl_s=7*86400)` and `find(self, title: str, artist: str | None, album: str | None, kind: str, genre: list[str] | None) -> str | None` returning a same-origin URL `/cover/lookup/<sha1>.jpg` or `None`.
  - `keep_for_history(art_url: str | None, public_root: Path) -> str | None`: copies the referenced file into `public_root/cover/history/` and returns `/cover/history/<name>`; `prune_history_covers(public_root: Path, keep_urls: set[str]) -> None` deletes history covers no longer referenced.
- Rules: exact-match policy from the spec (normalize case, punctuation, whitespace; music also needs the artist to match); the iTunes, Open Library, MusicBrainz plus Cover Art Archive endpoints from the spec; a 1 request per second gate for MusicBrainz; images saved via Pillow at most 600 px, JPEG, written atomically; a miss writes `<sha1>.none` and suppresses that lookup for `miss_ttl_s`; pruning touches only `cover_root/lookup/`. No lookup at all when both artist and album are missing for music.

- [ ] **Step 1: Write the failing tests**, at least:

```python
def test_route():
    assert route("long_form", None) == "podcast"
    assert route("music", ["Podcast"]) == "podcast"
    assert route("long_form", ["Audiobook"]) == "audiobook"
    assert route("long_form", ["Electronic"]) == "music"

def test_podcast_exact_match_saves_cover(tmp_path): ...    # fake fetch serves the iTunes fixture + a JPEG
def test_podcast_no_exact_match_returns_none(tmp_path): ...
def test_long_form_podcast_miss_falls_back_to_open_library(tmp_path): ...
def test_music_requires_artist_match(tmp_path): ...
def test_miss_is_cached_for_seven_days(tmp_path): ...      # second find makes no fetch; after ttl it retries
def test_musicbrainz_gate_sleeps(tmp_path): ...
def test_no_artist_no_album_music_skips_lookup(tmp_path): ...
def test_prune_only_touches_lookup_dir(tmp_path): ...       # an AirPlay cover in cover/ survives
def test_keep_for_history_and_prune(tmp_path): ...
def test_request_urls_carry_only_names(tmp_path): ...       # recorded URLs contain only the searched names
```

- [ ] **Step 2: Run** `uv run pytest tests/python/test_artlookup.py -v`, expect FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the full suite, expect PASS.
- [ ] **Step 5: Commit.**

### Task 4: The service, its deployment, and the contracts

**Files:**
- Modify: `lib/snaphouse/nowplaying.py` (add the runtime: `main()`), keep the runtime thin and the I/O `# pragma: no cover` only where it touches sockets
- Create: `roles/metadata/files/snaphouse-nowplaying.service`, `roles/metadata/files/verify.d/nowplaying.sh`
- Modify: `roles/metadata/tasks/main.yml`, `roles/room_picker/templates/room_picker.conf.j2` (exact-match locations for `/now-playing.json` and `/play-history.json`, like `/art-history.json`)
- Modify: `docs/cross-repo-features.md` (two pinned contracts), `docs/superpowers/specs/2026-11-06-snaphouse-design.md` and `AGENTS.md` (hub outbound list), `README.md` (one paragraph)
- Test: `tests/python/test_nowplaying_runtime.py`, plus whatever the existing role tests (`test_bt_roles.py`, `test_verify.py`, container apply) need

**Interfaces:**
- Consumes: Tasks 2 and 3.
- Produces: `python -m snaphouse.nowplaying` with `--snapserver-host` (default `localhost`), `--port` (default `1705`), `--public-root` (default `/var/lib/snaphouse/public`), `--house-stream` (default from `vars/all.yml`'s House stream name, passed by the unit). It connects to snapserver's TCP control port (newline-delimited JSON-RPC; confirm on the hub that the default TCP control is enabled in 0.35), sends `Server.GetStatus` on connect, applies `Stream.OnProperties` to `Tracker.on_properties`, and re-requests `Server.GetStatus` on any `Client.*`, `Group.*`, `Stream.OnUpdate`, or `Server.OnUpdate` notification. It calls `tick` every second, runs art lookups on one background worker thread (never blocking the socket loop), and writes both files atomically (temp file plus `os.replace`, mode 0644) whenever a dirty flag is set. On a socket error it reconnects with backoff capped at 30 s. History loads from `play-history.json` at start; a corrupt file starts empty and is logged.
- The unit: `Type=simple`, `Restart=always`, `RestartSec=5`, `After=snapserver.service`, runs from the on-device venv like the house-status unit, as a non-root user that can write the public root (match how the AirPlay bridge writes `cover/`).
- `verify.d/nowplaying.sh`: the unit is active, and `now-playing.json` exists and parses as JSON with a `track` key.

- [ ] **Step 1: Write the failing runtime tests** with a fake TCP server (a socketpair or a localhost listener in the test): connect sends `Server.GetStatus`; an `OnProperties` notification leads to a written `now-playing.json` matching the spec's shape; a dropped connection reconnects; a corrupt history file starts empty; writes are atomic (no partial file visible).
- [ ] **Step 2: Run** them, expect FAIL.
- [ ] **Step 3: Implement** the runtime, the unit, the role tasks (installed only on the hub, as the metadata role already is), the nginx locations, the verify check, and the docs. Ansible tasks touching hardware or services carry the `hardware`/`service` tags per AGENTS.md.
- [ ] **Step 4: Run** pytest, `ansible-playbook --syntax-check site.yml`, `ansible-lint`, `shellcheck roles/metadata/files/verify.d/nowplaying.sh`. Expect PASS. CI runs the container apply.
- [ ] **Step 5: Commit.** After merge, the controller applies on the hub (`cd /opt/snaphouse && ./bin/snaphouse update`) and checks `curl http://house.local/now-playing.json` while something plays.

### Task 5: Room picker now-playing card and history

**Files:**
- Modify: `roles/room_picker/files/site/rooms-model.js`, `rooms.js`, `index.html`, `style.css`
- Test: `tests/js/` (the existing rooms-model test file, or a new `now-playing.test.js`)

**Interfaces:**
- Consumes: the two JSON contracts.
- Produces (model, pure, tested): `parseNowPlayingDoc(raw) -> {title, artist, album, kind, status, duration, position, positionAt, startedAt, artUrl, rooms} | null` (null on missing, malformed, or `track: null`); `progressAt(track, nowMs) -> {elapsed, total, fraction, endsAtMs} | null` (null without a duration; extrapolates only while `playing`; clamps to the duration; with a duration but no position uses `startedAt`); `formatDuration(seconds) -> "5:22" | "1:02:03"`; `parsePlayHistory(raw) -> Array<play>` (drops invalid entries, keeps order, at most 50).
- UI: the card at the top of the page (art or placeholder, title, artist, album, bar with `elapsed / total` and `ends 9:46`, ticking each second, frozen while paused, "Paused" label when paused); hidden when the model returns null. History section below the rooms in the art-history visual language: thumbnail, title, artist and album, start time (date when not today), length. Poll `now-playing.json` every 5 s and `play-history.json` every 30 s with `cache: "no-store"`. The existing title/artist line derived from `/jsonrpc` is replaced by the card.

- [ ] **Step 1: Write the failing node tests** for the four model functions, including malformed input (Review Focus 3), paused freeze, clamping, and the `startedAt` fallback.
- [ ] **Step 2: Run** `node --test tests/js`, expect FAIL.
- [ ] **Step 3: Implement** the model, then the UI.
- [ ] **Step 4: Run** `node --test tests/js` and the full pytest suite (the room picker role tests), expect PASS. Load the page against a running hub in the Orca browser by IP and check the card and history render.
- [ ] **Step 5: Commit.**

---

## nightstand tasks (start after Task 4 is live on the hub)

### Task 6: Read now-playing from the hub

**Files:**
- Create: `src/nightstand/sources/hubnowplaying.py`
- Modify: `src/nightstand/models.py` (`NowPlaying`), `src/nightstand/app.py` (`LiveSources.now_playing`, `LiveSources.cover`), `src/nightstand/sources/coverart.py` (URL fetch and cache only; MusicBrainz removed), `src/nightstand/config.py` (`check_seconds` default 10), `config.example.toml`, `AGENTS.md` (allowlist), and `sources/snapserver.py` (delete if nothing else uses it)
- Test: `tests/test_hubnowplaying.py`, `tests/test_coverart.py`, `tests/test_app.py`

**Interfaces:**
- `NowPlaying` gains `kind: str = "music"`, `status: str = "playing"`, `duration: float | None = None`, `position: float | None = None`, `position_at: datetime | None = None`, `started_at: datetime | None = None`, and a method `position_now(self, now: datetime) -> float | None` (extrapolate from `position_at` while playing, freeze while paused, clamp to the duration, `None` without a position).
- `fetch_now_playing(client: httpx.Client, host: str) -> dict` (GET `http://{host}/now-playing.json`, raising on non-200 or bad JSON) and `parse_now_playing(doc: dict) -> NowPlaying | None` (`None` for `track: null`, empty `rooms`, or a missing title).
- `CoverArt.for_track(np)` fetches `http://{hub host}{np.art_url}` for same-origin paths and caches by URL hash as today; a track with no `art_url` has no cover.

- [ ] **Step 1: Failing tests:** parse every field of the spec's example; `track: null` and empty rooms give `None`; malformed docs raise in fetch and degrade to `None` through `LiveSources` (Review Focus 3); `position_now` extrapolates, freezes, clamps; the cover is fetched from the hub path; no MusicBrainz request is ever made.
- [ ] **Step 2: Run** `uv run pytest`, expect FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `uv run pytest`, expect PASS.
- [ ] **Step 5: Commit.**

### Task 7: Redraw key, progress bucket, and settle

**Files:**
- Modify: `src/nightstand/rules.py` (`_now_playing_key`, `_split_key`), `src/nightstand/app.py` (`AppState`, `run_once`), `src/nightstand/config.py` (`schedule.settle_seconds: int = 20`, `schedule.progress_bucket_seconds: int = 600`)
- Test: `tests/test_rules.py`, `tests/test_app.py`

**Interfaces:**
- `_now_playing_key(np: NowPlaying, now: datetime, cfg: Config) -> tuple` is `("np", title, artist, album, rooms, bucket)` where `bucket = int(np.position_now(now) // cfg.schedule.progress_bucket_seconds)` for a `long_form` track with a known position, else `None`. `decide` passes `now` and `cfg` through.
- `AppState` gains `shown_identity: TrackKey | None` and `pending: tuple[TrackKey | None, datetime] | None`. In `run_once`, compute `identity = (np.title, np.artist, np.album) if np else None`. If `identity != state.shown_identity`, hold (return False) until the same identity has been seen continuously for `settle_seconds`; then push normally (quiet hours still apply through `should_push`). On a push, set `shown_identity = identity`. Bucket and room changes for the same identity push without waiting.

- [ ] **Step 1: Failing tests:** bucket increments at 600 s for long-form playing, stays fixed while paused, is `None` for music; a new track at t pushes nothing until t+20 s; skipping through three tracks in 30 s pushes once, for the last; stop to nothing playing waits 20 s before art; bucket change pushes immediately; quiet hours hold a settled change and the first push after quiet hours carries the current bucket.
- [ ] **Step 2: Run** `uv run pytest tests/test_rules.py tests/test_app.py -v`, expect FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `uv run pytest`, expect PASS.
- [ ] **Step 5: Commit.**

### Task 8: The new now-playing screen

**Files:**
- Modify: `src/nightstand/screens/now_playing.py`, `src/nightstand/app.py` (`_render` passes `now`), `src/nightstand/cli.py` (previews: add a long-form podcast preview with a long title)
- Test: `tests/test_screens_now_playing.py`, new goldens under `tests/golden/`

**Interfaces:**
- `render(np: NowPlaying, cover: Image.Image | None, now: datetime) -> Image.Image` at 800x480, six pure colors.
- Layout constants from the spec's "Decided by the owner": `COVER_SIZE = 320`; text column from x 342 (x 40 with no cover) to the right margin; title Inter Black up to 4 lines, sizes 52 down to 28 in 2 px steps, ellipsized only at 28; artist bold 30 px one line; album regular 26 px one line; the strip below the cover with 28 px margins: long-form with duration and position draws the bar (black 2 px outline, red elapsed fill, 14 px tall) and `23:10 of 52:00  ·  ends 9:46`; music with a duration draws `5:22  ·  ends 9:14`; no duration draws no strip text; room chips bottom-left as today. End time: `now + (duration - position_now)`, or `started_at + duration` with no position, formatted with the frame's existing clock-time helper (add one in `text.py` if none exists, 12-hour without am/pm, matching the weather screen's sun times).

- [ ] **Step 1: Failing tests:** goldens for music with cover, podcast with cover and bar, podcast without cover, and a no-duration track; title shrink (a 90-character title fits in 4 lines above 28 px), ellipsis only at 28 px; a bounds test asserting no title pixels below the cover row's text limit and no overlap with the strip or chips for a 300-character title and a CJK title (Review Focus 5); a track with no artist and no album draws no empty lines (Review Focus 2); end-time math for both branches.
- [ ] **Step 2: Run** `uv run pytest tests/test_screens_now_playing.py -v`, expect FAIL.
- [ ] **Step 3: Implement**, then regenerate goldens only for the new screen and check them by eye with `simulate_panel`.
- [ ] **Step 4: Run** `uv run pytest`, expect PASS.
- [ ] **Step 5: Commit.** After merge, the frame self-updates; the controller confirms the new screen on the panel with the owner.

---

## After the tasks (controller, with the owner)

- Capture a real AVRCP sample from the owner's podcast and audiobook apps (Bluetooth from the phone, owner present), compare with the default field mapping and genre list, and open a small follow-up PR if they disagree.
- Cross-repo audit (cross-repo-completeness) across both repos before calling it done.
