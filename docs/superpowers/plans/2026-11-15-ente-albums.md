# Ente albums on the frame Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Pictures in Ente albums shared with a house account reach the frame in the normal art rotation, shown as read-only Gallery folders, tall pictures included.

**Architecture:** A hub timer runs its own sync, built on the Ente CLI's album list, file list and file download commands (no videos), into a private mirror root. The art library (`artlib.py`) scans that root as a second, read-only source, makes a downscaled JPEG rendition of every picture in a hub cache, and lists each album as a folder (`source: "ente"`) in `art-index.json`, served under `/art/`. The frame mirrors enabled folders as today and exempts folders with the reserved `ente-` prefix from the landscape rule.

**Tech Stack:** Python 3.11 (stdlib, Pillow, pillow-heif, pytest, uv), Ansible roles, nginx, systemd, vanilla JS (`node --test`).

**Spec:** `docs/superpowers/specs/2026-11-15-ente-albums-design.md` (in the snaphouse worktree). Issue #172.

**Repos:** `snaphouse` = `/Users/mdoyle/orca/workspaces/snaphouse/ente-albums` (branch `ente-albums-design`). `nightstand` = `/Users/mdoyle/projects/smart-home/nightstand`. snaphouse merges (and deploys to the hub) before nightstand starts.

## Global Constraints

- No identifying data in code, tests, fixtures, commits or PRs: no IPs, MACs, serials, names, emails, album names, public hostnames, or the external domain. Fixtures use made-up names such as "Album One".
- No em dashes anywhere (code, comments, docs, UI copy, commits, PR text).
- Python dependencies are managed with uv (`uv add`, `uv lock`, `uv run pytest`). Never pip.
- JS tests run locally with `node --test 'tests/js/**/*.test.js'` (the directory form fails on this Mac).
- Ansible lint: `uvx ansible-lint`. Python tests in snaphouse: `uv run pytest tests/python`.
- Existing validators for art folders and files (`_FOLDER_NAME_RE`, `_FILE_NAME_RE`, `rooms-model.js` checks, `artsync._is_safe_relpath`) must not be loosened. Ente ids are generated to pass them.
- Workers never merge. Merging deploys to the hub and the owner approves it. Workers commit on their task branch only.
- Every report must paste the red-phase test output (the failing run before the implementation) as well as the green run.
- Commits end with `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`. PR bodies end with `🤖 Generated with [Claude Code](https://claude.com/claude-code)`.
- Ente export is read-only for the hub: nothing is ever written back to Ente, and no credentials or account details appear in the repo, logs, status record or index.
- The art folder library, its ids, and the frame's art sync for non-Ente folders must behave exactly as before (existing tests stay green).

## Fixed decisions (so tasks agree)

- Ente CLI (verified in Task 1): version 0.4.4, asset `ente-cli-v0.4.4-linux-amd64.tar.gz`, SHA-256 `719834d6b65e9e48a4a1d4e3e577247633f0e8a5eb2bae30278ec6c6fc536f17`, binary `ente` inside the tarball, `ente --version` prints `ente 0.4.4`. Global flags: `--json`, `--no-input`, `--offline`. Headless env: `ENTE_CLI_HOME`, `ENTE_CLI_VAULT_KEY` (key from `ente vault key generate`, 44 base64 characters plus a newline).
- Commands the sync uses: `ente photos album list --json` (list of `{id, name, ownerId, type, updatedAt, visibility}`); `ente photos file list --album <ALBUM_ID> --type image --type livephoto --all --json` (list of `{albumIds, caption, createdAt, dateTime, durationSeconds, hash, height, id, location, modifiedAt, name, offsetTime, ownerId, sizeBytes, type, updatedAt, visibility, width}`; `--all` is required); `ente photos file download <FILE_ID> --album <ALBUM_ID> --output <NEW_PATH>` (output must not exist). A `livephoto` downloads as a ZIP holding the still (jpg or heic) plus a video member (extension `MP` for a Pixel motion photo); keep only the image member, never extract the video. Login: `ente account login --app photos --account house` (interactive, once); `ente account list --json` shows whether the account is logged in for photos.
- Mirror layout: `<root>/<albumId>/<fileId>.<ext>` plus `<root>/<albumId>/album.json` = `{"title": str, "updated_at": int, "files": {"<fileId>": {"updated_at": int, "hash": str, "ext": str}}}`. A file is listed only if it is in `album.json` and the image exists.
- Folder id: `"ente-" + albumId`. Piece file: `<fileId>.jpg`. Piece id: `<folder id>/<fileId>.jpg`. Piece url: `/art/<piece id>`. Both ids must match `^[0-9A-Za-z]{1,24}$` or the album or file is skipped (so the names pass `_FOLDER_NAME_RE` and `_FILE_NAME_RE`). Title shown: the album's title from `album.json`.
- Reserved prefix: any art folder or created/renamed folder name starting with `ente-` (case-insensitive) is refused or ignored by the art side.
- Paths on the hub: mirror root `/var/lib/snaphouse/ente/mirror`, CLI home `/var/lib/snaphouse/ente/home`, vault key `/var/lib/snaphouse/ente/vault.key` (0400, owner `ente`), status `/var/lib/snaphouse/ente/status.json` (0644), renditions `/var/lib/snaphouse/public/art-ente` (owned by `art`, served by nginx). User and group `ente`; the `art` user reads the mirror through group `ente` (dirs 0750).
- Control file state gains `settings.ente_seen: list[str]` (folder ids already seen). A folder id in the index but not in `ente_seen` is appended to both `ente_seen` and `folders_enabled` once (new albums start on); an id in `ente_seen` that the owner switched off stays off.
- Index additions (additive): top-level `"ente_sync": {"state": "ok"|"failing"|"not_set_up"|"unknown", "last_success": iso8601|null, "error": str|null}`; folder `"source": "ente"` and `"title": <display title>`; piece `"source": "ente"`. Art folders and pieces carry no `source` key.

