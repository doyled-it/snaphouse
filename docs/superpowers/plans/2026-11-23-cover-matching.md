# Cover Matching Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Find more album covers by searching Apple first, matching names fuzzily on both Apple and MusicBrainz, and stopping transient errors from becoming misses.

**Architecture:** A new pure module `lib/snaphouse/covermatch.py` holds the shared matcher (title and artist rules). `lib/snaphouse/artlookup.py` gains an Apple music search ahead of MusicBrainz, a fetch that reports why it failed, a MusicBrainz 503 retry, release then release-group art, a 10 minute error backoff and one log line per outcome. Cache layout and keys do not change.

**Tech Stack:** Python stdlib, Pillow (already present), pytest with a fake `Fetch` serving recorded fixtures from `tests/python/fixtures/artlookup/`.

**Spec:** `docs/superpowers/specs/2026-11-23-cover-matching-design.md`

## Global Constraints

- No em dashes anywhere (code, comments, docs, commits, UI text).
- No identifying data in the repo or in requests: only artist, album, track and show names, plus the repo User-Agent.
- No new outbound hosts. `itunes.apple.com` and `*.mzstatic.com` are already allowed.
- A wrong cover is worse than no cover. No numeric similarity threshold: close spellings stay a miss.
- Keep the cache layout and keys: full-credit key, lead key shared across an album, `.none` markers (7 days), podcast `.id` and `.noid` sidecars.
- Errors write no miss marker. Audiobook lookups are unchanged apart from the shared fetch logging.
- No test touches the network. Fixtures are trimmed to the fields used.
- Image fetches allow 20 s, searches 10 s. MusicBrainz stays at most one request per second.

## Review Focus

