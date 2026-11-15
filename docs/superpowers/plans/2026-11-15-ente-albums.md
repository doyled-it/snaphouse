# Ente albums on the frame Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Pictures in Ente albums shared with a house account reach the frame in the normal art rotation, shown as read-only Gallery folders, tall pictures included.

**Architecture:** A hub timer runs the official Ente CLI export into a private export root. The art library (`artlib.py`) scans that root as a second, read-only source, makes a downscaled JPEG rendition of every picture in a hub cache, and lists each album as a folder (`source: "ente"`) in `art-index.json`, served under `/art/`. The frame mirrors enabled folders as today and exempts folders with the reserved `ente-` prefix from the landscape rule.

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

- Folder id: `"ente-" + sha256(albumID)[:12]` (hex). `albumID` comes from the album's `metadata.json` (`ente.albumID`); if absent, use the album directory name. Always matches `^[A-Za-z0-9 _-]{1,40}$`.
- Piece file: `sha256("<album dir name>/<file name>")[:16] + ".jpg"` (matches `_FILE_NAME_RE`). Piece id: `<folder id>/<piece file>`. Piece url: `/art/<piece id>` (same scheme as art). Title shown: the album's display title (`metadata.json` `title`, else dir name).
- Reserved prefix: any art folder or created/renamed folder name starting with `ente-` (case-insensitive) is refused or ignored by the art side, so an art folder can never collide with an Ente id.
- Paths on the hub: export root `/var/lib/snaphouse/ente/export`, CLI home `/var/lib/snaphouse/ente/home`, vault key `/var/lib/snaphouse/ente/vault.key` (0400, owner `ente`), status `/var/lib/snaphouse/ente/status.json` (0644), renditions `/var/lib/snaphouse/public/art-ente` (owned by `art`, served by nginx). User and group `ente`; the `art` user reads the export root through group `ente` (dirs 0750).
- Control file state gains `settings.ente_seen: list[str]` (folder ids already seen). A folder id in the index but not in `ente_seen` is appended to both `ente_seen` and `folders_enabled` once (new albums start on); an id in `ente_seen` that the owner switched off stays off.
- Index additions (additive): top-level `"ente_sync": {"state": "ok"|"failing"|"not_set_up"|"unknown", "last_success": iso8601|null, "error": str|null}`; folder `"source": "ente"` and `"title": <display title>`; piece `"source": "ente"`. Art folders and pieces carry no `source` key.

## Review Focus

- A portrait HEIC from an iPhone carries an EXIF orientation flag: the rendition must be upright (task 3 test).
- Album names with emoji, spaces, 100+ characters, two albums with the same title, an album renamed in Ente: ids stay valid and unique, switch state follows `albumID` (tasks 2, 5 tests).
- Corrupt, zero-byte, over-limit (`MAX_IMAGE_PIXELS`) or unsupported files, and Live Photo `.mov` halves: skipped, the index and the other pictures unaffected (tasks 2, 3, 5 tests).
- Ente not set up, export root missing, or export mid-write (partial file): index still publishes, art folders untouched, status says why (tasks 4, 5 tests).
- A user-made art folder literally named like an Ente id (`ente-0123456789ab`): ignored on scan, refused on create/rename (task 5 tests).

---

### Task 1: Verification spike runbook (controller, repo: none, no code)

The controller runs this by hand on the hub (or any Linux amd64 box) with the house account. Results decide Task 8 and fill pinned values used by Task 7. Record answers in a comment on issue #172 without account details.