## Review Focus

- A portrait HEIC from an iPhone carries an EXIF orientation flag: the rendition must be upright (task 3 test).
- Album titles with emoji, spaces, 100+ characters, two albums with the same title, an album renamed in Ente: ids come from Ente's ids so they stay valid and unique and the switch state survives (tasks 2, 5 tests).
- A sync that dies partway, a failed album or file listing, an unshared album, and a ZIP with no image member or a hostile member name: the mirror stays consistent and nothing is wrongly deleted (task 4 tests).
- Corrupt, zero-byte, over-limit (`MAX_IMAGE_PIXELS`) or unsupported files: skipped, the index and the other pictures unaffected (tasks 2, 3, 5 tests).
- Ente not set up, mirror root missing, or export mid-write (partial file): index still publishes, art folders untouched, status says why (tasks 4, 5 tests).
- A user-made art folder literally named like an Ente id (`ente-0123456789ab`): ignored on scan, refused on create/rename (task 5 tests).

---

### Task 1: Verification spike (controller, repo: none) DONE

The spike is complete. Findings (no account details, album names or counts recorded here):

- `ente photos export` is dropped: it downloads videos. The sync uses the three commands in "Fixed decisions" instead.
- CLI 0.4.4, asset `ente-cli-v0.4.4-linux-amd64.tar.gz`, SHA-256 and `ente --version` output as in "Fixed decisions". Global flags `--json`, `--no-input`, `--offline`; headless needs `ENTE_CLI_HOME` and `ENTE_CLI_VAULT_KEY`.
- `album list --json` includes albums shared with the house account (`ownerId` differs from the account's own id). Album ids and file ids are stable across renames. `file list` needs `--all` (default limit 100).
- A plain image downloads as the original. A Live Photo (Pixel motion photo) downloads as a ZIP with the still plus a video member with extension `MP`.
- `file download --output` refuses an existing path, so the sync writes a temp name and renames.
- Removal and unshare behaviour is no longer a question: the sync deletes anything not in the current listing.

Confirmed on the hub after the spike (2026-11-18): `ente account list --json` is a list of `{id, name, email, host, apps, selected}` (strings, `apps` a list); the account is logged in for photos when its `apps` contains `photos`. Album `visibility` is `visible` for a normal album (assume `hidden` for a hidden one, case-insensitive; anything else counts as visible). File `visibility` can be null. Album and file ids are numeric strings (about 16 digits), which `ENTE_ID_RE` accepts. A shared album may be the owner's Favorites (`type: favorites`); treat every album type alike. Task 12 step 3 rechecks these on the final setup.

---

### Task 2: Mirror scan model (repo: snaphouse)

**Files:**
- Create: `lib/snaphouse/enteexport.py`
- Test: `tests/python/test_enteexport.py`

**Interfaces:**
- Produces:
  - `ENTE_PREFIX = "ente-"`
  - `ENTE_ID_RE = re.compile(r"^[0-9A-Za-z]{1,24}$")`
  - `ENTE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif"}`
  - `@dataclass(frozen=True) class EntePicture: file_id: str; source: Path; album_id: str; mtime: int; size: int` (`mtime` and `size` of the mirrored file).
  - `@dataclass(frozen=True) class EnteAlbum: folder: str; title: str; pictures: tuple[EntePicture, ...]` (`folder` is `"ente-" + album_id`).
  - `def scan_export(root: Path) -> list[EnteAlbum]`: one album per `<root>/<albumId>/` directory with a readable `album.json` (shape in "Fixed decisions"); pictures are the `files` entries whose `<fileId><ext>` exists and whose extension is in `ENTE_EXTENSIONS` (case-insensitive); sorted by `folder`, pictures by `file_id`; `[]` when `root` is missing or unreadable. Albums or files whose id fails `ENTE_ID_RE` are skipped.

- [ ] **Step 1: Write the failing tests** in `tests/python/test_enteexport.py`, building a fake mirror under `tmp_path` ("Album One", "Album Two", ids like `101`, `202`):
  - `test_scan_lists_one_album_per_directory_sorted`
  - `test_folder_is_ente_prefix_plus_album_id`
  - `test_title_comes_from_album_json`
  - `test_album_without_or_with_garbage_album_json_is_skipped`
  - `test_only_recorded_files_that_exist_with_image_extensions` (case-insensitive `.HEIC` counts; a `.MP` file, a temp name, `album.json` itself, dotfiles are never pictures; a record entry whose file is missing is dropped)
  - `test_ids_failing_the_pattern_are_skipped` (`../x`, 40 chars, empty)
  - `test_two_albums_same_title_get_distinct_folders`
  - `test_title_with_emoji_and_long_text_is_kept_verbatim_but_folder_stays_valid` (assert `re.fullmatch(r"[A-Za-z0-9 _-]{1,40}", folder)`)
  - `test_symlinks_are_not_followed`
  - `test_unreadable_or_missing_root_returns_empty`
- [ ] **Step 2: Run** `uv run pytest tests/python/test_enteexport.py -v`. Expected: FAIL (ImportError). Paste output.
- [ ] **Step 3: Implement** the interface. Do not read image contents here.
- [ ] **Step 4: Run** the same command. Expected: all PASS.
- [ ] **Step 5: Commit** `git add lib/snaphouse/enteexport.py tests/python/test_enteexport.py && git commit -m "Ente: scan the mirror into albums and pictures"` (with the attribution line).

---

### Task 3: Renditions (repo: snaphouse)

**Files:**
- Create: `lib/snaphouse/enterender.py`
- Modify: `pyproject.toml` (add `"pillow-heif>=0.16"` to the `hub` extra), `uv.lock` (run `uv lock`)
- Test: `tests/python/test_enterender.py`; check `tests/python/test_venv_extras.py` still passes.

**Interfaces:**
- Consumes: `EntePicture` from Task 2.
- Produces:
  - `RENDITION_LONG_SIDE = 1600`
  - `def rendition_path(cache: Path, folder: str, picture: EntePicture) -> Path` returns `cache / folder / f"{picture.file_id}.jpg"`.
  - `def ensure_rendition(cache: Path, folder: str, picture: EntePicture, *, max_pixels: int) -> Path | None`: returns the rendition path, generating it if missing or if its mtime differs from `picture.mtime`; sets the rendition mtime to `picture.mtime` (`os.utime`); returns `None` for corrupt, zero-byte, unsupported or over `max_pixels` sources (no exception).
  - `def prune_renditions(cache: Path, keep: set[Path]) -> int`: deletes rendition files not in `keep` and empty folder dirs; returns count removed.
  - `def register_heif() -> None`: idempotent, registers pillow-heif's opener; tolerates the package being absent (HEIC then returns `None`).

- [ ] **Step 1: Write failing tests** (`tmp_path`; build images with Pillow; HEIC sample via `pillow_heif` save when available, else `pytest.importorskip("pillow_heif")` for the HEIC test only):
  - `test_rendition_downscales_long_side_to_1600_and_is_jpeg` (4000x3000 PNG becomes 1600x1200 JPEG)
  - `test_small_image_not_upscaled`
  - `test_exif_orientation_applied` (a 200x100 JPEG with Orientation 6 yields 100x200)
  - `test_heic_source_becomes_jpeg` (importorskip)
  - `test_alpha_png_flattened_to_rgb`
  - `test_unchanged_source_is_not_regenerated` (assert rendition mtime and inode unchanged on second call)
  - `test_changed_source_mtime_regenerates`
  - `test_corrupt_zero_byte_and_oversized_return_none` (oversized via small `max_pixels`)
  - `test_prune_removes_unreferenced_and_empty_dirs`
  - `test_write_is_atomic` (no `.tmp` file left behind after success or failure)
- [ ] **Step 2: Run** `uv run pytest tests/python/test_enterender.py -v`. Expected: FAIL. Paste output.
- [ ] **Step 3: Add the dependency** with `uv add --optional hub "pillow-heif>=0.16"` (then `uv lock`); implement the module. Approach: open with Pillow, `ImageOps.exif_transpose`, `thumbnail((1600,1600))`, flatten alpha onto white, save JPEG quality 85 to a temp name in the same dir, `os.replace`, then `os.utime`.
- [ ] **Step 4: Run** `uv run pytest tests/python/test_enterender.py tests/python/test_venv_extras.py -v`. Expected: PASS.
- [ ] **Step 5: Commit** `git add lib/snaphouse/enterender.py tests/python/test_enterender.py pyproject.toml uv.lock && git commit -m "Ente: downscaled JPEG renditions with HEIC support"`.

---

### Task 4: Ente sync runner and status record (repo: snaphouse)

**Files:**
- Create: `lib/snaphouse/enterun.py`
- Test: `tests/python/test_enterun.py`

**Interfaces:**
- Consumes: the CLI commands and mirror layout in "Fixed decisions". The CLI is called only through an injected `runner` (default `subprocess.run`), always as `[cli, "--no-input", "--json", ...]` with env `ENTE_CLI_HOME` and `ENTE_CLI_VAULT_KEY` set.
- Produces:
  - `read_status(path: Path, *, now: float, stale_after: float = 3600.0) -> dict`: returns `{"state","last_success","error"}`. Missing or bad JSON gives `state: "unknown"`; an `ok` record older than `stale_after` becomes `failing` with error `"Ente sync has not run recently."`. Never raises.
  - `write_status(path: Path, state: str, *, last_success: str | None, error: str | None) -> None`: atomic, mode 0644.
  - `def is_logged_in(accounts: object) -> bool`: true only for a non-empty list with an entry naming app `photos` (case-insensitive, any key whose string value is `photos`).
  - `def wanted_albums(albums: list[dict]) -> list[dict]`: drops entries without a valid id (`ENTE_ID_RE`) and those whose `visibility` is `hidden` (case-insensitive).
  - `def diff_album(record_files: dict[str, dict], remote: list[dict]) -> tuple[list[dict], list[str]]`: returns `(to_download, to_delete)`. A remote file is downloaded if its `id` is not in the record or its `updatedAt` or `hash` differs from the record; `to_delete` is the record ids no longer remote. Ids failing `ENTE_ID_RE` are ignored.
  - `def pick_image_member(names: list[str]) -> str | None`: the first ZIP member name whose extension is in `ENTE_EXTENSIONS`, with no path separators, `..`, or absolute path; else `None`.
  - `def extract_still(zip_path: Path, dest: Path, *, max_bytes: int) -> bool`: writes only the image member (streamed, capped at `max_bytes`) to `dest`; never reads or extracts any other member; `False` when there is no image member or the cap is hit.
  - `def run_export(cli: Path, mirror_root: Path, home: Path, vault_key: Path, status: Path, *, runner=subprocess.run, clock=time.time) -> int`: exit code 0 on success or not-set-up, 1 on failure. Flow: `ente account list --json` then `is_logged_in`, else write `not_set_up` and return 0. Then `album list --json`, `wanted_albums`. For each album: `file list --album <id> --type image --type livephoto --all --json`, `diff_album`, then for each download call `file download <fileId> --album <albumId> --output <tmp>` where `<tmp>` is a new name inside `<root>/<albumId>/` (for a `livephoto` the tmp is a ZIP processed by `extract_still`; for an `image` the extension comes from the remote `name`, falling back to `.jpg`), rename into `<fileId>.<ext>`, delete the temp, update the record, and write `album.json` atomically after the album finishes (title from the album list, `updated_at`). Delete files in `to_delete` (and their record entries) after the downloads. Remove any `<root>/<albumId>/` for an album not in `wanted_albums` once the album list itself succeeded. On any failure (a list call fails, a download fails) keep the previous `last_success`, write `failing` with a plain-words error (never raw CLI output or account details), and continue with the next album when safe; a failed `file list` for an album means no deletions in that album, and a failed `album list` means no deletions anywhere. Stale temp names from a killed run are swept at the start. Returns 1 if any album failed.
  - `def main(argv=None) -> int`: argparse `--cli --mirror-root --home --vault-key --status`; entrypoint `python -m snaphouse.enterun`.

- [ ] **Step 1: Write failing tests** with a fake `runner` that serves canned JSON and writes bytes to the `--output` path (fixtures use "Album One", ids `101`, `5001`; canned error text contains `secret@example.test`):
  - `test_not_logged_in_writes_not_set_up_and_exits_zero` (empty list and list with no `photos` entry)
  - `test_every_cli_call_has_no_input_json_and_env`
  - `test_file_list_asks_for_image_and_livephoto_only_with_all` (assert the exact argv)
  - `test_first_run_downloads_files_and_writes_album_json`
  - `test_unchanged_file_is_not_downloaded_again`
  - `test_changed_updated_at_or_hash_redownloads`
  - `test_file_no_longer_listed_is_deleted_and_record_updated`
  - `test_unshared_album_directory_removed`
  - `test_hidden_album_skipped`
  - `test_failed_album_list_deletes_nothing_and_sets_failing`
  - `test_failed_file_list_keeps_that_albums_files_but_syncs_the_others`
  - `test_failed_download_leaves_no_partial_file_and_is_retried_next_run`
  - `test_download_uses_temp_name_then_renames` (the runner asserts the output path did not exist and is not the final name)
  - `test_livephoto_zip_keeps_only_the_still_and_never_reads_the_video` (ZIP with `a.jpg` and `a.MP`; the `.MP` bytes never reach disk; a member named `../x.jpg` is refused)
  - `test_zip_without_image_member_is_skipped_not_fatal`
  - `test_stale_temp_files_swept_at_start`
  - `test_status_error_is_plain_words_and_never_contains_cli_output`
  - `test_diff_album_cases`, `test_pick_image_member_cases`, `test_is_logged_in_cases`
  - `test_read_status_missing_is_unknown`, `test_read_status_garbage_is_unknown`, `test_read_status_stale_ok_becomes_failing`
  - `test_write_status_is_atomic_and_0644`
- [ ] **Step 2: Run** `uv run pytest tests/python/test_enterun.py -v`. Expected: FAIL. Paste output.
- [ ] **Step 3: Implement** the interface (stdlib only: `json`, `zipfile`, `subprocess`, `os.replace`).
- [ ] **Step 4: Run** the same command. Expected: PASS.
- [ ] **Step 5: Commit** `git commit -m "Ente: sync runner and status record"` with the added files.

---

### Task 5: Library integration and index contract (repo: snaphouse)

**Files:**
- Modify: `lib/snaphouse/artlib.py` (`Library.__init__`, `scan`, `_build_folders`, `_normalize_state`, `_default_state`, `ensure_thumbnails`, `_thumbnail_targets`, `_prune_thumbnails`, `command` landscape check near line 631)
- Modify: `docs/cross-repo-features.md` ("Art index file" at ~307 and "Art control file" at ~350: add `ente_sync`, folder `source`/`title`, piece `source`, `settings.ente_seen`, the `ente-` prefix rule, and that the frame exempts `ente-` folders from `min_aspect`)
- Test: `tests/python/test_artlib.py` (extend), `tests/js/docs-contract.test.js` (extend with doc assertions for the new key names)

**Interfaces:**
- Consumes: `scan_export`, `EnteAlbum`, `EntePicture`, `ENTE_PREFIX`, `ENTE_ID_RE` (Task 2); `ensure_rendition`, `prune_renditions`, `rendition_path` (Task 3); `read_status` (Task 4).
- Produces:
  - `Library.__init__(self, root, thumbs, state, *, ente_root: Path | None = None, ente_cache: Path | None = None, ente_status: Path | None = None, clock=time.time)` (new args keyword-only, default `None` means no Ente source: behaviour identical to today).
  - `scan()` output: the index shape in "Fixed decisions". Ente piece dict: `id`, `folder` (id), `file` (`<fileId>.jpg`), `title` (album title), `artist: None`, `source_url: None`, `width`/`height` of the rendition, `landscape` (measured), `size`/`mtime` of the rendition, `thumb`, `url: "/art/<id>"`, `source: "ente"`.
  - Constant `ENTE_PREFIX` re-exported usage: art dirs whose name `casefold()` starts with `ente-` are skipped by `scan`.
  - Metadata-only scan (`ensure_thumbnails=False`) lists an Ente piece only if its rendition already exists; the thumbnail pass (`ensure_thumbnails()`) generates renditions (counted against `THUMBNAIL_BATCH`) then thumbnails from the rendition, and prunes both.
  - `command("show", piece_id)` accepts an Ente piece regardless of landscape.

- [ ] **Step 1: Write failing tests** (fixture mirror tree built in `tmp_path`, names like "Album One"):
  - `test_scan_without_ente_root_is_unchanged` (existing fixtures: no `ente_sync`-less regressions; key `ente_sync` present with `state: "unknown"` only when `ente_status` given, else absent)
  - `test_ente_album_listed_as_folder_with_source_and_title`
  - `test_ente_piece_url_is_under_art_and_id_matches_validators` (`/art/<folder>/<stem>.jpg`, regexes from `artlib`)
  - `test_ente_pieces_counted_and_tall_piece_still_listed`
  - `test_new_album_appended_to_enabled_and_seen`
  - `test_album_switched_off_stays_off_after_rescan`
  - `test_renamed_album_keeps_switch_state` (same album id, new title in `album.json`)
  - `test_removed_picture_leaves_index_and_rendition_pruned`
  - `test_metadata_only_scan_omits_pieces_without_rendition`
  - `test_art_folder_named_ente_prefix_is_ignored_on_scan` (case-insensitive)
  - `test_corrupt_ente_file_skipped_others_listed`
  - `test_missing_export_root_gives_no_ente_folders_and_keeps_art`
  - `test_command_show_accepts_tall_ente_piece_but_rejects_tall_art_piece`
  - `test_ente_seen_survives_state_normalisation` (garbage values dropped)
  - JS: in `docs-contract.test.js`, `test_contract_documents_ente_fields` asserting the doc text contains `ente_sync`, `ente_seen`, `source`, and `ente-`.
- [ ] **Step 2: Run** `uv run pytest tests/python/test_artlib.py -v` and `node --test 'tests/js/**/*.test.js'`. Expected: new tests FAIL. Paste output.
- [ ] **Step 3: Implement** the interface. Reconcile `ente_seen`/`folders_enabled` inside `scan` and persist with `_save_state` only when they change. Folders: Ente folders sort after art folders; `builtin: False`.
- [ ] **Step 4: Run** `uv run pytest tests/python/test_artlib.py tests/python/test_artadmin.py -v` and the node command. Expected: PASS.
- [ ] **Step 5: Commit** (`git add` the four files) `"Ente: albums as read-only library folders"`.

---

### Task 6: Read-only refusals and service wiring (repo: snaphouse)

**Files:**
- Modify: `lib/snaphouse/artlib.py` (`upload`, `delete`, `restore`, `move`, `create_folder`, `rename_folder`, `delete_folder`), `lib/snaphouse/artadmin.py` (`_reject_reserved_name`, `main` args, `make Library(...)`)
- Test: `tests/python/test_artlib.py`, `tests/python/test_artadmin.py`

**Interfaces:**
- Consumes: Task 5 `Library` kwargs and `ENTE_PREFIX`.
- Produces:
  - `ENTE_READONLY_MESSAGE = "This picture comes from Ente; change it in Ente."` (module constant in `artlib.py`).
  - Library write methods raise `ArtError(409, ENTE_READONLY_MESSAGE)` when the piece id or folder name starts with `ente-` (case-insensitive) and for `create_folder`/`rename_folder`/`move`/`upload` targets with that prefix (message for creating: `"That folder name is reserved."`, 409).
  - `set_folder_enabled` and `command("show"/...)` and pin work on Ente folders (no change).
  - `artadmin.main` new optional args: `--ente-root`, `--ente-cache`, `--ente-status` (all `Path`, default `None`), passed to `Library`.

- [ ] **Step 1: Write failing tests:**
  - `test_delete_ente_piece_refused_409_with_message`
  - `test_restore_move_upload_into_ente_folder_refused`
  - `test_move_ente_piece_out_refused`
  - `test_rename_and_delete_ente_folder_refused`
  - `test_create_and_rename_to_ente_prefix_refused_any_case`
  - `test_enable_disable_ente_folder_works_and_persists`
  - `test_http_delete_ente_piece_returns_409_json_error` (via the existing artadmin test harness)
  - `test_main_accepts_ente_arguments` (parse only)
- [ ] **Step 2: Run** `uv run pytest tests/python/test_artlib.py tests/python/test_artadmin.py -v`. Expected: new FAIL. Paste output.
- [ ] **Step 3: Implement** a single helper `_refuse_if_ente(name_or_id: str) -> None` called first in each write method; extend `_reject_reserved_name` for the prefix.
- [ ] **Step 4: Run** the same command. Expected: PASS (existing art tests unchanged).
- [ ] **Step 5: Commit** `"Ente: refuse writes to Ente folders and pieces"`.

---

### Task 7: Ansible role, timer, nginx and unit changes (repo: snaphouse)

**Files:**
- Create: `roles/ente_sync/tasks/main.yml`, `roles/ente_sync/templates/snaphouse-ente-sync.service.j2`, `roles/ente_sync/templates/snaphouse-ente-sync.timer.j2`, `roles/ente_sync/files/verify.d/ente_sync.sh`, `roles/ente_sync/handlers/main.yml`
- Modify: `vars/all.yml` (`ente_cli_version: "0.4.4"`, `ente_cli_sha256: "719834d6b65e9e48a4a1d4e3e577247633f0e8a5eb2bae30278ec6c6fc536f17"`), `site.yml` and the hub's role list where `art_share` is attached (find with `grep -rn art_share site.yml vars`), `roles/art_share/files/art.conf`, `roles/art_share/templates/snaphouse-artadmin.service.j2`, `roles/art_share/tasks/main.yml` (create `/var/lib/snaphouse/public/art-ente`, owner `art`)
- Test: `tests/python/test_ente_sync_role.py` (new, pattern of `tests/python/test_artadmin_role.py`), `tests/python/test_artadmin_role.py` (extend)

**Interfaces:**
- Consumes: `python -m snaphouse.enterun` args (Task 4); `artadmin` args `--ente-root/--ente-cache/--ente-status` (Task 6); paths in "Fixed decisions".
- Produces:
  - nginx block in `art.conf` (before `location /art/`, a regex location wins over the prefix): `location ~ "^/art/(ente-[0-9A-Za-z]{1,24}/[0-9A-Za-z]{1,24}\.jpg)$" { alias /var/lib/snaphouse/public/art-ente/$1; }`.
  - Units `snaphouse-ente-sync.service` (`Type=oneshot`, `User=ente`, `ExecStart=/opt/snaphouse/.venv/bin/python -m snaphouse.enterun --cli /usr/local/bin/ente --mirror-root /var/lib/snaphouse/ente/mirror --home /var/lib/snaphouse/ente/home --vault-key /var/lib/snaphouse/ente/vault.key --status /var/lib/snaphouse/ente/status.json`, `Environment=PYTHONPATH=/opt/snaphouse/lib`, `ProtectSystem=strict`, `NoNewPrivileges=yes`, `PrivateTmp=yes`, `ReadWritePaths=/var/lib/snaphouse/ente`) and `snaphouse-ente-sync.timer` (`OnBootSec=2min`, `OnUnitActiveSec=15min`, `RandomizedDelaySec=60`).
  - artadmin unit `ExecStart` gains `--ente-root /var/lib/snaphouse/ente/mirror --ente-cache /var/lib/snaphouse/public/art-ente --ente-status /var/lib/snaphouse/ente/status.json`; `ReadWritePaths` gains `/var/lib/snaphouse/public/art-ente`.

- [ ] **Step 1: Write failing tests:**
  - `test_role_pins_cli_0_4_4_with_sha256_and_downloads_the_linux_amd64_asset` (`get_url` with `checksum: sha256:` from the var; URL names `ente-cli-v{{ ente_cli_version }}-linux-amd64.tar.gz`)
  - `test_role_installs_the_ente_binary_and_checks_version_output` (`ente --version` expected `ente 0.4.4`)
  - `test_vault_key_generated_once_with_ente_vault_key_generate_mode_0400_owner_ente` (`creates:` guards regeneration; no key value in vars)
  - `test_user_ente_system_user_no_login_and_art_in_group` (group `ente`, dirs `0750`)
  - `test_timer_every_15_minutes_and_service_runs_as_ente_hardened`
  - `test_no_password_or_account_name_in_unit_or_vars`
  - `test_artadmin_unit_passes_ente_paths_and_writes_art_ente`
  - `test_nginx_serves_ente_renditions_by_regex_alias_only` (pattern present; no `autoindex` on it; the mirror root is never served)
  - `test_hub_roles_include_ente_sync`
- [ ] **Step 2: Run** `uv run pytest tests/python/test_ente_sync_role.py tests/python/test_artadmin_role.py -v`. Expected: FAIL. Paste output.
- [ ] **Step 3: Implement.** CLI install: `ansible.builtin.get_url` with the checksum, `unarchive` to `/opt/snaphouse/ente/`, symlink `/usr/local/bin/ente`. Vault key: `command: ente vault key generate` run as `ente`, redirected to the key file once (use the exact output handling that yields a 44 character base64 line plus newline). Timer enabled and started; it exits cleanly as "not set up" until the owner logs in. The verify script checks the timer is active and `status.json` parses with `jq -e '.state'`; it must pass in the not-set-up state.
- [ ] **Step 4: Run** the two test files, then `uv run pytest tests/python` and `uvx ansible-lint`. Expected: PASS and lint clean.
- [ ] **Step 5: Commit** `"Ente: sync role, timer, rendition location"`.

---

### Task 8: Folded into Task 4

Removal reconcile (a file or album no longer listed is deleted from the mirror) is part of the core sync in Task 4, so there is no separate task. The number is kept so later task numbers stay stable.

---

### Task 9: Gallery UI (repo: snaphouse)

**Files:**
- Modify: `roles/room_picker/files/site/rooms-model.js` (`validateFolder`, `validatePiece`, `normalizeIndex` to keep `source`, `title`, `ente_sync`; rotation summary near line 685; action-gating helper), `roles/room_picker/files/site/rooms.js` (folder list ~2615, piece options menu, upload target selector), `roles/room_picker/files/site/style.css` (small status line only if needed)
- Test: `tests/js/rooms-model.test.js`, `tests/js/art-manager.test.js` as needed

**Interfaces:**
- Consumes: index additions from "Fixed decisions".
- Produces (rooms-model.js exports):
  - `isReadOnlyFolder(folder: {source?: string}): boolean` (true when `source === "ente"`).
  - `isReadOnlyPiece(piece: {source?: string}): boolean`.
  - `enteSyncLabel(sync: {state, last_success, error} | null, nowMs: number): string | null`: `null` when `sync` is null or `not_set_up`; `"Synced from Ente 5 min ago"` style text for `ok` (under a minute: `"Synced from Ente just now"`; hours and days as `"N h ago"`, `"N d ago"`); `"Ente sync is failing: <error>"` for `failing`; `"Ente sync status unknown"` for `unknown`.
  - Existing rotation summary counts a piece when `piece.landscape || piece.source === "ente"` and the folder is enabled.
  - `folderDisplayName(folder): string` returns `folder.title` when present else `folder.name`.
  - `normalizeIndex` result gains `enteSync` (object or `null`); unknown or malformed values are dropped, never throw.

- [ ] **Step 1: Write failing tests:** `isReadOnlyFolder/Piece` truthiness; `enteSyncLabel` for each state and for `null`; `rotation summary counts tall ente piece but not tall art piece`; `normalizeIndex keeps source/title and drops non-string source`; `folderDisplayName`; `ente folder with unsafe name is still dropped` (validator not loosened: a folder named `bad/../x` still fails).
- [ ] **Step 2: Run** `node --test 'tests/js/**/*.test.js'`. Expected: new FAIL. Paste output.
- [ ] **Step 3: Implement** the model functions, then wire `rooms.js`: hide Rename, Delete folder, Upload-here, per-piece Delete and Move for read-only folders and pieces; show the sync label on Ente folders (one line under the folder list header); keep switch, Show now, Pin, viewer. Show the display title in folder list and filter. HEIC is never reached by the page (renditions are JPEG), so no viewer change.
- [ ] **Step 4: Run** `node --test 'tests/js/**/*.test.js'` and `uv run pytest tests/python/test_room_picker_role.py`. Expected: PASS.
- [ ] **Step 5: Commit** `"Gallery: read-only Ente folders and sync status"`.

---

### Task 10: Frame landscape exemption (repo: nightstand; starts after snaphouse is merged)

**Files:**
- Modify: `src/nightstand/art.py` (`ArtLibrary.eligible`, `ArtLibrary.pick`; add a module constant and helper)
- Test: `tests/test_art.py`

**Interfaces:**
- Produces:
  - `ENTE_FOLDER_PREFIX = "ente-"` in `nightstand/art.py`.
  - `def is_landscape_exempt(piece: ArtPiece) -> bool`: true when `piece.id` has the form `<folder>/<file>` and `folder` starts with `ENTE_FOLDER_PREFIX` (case-insensitive).
  - `eligible(min_aspect)` and `pick(...)` include an exempt piece whenever its image decodes, whatever its aspect; non-exempt pieces behave exactly as before. `app.py` is unchanged: `_compute_art` still calls `self._art.eligible(min_aspect)` and the hub's `min_aspect` still applies to every other folder. Per-frame slices (#60) only cover commands, pins and quiet hours, so folder enablement and the exemption apply to every frame.

- [ ] **Step 1: Write failing tests** (images made with Pillow in `tmp_path`):
  - `test_eligible_keeps_tall_piece_in_ente_folder`
  - `test_eligible_still_drops_tall_piece_in_other_folder_and_top_level`
  - `test_ente_prefix_match_is_case_insensitive_and_needs_a_folder` (a top-level file named `ente-x.jpg` is not exempt)
  - `test_exempt_corrupt_image_still_skipped`
  - `test_pick_returns_tall_ente_piece_when_it_is_the_only_one`
  - `test_exempt_piece_is_sorted_among_others_by_id` (pool is shared and equal)
- [ ] **Step 2: Run** `uv run pytest tests/test_art.py -v` (nightstand's own uv). Expected: new FAIL. Paste output.
- [ ] **Step 3: Implement.** `_aspect` caching is unchanged; skip the aspect threshold for exempt pieces but still require a decodable image (reuse `_aspect`).
- [ ] **Step 4: Run** `uv run pytest tests -q`. Expected: PASS.
- [ ] **Step 5: Commit** `"Art: Ente folders ignore the landscape rule"`.

---

### Task 11: Frame sync of Ente folders and docs (repo: nightstand)

**Files:**
- Modify: `src/nightstand/sources/artsync.py` (only if a test below fails; `_is_safe_relpath` must stay as is), `AGENTS.md` (art section: the `ente-` rule and shared-folders note), `config.example.toml` comment if it mentions `min_aspect`
- Test: `tests/test_artsync.py`

**Interfaces:**
- Consumes: index shape from "Fixed decisions" (pieces with `folder: "ente-101"`, `id: "ente-<12hex>/<16hex>.jpg"`, `source: "ente"`; extra keys must be ignored).

- [ ] **Step 1: Write failing/guard tests:**
  - `test_ente_pieces_mirror_with_folder_layout_and_sidecar` (id `ente-101/5001.jpg` lands at that relative path; sidecar line 1 is the album title)
  - `test_ente_folder_switched_off_removes_its_files_and_dir`
  - `test_removed_ente_piece_deleted_on_next_sync`
  - `test_index_with_unknown_source_and_ente_sync_keys_is_tolerated`
  - `test_unsafe_ente_looking_id_still_refused` (`ente-x/../a.jpg`)
- [ ] **Step 2: Run** `uv run pytest tests/test_artsync.py -v`. Expected: most pass already (guards); paste output, including any red.
- [ ] **Step 3: Implement** only what a red test requires; update `AGENTS.md`.
- [ ] **Step 4: Run** `uv run pytest tests -q`. Expected: PASS.
- [ ] **Step 5: Commit** `"Art sync: cover Ente folders in tests and docs"`.

---

### Task 12: Install day (controller with the owner, once; no code)

- [ ] **Step 1:** Owner creates the free house account and shares the memes album and the friends-and-family album with it from the main account.
- [ ] **Step 2:** After the snaphouse PR is merged and applied, on the hub run `sudo -u ente env ENTE_CLI_HOME=/var/lib/snaphouse/ente/home ENTE_CLI_VAULT_KEY=/var/lib/snaphouse/ente/vault.key /usr/local/bin/ente account login --app photos --account house` (owner types the password; nothing else stores it).
- [ ] **Step 3:** Run `ente account list --json` and `ente photos album list --json` as that user and confirm two things the spike did not record: how a logged-in photos account appears in the account list (adjust `is_logged_in` if needed) and the `visibility` spelling of a hidden album. Then `sudo systemctl start snaphouse-ente-sync.service` and check `/var/lib/snaphouse/ente/status.json` shows `ok`.
- [ ] **Step 4:** Open the Gallery: both albums appear, switched on, with the "Synced from Ente" line. Wait for the thumbnail pass, then confirm tall pictures are listed.
- [ ] **Step 5:** Merge the nightstand PR (owner approves), let the frame sync, then add a test picture in Ente and confirm it reaches the frame; remove it and confirm it leaves (the spec's success criterion). Remove the test picture afterwards.