- Same album title by an unrelated artist (Apple's "House of Balloons" covers) stays a miss (Task 3).
- A "Various Artists" compilation never matches a named artist (Task 3).
- An Apple outage with MusicBrainz parsing a miss is an error, not a 7 day miss marker (Task 5).
- A track with no album and no matching song stays a miss, and a song with another title or artist is rejected (Task 3).
- A bad album does not refetch on every track change for 10 minutes (Task 6).

## File structure

- Create `lib/snaphouse/covermatch.py`: pure matcher, no I/O.
- Modify `lib/snaphouse/artlookup.py`: fetch, Apple search, MusicBrainz flow, backoff, logging.
- Create `tests/python/test_covermatch.py`; extend `tests/python/test_artlookup.py`.
- New fixtures in `tests/python/fixtures/artlookup/` (already recorded from the live APIs for the design's table).
- Docs: module docstring, `AGENTS.md` outbound line, `docs/superpowers/specs/2026-11-08-now-playing-design.md` lookup section.

### Task 1: Fetch reports why it failed

**Files:** Modify `lib/snaphouse/artlookup.py`; test `tests/python/test_artlookup.py`.

**Interfaces:**
- Produces: `class FetchError(Exception)` with `.reason: str` (an HTTP status like `"HTTP 503"` or an exception class name). `urllib_fetch(url) -> Optional[bytes]` raises `FetchError` on failure instead of returning None, with timeout 20 s for image hosts (`mzstatic.com`, `coverartarchive.org`, `archive.org`, `covers.openlibrary.org`) and 10 s otherwise (`_timeout_for(url) -> float`). `ArtLookup._get(url) -> Optional[bytes]` wraps the injected fetch: catches `FetchError`, stores the reason in `self._last_reason`, returns None. A fake returning None gives reason `"no data"`. All internal `self._fetch(` calls move to `self._get(`.

- [ ] Test `test_timeout_for_images_is_twenty_seconds`: `_timeout_for("https://coverartarchive.org/x") == 20.0`, `_timeout_for("https://musicbrainz.org/ws/2/release/") == 10.0`.
- [ ] Test `test_urllib_fetch_reports_http_status`: monkeypatch `urllib.request.urlopen` to raise `HTTPError(code=503)`; `urllib_fetch` raises `FetchError` with reason `"HTTP 503"`. A second case with `TimeoutError` gives `"TimeoutError"`.
- [ ] Test `test_fetch_error_reason_is_kept`: a fake fetch raising `FetchError("HTTP 503")`; `lookup._get(url)` is None and `lookup._last_reason == "HTTP 503"`.
- [ ] Run the three, see them fail (missing names). Implement. Run the full `test_artlookup.py`, all existing tests still pass.
- [ ] Commit `feat(covers): fetch reports why it failed`.

### Task 2: Shared matcher

**Files:** Create `lib/snaphouse/covermatch.py`; test `tests/python/test_covermatch.py`.

**Interfaces (produces):**
- `normalize(value: Optional[str]) -> str`: as `artlookup._normalize` plus `&` read as `and`.
- `core_title(value: str) -> str`: normalized title with trailing `(...)`, `[...]`, ` - ...` groups removed (this covers ` - Single` and ` - EP`); falls back to the full normalized title when the core would be empty.
- `artist_names(credit: str) -> set[str]`: split on `, `, ` & `, ` and `, ` x `, ` with `, `feat.`, `ft.`, `featuring` (case-insensitive), normalize each, drop a leading `the `.
- `artists_match(track_credit: Optional[str], candidate_credit: Optional[str]) -> bool`: full normalized credits equal, or the name sets intersect. False when either is empty.
- `title_score(track_title: str, candidate_title: str) -> int`: 2 for equal normalized full titles, 1 for equal cores, 0 otherwise.

- [ ] Tests, each with the design's real values: `"THE MOUSE & THE MASK"` vs `"THE MOUSE AND THE MASK (METALFACE EDITION)"` scores 1; `"NEHRUVIANDOOM (REDUX)"` vs `"NEHRUVIANDOOM (REDUX)"` scores 2 and vs `"NEHRUVIANDOOM"` scores 1; `"Kaya (Remastered 2013)"` vs `"Kaya"` scores 1; `"Catfish - Single"` vs `"Catfish"` scores 1; `"Special Herbs, Vol. 1 & 2"` vs `"Special Herbs, Vol. 7 And 8"` scores 0; `"100% Electronica"` vs `"200% Electronica"` scores 0. Artists: `"DANGERDOOM, MF DOOM & Danger Mouse"` matches `"MF DOOM"`; `"Bob Marley & The Wailers"` matches `"Bob Marley and the Wailers"`; `"The Weeknd"` does not match `"Goomp"`; `"Four Tops"` does not match `"Various Artists"`; empty never matches.
- [ ] Run, see ImportError fail. Implement. Run, pass.
- [ ] Commit `feat(covers): shared fuzzy matcher for titles and artists`.

### Task 3: Apple album and song search first

**Files:** Modify `lib/snaphouse/artlookup.py`; test `test_artlookup.py`.

**Interfaces:**
- Consumes: Task 2 matcher, Task 1 `_get`.
- Produces: `ArtLookup._apple_music(title, artist, album) -> _Resolved`. Term is lead artist plus core title of the album (or the track title plus lead artist when no album, `entity=song`), `media=music`, `limit=10`. Candidates need `artists_match` and `title_score > 0` (song search compares `trackName` to the title and uses the song's `collectionName` artwork; album search compares `collectionName`). Highest `title_score` wins, ties keep Apple's order. Artwork is `artworkUrl100` with its `100x100bb` segment rewritten to `600x600bb`. Wired as the first step of `_find_music` (before MusicBrainz), found cover cached under the full-credit key. The early `if not album: return miss` leaves `_find_music`; a no-album track uses the song search only.

- [ ] One test per table row, fake fetch serving the recorded fixture and a JPEG for `mzstatic.com`: Catfish (Four Tops), 100% Electronica (Clanton; `200% Electronica` by another act must not win), Special Herbs Vol. 1 & 2, NEHRUVIANDOOM (REDUX) picks the Redux release over the plain one, Kaya (Remastered 2013), THE MOUSE & THE MASK via `"THE MOUSE AND THE MASK (METALFACE EDITION)"` and credit `"DANGERDOOM, MF DOOM & Danger Mouse"`. Each asserts a cover URL, zero MusicBrainz calls, and the requested term and `600x600bb` image URL.
- [ ] Song test: no album, title `"Strung Out For Your Love"`, artist `"Four Tops"` finds the Catfish artwork from `apple_song_catfish.json`.
- [ ] Negative tests (must stay a miss on Apple): `"House of Balloons (Original)"` by `"The Weeknd"` against `apple_album_house_of_balloons.json` (all other acts); album `"Smooth Sax Tribute to the Four Tops"` by `"Four Tops"` against the song fixture (credited to Various Artists); no-album track whose song search returns songs with another title; a video title with no album and an empty result.
- [ ] Run, see failures (no Apple calls made). Implement and wire. Existing music tests: add an empty Apple route (`apple_album_empty.json`) to their fake, since an Apple failure now counts per Task 5; report which tests changed.
- [ ] Commit `feat(covers): search Apple first for music covers`.

### Task 4: MusicBrainz uses the matcher, retries a 503

**Files:** Modify `lib/snaphouse/artlookup.py`; test `test_artlookup.py`.

**Interfaces:** `_music_search` selects releases with `title_score > 0` and `artists_match(name, joined credit)`, highest score first. A `FetchError` with reason `"HTTP 503"` on a MusicBrainz search sleeps 2 s (`self._sleep`) and retries once; a second failure is an error. `_credit_equals` is replaced by the matcher.

- [ ] Test `test_musicbrainz_503_retries_once`: first MusicBrainz call raises `FetchError("HTTP 503")`, second serves `musicbrainz.json`; the cover is found, `slept` contains 2.0.
- [ ] Test `test_musicbrainz_503_twice_is_an_error`: two 503s give a failure and no `.none` marker.
- [ ] Test `test_musicbrainz_matches_decorated_release_by_core`: the Cave World fixture (releases titled `Cave World`) found for album `"Cave World (Deluxe)"` with Apple empty.
- [ ] Run, see failures. Implement. Run the file, existing music tests still pass.
- [ ] Commit `feat(covers): MusicBrainz matcher and 503 retry`.

### Task 5: Next release, then release-group art; Apple error is not a miss

**Files:** Modify `lib/snaphouse/artlookup.py`; test `test_artlookup.py`.

**Interfaces:** `_music_search` walks matching releases in score order; a 404 (or any failed fetch) on a release's `front-500` moves to the next, then to `https://coverartarchive.org/release-group/<release-group id>/front-500`; if all fail the outcome is `_ERROR`. `_find_music` combines sources: Apple found wins; else MusicBrainz found; else if either source errored the result is `_ERROR`; else a miss.

- [ ] Test Preacher's Son (`musicbrainz_preachers_son.json`): release art 404, release-group art 200, cover found, request log shows the release then the release-group URL.
- [ ] Test Special Herbs (`musicbrainz_special_herbs.json`) with Apple empty: release 404, release-group 200, found.
- [ ] Test next release first: two matching releases, first 404, second 200, no release-group request.
- [ ] Test `test_apple_error_with_musicbrainz_miss_is_failure`: Apple fetch returns None, MusicBrainz parses and misses: result None and no `.none` marker.
- [ ] Test `test_apple_error_with_musicbrainz_hit_finds_cover`.
- [ ] Run, see failures. Implement. Commit `feat(covers): fall back to the next release and release-group art`.

### Task 6: Error backoff

**Files:** Modify `lib/snaphouse/artlookup.py`; test `test_artlookup.py`.

**Interfaces:** `self._error_until: dict[str, float]` keyed by the full-credit key; `_ERROR_BACKOFF_S = 600`. After an `_ERROR` (music, podcast and audiobook) the same key returns a failure with no network call until the clock passes the deadline. No marker is written.

- [ ] Test: first `find` fails (all fetches None), second `find` 5 minutes later makes zero fetch calls and still returns None, third at 11 minutes fetches again (injected clock).
- [ ] Run, see failure. Implement. Commit `feat(covers): do not retry a failed lookup for 10 minutes`.

### Task 7: Podcast publisher-prefix match

**Files:** Modify `lib/snaphouse/artlookup.py`; test `test_artlookup.py`.

**Interfaces:** In `_podcast_search` and `_podcast_id_search`, when no result matches the show name exactly, accept a result whose `artistName` equals the show name (normalized) and whose normalized `collectionName` starts with the normalized show name. Exact matches still win. Audiobooks unchanged.

- [ ] Test: `"Democracy Now!"` against `apple_podcast_democracy_now.json` finds "Democracy Now! Audio" and carries its collectionId. Negative: `"Democracy Now"` where the only prefix result has a different publisher stays a miss.
- [ ] Run, see failure. Implement. Commit `feat(covers): match a podcast under its publisher name`.

### Task 8: Logging

**Files:** Modify `lib/snaphouse/artlookup.py`; test `test_artlookup.py`.

**Interfaces:** one INFO line per source attempt: `art lookup apple: <outcome> (<reason or matched names>)`, with outcomes `found`, `miss`, `error`; reasons come from `_last_reason`. The existing per-lookup line stays. Only names are logged.

- [ ] Test (caplog): an Apple miss then MusicBrainz 503 twice logs `musicbrainz` with `error` and `HTTP 503`; a found Apple cover logs the matched artist and album names.
- [ ] Run, see failure. Implement. Commit `feat(covers): log source, reason and outcome`.

### Task 9: Docs

**Files:** `lib/snaphouse/artlookup.py` docstring, `AGENTS.md`, `docs/superpowers/specs/2026-11-08-now-playing-design.md`.

- [ ] Update the docstring (Apple first for music, matcher, backoff, release-group fallback), the AGENTS.md hub outbound line (Apple now serves music covers too) and the now-playing design lookup section. No em dashes.
- [ ] Run the full checks: `node --test tests/js/*.test.js`, `uv run pytest tests/python`, `ansible-playbook --syntax-check site.yml`, shellcheck on touched shell files (none expected).
- [ ] Commit `docs(covers): describe the new lookup order`. Push, open the PR.