- [ ] **Step 1: CLI version and flags.** Pick the newest `ente-cli-v0*` release on GitHub. Download `ente-cli-vX.Y.Z-linux-amd64.tar.gz` and `SHA256SUMS`, verify the checksum. Run `ente help photos export`. Confirm these flags exist: `--album`, `--exclude-album`, `--exclude-shared`, `--exclude-hidden`, `--hardlinks`, and global `--no-input`. Record: exact version, the tarball SHA-256, and the exact flag spellings.
- [ ] **Step 2: Headless auth.** Confirm `ENTE_CLI_HOME` and `ENTE_CLI_VAULT_KEY` (file path or value form) work with `ente account add` (interactive, once) and a later non-interactive `ente photos export <dir> --no-input`. Record how the vault key must be generated and its format.
- [ ] **Step 3: Shared album export.** Share a test album ("Album One", 3 pictures including one HEIC, one portrait, one Live Photo) from the main account to the house account. Run the export. Record: directory layout, whether `metadata.json` has `ente.albumID` and `title`, the `metadata/<file>.json` fields (is there a stable per-file id? if yes, Task 2 should prefer it over the file name), how the Live Photo `.mov` appears, and whether a re-run is idempotent.
- [ ] **Step 4: Removal.** Remove one picture from the shared album in Ente, run the export again. Record the outcome as A (file moved to `Trash/` or deleted from the album folder) or B (file stays in the album folder). Also test: remove the album share entirely.
- [ ] **Step 5: Rename.** Rename the album in Ente, export again. Record whether the directory is renamed or duplicated, and whether `ente.albumID` is unchanged.
- [ ] **Step 6: Decide.** If removal is A, skip Task 8. If B, Task 8 is required and its Interfaces must be completed with the CLI's own listing command from this step.

---

### Task 2: Export scan model (repo: snaphouse)

**Files:**
- Create: `lib/snaphouse/enteexport.py`
- Test: `tests/python/test_enteexport.py`

**Interfaces:**
- Produces:
  - `ENTE_PREFIX = "ente-"`
  - `ENTE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif"}`
  - `@dataclass(frozen=True) class EntePicture: file_id: str; source: Path; album_id: str; mtime: int; size: int` where `file_id` is the 16-hex piece stem (without `.jpg`).
  - `@dataclass(frozen=True) class EnteAlbum: folder: str; title: str; pictures: tuple[EntePicture, ...]`
  - `def folder_id(album_key: str) -> str` returns `"ente-" + sha256(album_key)[:12]`.
  - `def file_stem(album_dir_name: str, file_name: str) -> str` returns `sha256(f"{album_dir_name}/{file_name}")[:16]`.
  - `def scan_export(root: Path) -> list[EnteAlbum]`: sorted by `folder`; `[]` when `root` is missing or unreadable.

- [ ] **Step 1: Write the failing tests** in `tests/python/test_enteexport.py`, building a fake export tree under `tmp_path` ("Album One", "Album Two"):
  - `test_scan_lists_one_album_per_directory_sorted`
  - `test_folder_id_uses_album_id_from_metadata_json` (two dirs renamed, same `ente.albumID`, same `folder`)
  - `test_folder_id_falls_back_to_directory_name_without_metadata`
  - `test_title_is_metadata_title_else_dir_name`
  - `test_only_image_extensions_case_insensitive` (`A.HEIC` counts; `.mov`, `.mp4`, `metadata.json`, dotfiles ignored)
  - `test_ignores_trash_metadata_dirs_and_export_json` (`Trash/`, `metadata/`, `export.json` at root, hidden dirs)
  - `test_two_albums_same_title_get_distinct_folder_ids`
  - `test_ids_valid_for_emoji_long_and_spaced_names` (assert `re.fullmatch(r"[A-Za-z0-9 _-]{1,40}", folder)` and piece stem `[0-9a-f]{16}`)
  - `test_unreadable_or_missing_root_returns_empty`
  - `test_unreadable_metadata_json_falls_back` (garbage JSON does not raise)
- [ ] **Step 2: Run** `uv run pytest tests/python/test_enteexport.py -v`. Expected: FAIL (ImportError). Paste output.
- [ ] **Step 3: Implement** the interface above. Never follow symlinks out of the root (skip symlinks). Do not read file contents here.
- [ ] **Step 4: Run** the same command. Expected: all PASS.
- [ ] **Step 5: Commit** `git add lib/snaphouse/enteexport.py tests/python/test_enteexport.py && git commit -m "Ente: scan the export tree into albums and pictures"` (with the attribution line).

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

### Task 4: Export runner and status record (repo: snaphouse)

**Files:**
- Create: `lib/snaphouse/enterun.py`
- Test: `tests/python/test_enterun.py`

**Interfaces:**
- Produces:
  - `def read_status(path: Path, *, now: float, stale_after: float = 3600.0) -> dict`: returns `{"state","last_success","error"}` per the index contract. Missing file gives `state: "unknown"`; a record whose `last_success` is older than `stale_after` while state is `ok` gives `state: "failing"`, `error: "Ente sync has not run recently."`. Never raises on bad JSON (returns `unknown`).
  - `def write_status(path: Path, state: str, *, last_success: str | None, error: str | None) -> None`: atomic, mode 0644.
  - `def run_export(cli: Path, export_root: Path, home: Path, vault_key: Path, status: Path, *, runner=subprocess.run, clock=time.time) -> int`: exit code 0 on success or not-set-up, 1 on failure. Behaviour: if `home` has no logged-in account (no `*.db`/config signalling login: treat "CLI exits with its not-logged-in error" as not set up, decided from the spike result in Task 1; until then detect by the CLI's non-zero exit plus the absence of any file under `home`) write `not_set_up` and return 0. Otherwise invoke `[cli, "photos", "export", str(export_root), "--no-input", "--exclude-hidden"]` with env `ENTE_CLI_HOME`, `ENTE_CLI_VAULT_KEY`; on success write `ok` with `last_success` set; on failure keep the previous `last_success`, set `failing` and a plain-words error (never the CLI's raw output, never account details).
  - `def main(argv=None) -> int`: argparse `--cli --export-root --home --vault-key --status`; entrypoint `python -m snaphouse.enterun`.

- [ ] **Step 1: Write failing tests** with an injected fake `runner`:
  - `test_not_set_up_writes_status_and_exits_zero`
  - `test_success_writes_ok_with_timestamp`
  - `test_failure_keeps_last_success_and_sets_plain_error` (assert the CLI stderr text, here a canned string containing "secret@example.test", is absent from the status file)
  - `test_cli_called_with_no_input_exclude_hidden_and_env`
  - `test_read_status_missing_is_unknown`, `test_read_status_garbage_is_unknown`, `test_read_status_stale_ok_becomes_failing`
  - `test_write_status_is_atomic_and_0644`
- [ ] **Step 2: Run** `uv run pytest tests/python/test_enterun.py -v`. Expected: FAIL. Paste output.
- [ ] **Step 3: Implement.** Use the exact flag spellings recorded in Task 1; if they differ from the above, the controller edits this task first.
- [ ] **Step 4: Run** the same command. Expected: PASS.
- [ ] **Step 5: Commit** `git commit -m "Ente: export runner and sync status record"` with the added files.

---

### Task 5: Library integration and index contract (repo: snaphouse)

**Files:**
- Modify: `lib/snaphouse/artlib.py` (`Library.__init__`, `scan`, `_build_folders`, `_normalize_state`, `_default_state`, `ensure_thumbnails`, `_thumbnail_targets`, `_prune_thumbnails`, `command` landscape check near line 631)
- Modify: `docs/cross-repo-features.md` ("Art index file" at ~307 and "Art control file" at ~350: add `ente_sync`, folder `source`/`title`, piece `source`, `settings.ente_seen`, the `ente-` prefix rule, and that the frame exempts `ente-` folders from `min_aspect`)
- Test: `tests/python/test_artlib.py` (extend), `tests/js/docs-contract.test.js` (extend with doc assertions for the new key names)

**Interfaces:**
- Consumes: `scan_export`, `EnteAlbum`, `EntePicture`, `ENTE_PREFIX` (Task 2); `ensure_rendition`, `prune_renditions`, `rendition_path` (Task 3); `read_status` (Task 4).
- Produces:
  - `Library.__init__(self, root, thumbs, state, *, ente_root: Path | None = None, ente_cache: Path | None = None, ente_status: Path | None = None, clock=time.time)` (new args keyword-only, default `None` means no Ente source: behaviour identical to today).
  - `scan()` output: the index shape in "Fixed decisions". Ente piece dict: `id`, `folder` (id), `file` (`<stem>.jpg`), `title` (album title), `artist: None`, `source_url: None`, `width`/`height` of the rendition, `landscape` (measured), `size`/`mtime` of the rendition, `thumb`, `url: "/art/<id>"`, `source: "ente"`.
  - Constant `ENTE_PREFIX` re-exported usage: art dirs whose name `casefold()` starts with `ente-` are skipped by `scan`.
  - Metadata-only scan (`ensure_thumbnails=False`) lists an Ente piece only if its rendition already exists; the thumbnail pass (`ensure_thumbnails()`) generates renditions (counted against `THUMBNAIL_BATCH`) then thumbnails from the rendition, and prunes both.
  - `command("show", piece_id)` accepts an Ente piece regardless of landscape.

- [ ] **Step 1: Write failing tests** (fixture export tree built in `tmp_path`, names like "Album One"):
  - `test_scan_without_ente_root_is_unchanged` (existing fixtures: no `ente_sync`-less regressions; key `ente_sync` present with `state: "unknown"` only when `ente_status` given, else absent)
  - `test_ente_album_listed_as_folder_with_source_and_title`
  - `test_ente_piece_url_is_under_art_and_id_matches_validators` (`/art/<folder>/<stem>.jpg`, regexes from `artlib`)
  - `test_ente_pieces_counted_and_tall_piece_still_listed`
  - `test_new_album_appended_to_enabled_and_seen`
  - `test_album_switched_off_stays_off_after_rescan`
  - `test_renamed_album_keeps_switch_state` (same albumID, new dir name)
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
- Create: `roles/ente_export/tasks/main.yml`, `roles/ente_export/templates/snaphouse-ente-export.service.j2`, `roles/ente_export/templates/snaphouse-ente-export.timer.j2`, `roles/ente_export/files/verify.d/ente_export.sh`, `roles/ente_export/handlers/main.yml`
- Modify: `vars/all.yml` (`ente_cli_version`, `ente_cli_sha256`: pinned values from Task 1), `site.yml` and the hub's role list in the inventory vars where `art_share` is attached (find with `grep -rn art_share site.yml vars`), `roles/art_share/files/art.conf`, `roles/art_share/templates/snaphouse-artadmin.service.j2`, `roles/art_share/tasks/main.yml` (create `/var/lib/snaphouse/public/art-ente`, owner `art`)
- Test: `tests/python/test_ente_export_role.py` (new, pattern of `tests/python/test_artadmin_role.py`), `tests/python/test_artadmin_role.py` (extend)

**Interfaces:**
- Consumes: `python -m snaphouse.enterun` args (Task 4); `artadmin` args `--ente-root/--ente-cache/--ente-status` (Task 6); paths in "Fixed decisions".
- Produces:
  - nginx block in `art.conf` (before `location /art/`, regex wins over the prefix): `location ~ "^/art/(ente-[0-9a-f]{12}/[0-9a-f]{16}\.jpg)$" { alias /var/lib/snaphouse/public/art-ente/$1; }`.
  - Units `snaphouse-ente-export.service` (`Type=oneshot`, `User=ente`, `ExecStart=/opt/snaphouse/.venv/bin/python -m snaphouse.enterun ...`, `Environment=PYTHONPATH=/opt/snaphouse/lib`, `ProtectSystem=strict`, `NoNewPrivileges=yes`, `PrivateTmp=yes`, `ReadWritePaths=/var/lib/snaphouse/ente`) and `snaphouse-ente-export.timer` (`OnBootSec=2min`, `OnUnitActiveSec=15min`, `RandomizedDelaySec=60`).
  - artadmin unit `ExecStart` gains `--ente-root /var/lib/snaphouse/ente/export --ente-cache /var/lib/snaphouse/public/art-ente --ente-status /var/lib/snaphouse/ente/status.json`; `ReadWritePaths` gains `/var/lib/snaphouse/public/art-ente`.

- [ ] **Step 1: Write failing tests:**
  - `test_role_pins_cli_version_and_checksum_and_verifies_sha256sums` (task file contains `get_url` with `checksum: sha256:` and the vars exist)
  - `test_vault_key_generated_root_owned_mode_0400_for_ente_only_not_regenerated` (`creates:` or `force: false`; owner `ente`, mode `0400`)
  - `test_user_ente_system_user_no_login_and_art_in_group` (`art` user can read the export root: group `ente`, dirs `0750`)
  - `test_timer_every_15_minutes_and_service_runs_as_ente_hardened`
  - `test_export_service_has_no_login_credentials_in_unit_or_vars` (no `password` string)
  - `test_artadmin_unit_passes_ente_paths_and_writes_art_ente`
  - `test_nginx_serves_ente_renditions_by_regex_alias_only` (pattern present; no `autoindex` on it; export root never served)
  - `test_hub_roles_include_ente_export`
- [ ] **Step 2: Run** `uv run pytest tests/python/test_ente_export_role.py tests/python/test_artadmin_role.py -v`. Expected: FAIL. Paste output.
- [ ] **Step 3: Implement.** CLI install: `ansible.builtin.get_url` with checksum from vars, `unarchive` to `/opt/snaphouse/ente/`, symlink `/usr/local/bin/ente`. Vault key: generated once with `openssl rand`, in whatever format Task 1 recorded. Timer enabled and started (it exits cleanly as "not set up" until the owner logs in). The verify script checks the timer is active and `status.json` parses with `jq -e '.state'`; it must pass in the not-set-up state.
- [ ] **Step 4: Run** the two test files, then `uv run pytest tests/python` and `uvx ansible-lint`. Expected: PASS and lint clean.
- [ ] **Step 5: Commit** `"Ente: export role, timer, rendition location"`.

---

### Task 8: Removal reconcile (repo: snaphouse) CONDITIONAL, only if the Task 1 spike found outcome B

**Files:**
- Modify: `lib/snaphouse/enterun.py`, `tests/python/test_enterun.py`

**Interfaces:**
- Consumes: `run_export` (Task 4). Produces: `def reconcile(export_root: Path, listing: dict[str, set[str]]) -> int` removing album files absent from the CLI's own listing (`album dir name -> file names`), returning the count removed; `run_export` calls it after a successful export. The listing command and its parse come from the Task 1 record and must be added to this brief by the controller before dispatch.

- [ ] **Step 1: Failing tests:** `test_reconcile_moves_missing_files_out_of_album_dir` (moved under `export_root/Trash/`, not deleted), `test_reconcile_keeps_files_in_listing`, `test_reconcile_skips_when_listing_empty_or_failed` (a failed or empty listing must never empty the library), `test_run_export_calls_reconcile_only_after_success`.
- [ ] **Step 2: Run** `uv run pytest tests/python/test_enterun.py -v`. Expected: FAIL. Paste output.
- [ ] **Step 3: Implement.** **Step 4: Run** the same file, expected PASS. **Step 5: Commit** `"Ente: reconcile removed pictures"`.

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
- Consumes: index shape from "Fixed decisions" (pieces with `folder: "ente-<12hex>"`, `id: "ente-<12hex>/<16hex>.jpg"`, `source: "ente"`; extra keys must be ignored).

- [ ] **Step 1: Write failing/guard tests:**
  - `test_ente_pieces_mirror_with_folder_layout_and_sidecar` (id `ente-0123456789ab/0123456789abcdef.jpg` lands at that relative path; sidecar line 1 is the album title)
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
- [ ] **Step 2:** After the snaphouse PR is merged and applied, on the hub run `sudo -u ente env ENTE_CLI_HOME=/var/lib/snaphouse/ente/home ENTE_CLI_VAULT_KEY=/var/lib/snaphouse/ente/vault.key /usr/local/bin/ente account add` (owner types the password; nothing else stores it).
- [ ] **Step 3:** `sudo systemctl start snaphouse-ente-export.service`; check `/var/lib/snaphouse/ente/status.json` shows `ok`.
- [ ] **Step 4:** Open the Gallery: both albums appear, switched on, with the "Synced from Ente" line. Wait for the thumbnail pass, then confirm tall pictures are listed.
- [ ] **Step 5:** Merge the nightstand PR (owner approves), let the frame sync, then add a test picture in Ente and confirm it reaches the frame; remove it and confirm it leaves (the spec's success criterion). Remove the test picture afterwards.
