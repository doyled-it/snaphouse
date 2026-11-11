"""Tests for the art admin service wiring in the art_share role.

The art_share role already serves the art folder two ways (SMB and the read-only
JSON listing). Task 2 adds the admin service on top: a loopback HTTP API behind
nginx, its systemd unit (hardened, running as the art user), the service-owned
directories it writes, its verify check, and the restart-on-change digests.
These pin the intent the role encodes without an Ansible apply, the way
test_metadata.py does for the now-playing service. The private state lives in a
dedicated art-owned directory, since the service writes it atomically and
/var/lib/snaphouse itself is root-owned.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml
from jinja2 import Environment, FileSystemLoader

REPO = Path(__file__).resolve().parents[2]

ROLE_TASKS = REPO / "roles/art_share/tasks/main.yml"
ROLE_HANDLERS = REPO / "roles/art_share/handlers/main.yml"
UNIT_TEMPLATE = "roles/art_share/templates/snaphouse-artadmin.service.j2"
VERIFY_CHECK = REPO / "roles/art_share/files/verify.d/artadmin.sh"
ART_CONF = REPO / "roles/art_share/files/art.conf"

PUBLIC_DOCS_DIR = "/var/lib/snaphouse/public/art"
THUMBS_DIR = "/var/lib/snaphouse/public/art-thumbs"
STATE_DIR = "/var/lib/snaphouse/artadmin"
STATE_FILE = "/var/lib/snaphouse/artadmin/art-control.json"


def _tasks() -> list[dict]:
    return yaml.safe_load(ROLE_TASKS.read_text())


def _handlers() -> list[dict]:
    return yaml.safe_load(ROLE_HANDLERS.read_text())


def _module(task: dict) -> tuple[str, dict]:
    for key, value in task.items():
        if key.startswith("ansible.builtin."):
            return key, value
    raise AssertionError(f"task has no module: {task.get('name')}")


def _unit() -> str:
    env = Environment(loader=FileSystemLoader(str(REPO)), keep_trailing_newline=True)
    return env.get_template(UNIT_TEMPLATE).render()


# --------------------------------------------------------------------------- #
# the systemd unit
# --------------------------------------------------------------------------- #


def test_unit_runs_the_module_from_the_on_device_venv():
    unit = _unit()
    assert "/opt/snaphouse/.venv/bin/python -m snaphouse.artadmin" in unit
    assert "Environment=PYTHONPATH=/opt/snaphouse/lib" in unit


def test_unit_passes_every_path_and_the_loopback_port():
    unit = _unit()
    assert "--root /srv/snaphouse/art" in unit
    assert "--public-root /var/lib/snaphouse/public" in unit
    assert f"--state {STATE_FILE}" in unit
    assert "--port 8781" in unit
    # Bound to loopback only; nginx is the only thing that reaches it.
    assert "--host 127.0.0.1" in unit


def test_unit_passes_the_frame_control_paths():
    # Frame control persists beside the art state and reads the republished art
    # history for the art pin; both are passed explicitly, like --state.
    unit = _unit()
    assert "--frame-state /var/lib/snaphouse/artadmin/frame-control.json" in unit
    assert "--art-history /var/lib/snaphouse/public/art-history.json" in unit


def test_unit_runs_as_the_art_user():
    unit = _unit()
    # It owns the library (so SMB keeps working) and writes the public docs; it
    # runs as the art account rather than root.
    assert "User=art" in unit
    assert "Group=art" in unit


def test_unit_is_a_simple_restarting_service():
    unit = _unit()
    assert "Type=simple" in unit
    assert "Restart=always" in unit
    assert "RestartSec=" in unit


def test_unit_is_hardened():
    unit = _unit()
    assert "ProtectSystem=strict" in unit
    assert "NoNewPrivileges=yes" in unit
    assert "PrivateTmp=yes" in unit


def test_unit_read_write_paths_cover_only_what_it_writes():
    unit = _unit()
    rwp_lines = [ln for ln in unit.splitlines() if ln.startswith("ReadWritePaths=")]
    assert rwp_lines, "the unit sets no ReadWritePaths"
    paths = rwp_lines[0].split("=", 1)[1].split()
    # The library, the public docs dir, the thumbs dir, and the state dir: the
    # four places it must write, and nothing else.
    assert set(paths) == {
        "/srv/snaphouse/art",
        PUBLIC_DOCS_DIR,
        THUMBS_DIR,
        STATE_DIR,
    }


# --------------------------------------------------------------------------- #
# the role tasks: service-owned directories
# --------------------------------------------------------------------------- #


def _directory_specs() -> list[tuple[str, dict]]:
    """Every (path, args) a directory task creates, expanding a task-level loop so
    a looped `path: "{{ item }}"` resolves to each item."""
    specs: list[tuple[str, dict]] = []
    for task in _tasks():
        module, args = _module(task)
        if not (module.endswith("file") and args.get("state") == "directory"):
            continue
        loop = task.get("loop")
        if loop:
            for item in loop:
                specs.append((str(item), args))
        else:
            specs.append((str(args.get("path")), args))
    return specs


def test_role_creates_the_art_owned_public_docs_dir():
    docs = [a for path, a in _directory_specs() if path == PUBLIC_DOCS_DIR]
    assert docs, "no task creates the public docs subdirectory"
    assert docs[0].get("owner") == "art"
    assert docs[0].get("group") == "art"


def test_role_creates_the_art_owned_thumbs_dir():
    thumbs = [a for path, a in _directory_specs() if path == THUMBS_DIR]
    assert thumbs, "no task creates the thumbnails subdirectory"
    assert thumbs[0].get("owner") == "art"


def test_role_creates_the_art_owned_state_dir():
    state = [a for path, a in _directory_specs() if path == STATE_DIR]
    assert state, "no task creates the private state subdirectory"
    assert state[0].get("owner") == "art"


def test_role_does_not_reown_the_shared_public_root():
    # Re-owning /var/lib/snaphouse or its public root fights the web role, which
    # sets them back to root every apply (the art-share idempotency gate).
    paths = [path for path, _ in _directory_specs()]
    for shared in ("/var/lib/snaphouse", "/var/lib/snaphouse/public"):
        assert shared not in paths, f"the role must not create or re-own {shared}"


# --------------------------------------------------------------------------- #
# the role tasks: unit, enablement, verify, digests
# --------------------------------------------------------------------------- #


def test_role_installs_the_unit_template():
    installed = [
        _module(task)[1]
        for task in _tasks()
        if _module(task)[0].endswith("template")
        and "snaphouse-artadmin.service" in str(_module(task)[1].get("dest", ""))
    ]
    assert installed, "no task installs the art admin unit template"
    assert installed[0].get("src") == "snaphouse-artadmin.service.j2"


def test_enable_and_start_is_gated_on_real_systemd():
    started = [
        task
        for task in _tasks()
        for module, args in [_module(task)]
        if module.endswith("systemd_service")
        and args.get("name") == "snaphouse-artadmin.service"
        and args.get("state") == "started"
    ]
    assert started, "no task enables and starts the art admin service"
    task = started[0]
    assert "common_systemd_init" in str(task.get("when"))
    assert "service" in (task.get("tags") or [])


def test_role_installs_the_verify_check():
    installed = [
        task
        for task in _tasks()
        if _module(task)[0].endswith("copy")
        and "verify.d/artadmin.sh" in str(_module(task)[1].get("dest", ""))
    ]
    assert installed, "no task installs the art admin verify check"


def test_role_records_module_digests_for_restart_on_change():
    # The service runs its code in place from /opt/snaphouse, so a self-update
    # that changes artadmin.py or artlib.py must restart it. The digest tasks
    # (gated on the on-device checkout) pin that.
    digest_loops = [
        task.get("loop")
        for task in _tasks()
        for module, args in [_module(task)]
        if module.endswith("copy") and ".sha1" in str(args.get("dest", ""))
    ]
    flattened = [item for loop in digest_loops if loop for item in loop]
    assert "artadmin.py" in flattened
    assert "artlib.py" in flattened
    # Frame control is part of the same service, so a change to it must restart
    # the service on a self-update too.
    assert "framecontrol.py" in flattened


def test_handlers_restart_the_service_and_are_gated_on_systemd():
    handlers = _handlers()
    restart = [h for h in handlers if "art admin" in str(h).lower()]
    assert restart, "no handler restarts the art admin service"
    for handler in handlers:
        assert "common_systemd_init" in str(handler.get("when"))


# --------------------------------------------------------------------------- #
# the verify check
# --------------------------------------------------------------------------- #


def test_verify_check_asserts_active_and_a_parsed_index():
    text = VERIFY_CHECK.read_text()
    assert "is-active" in text
    assert "snaphouse-artadmin.service" in text
    assert "art-index.json" in text
    # The same service publishes frame-control.json; the check proves it too.
    assert "frame-control.json" in text


def test_verify_check_retries_before_failing():
    # The service publishes the index immediately now, but the check still retries
    # (a restart, a slow nginx reload) rather than failing on the first miss.
    text = VERIFY_CHECK.read_text()
    assert "sleep" in text, "the check does not retry"
    # A bounded retry loop driven by a deadline.
    assert "deadline" in text
    assert re.search(r"while|until", text), "no retry loop"
    # The retry window (deadline = now + N seconds) must stay under `snaphouse
    # verify`'s 60 s per-check timeout, or the harness kills the check mid-retry.
    windows = [int(n) for n in re.findall(r"date \+%s\)\s*\+\s*(\d+)", text)]
    assert windows, "no retry window found"
    assert max(windows) < 60, "the retry window must stay under the 60 s harness timeout"


# --------------------------------------------------------------------------- #
# nginx: the proxy, the exact-match docs, and the thumbs location
# --------------------------------------------------------------------------- #


def test_nginx_proxies_the_api_with_the_upload_cap():
    conf = ART_CONF.read_text()
    assert "location /api/art/ {" in conf
    assert "proxy_pass http://127.0.0.1:8781" in conf
    assert "client_max_body_size 26m;" in conf


def test_nginx_proxies_both_api_prefixes_to_the_art_admin_service():
    # Both /api/art/ (upload) and /api/frame/ (the room picker's settings and
    # pin controls) are proxied to the art admin service on loopback. Without the
    # /api/frame/ block the room picker's POSTs fall through to the `location /`
    # prefix and nginx answers 404 (issue #102).
    conf = ART_CONF.read_text()
    for prefix in ("/api/art/", "/api/frame/"):
        marker = f"location {prefix} {{"
        assert marker in conf, f"no proxy location for {prefix}"
        block = conf.split(marker, 1)[1].split("}", 1)[0]
        assert "proxy_pass http://127.0.0.1:8781;" in block, (
            f"{prefix} must proxy to the art admin service on 127.0.0.1:8781"
        )


def test_nginx_serves_the_two_documents_as_exact_match():
    conf = ART_CONF.read_text()
    for name in ("/art-index.json", "/art-control.json"):
        marker = f"location = {name} {{"
        assert marker in conf, f"no exact-match location for {name}"
        block = conf.split(marker, 1)[1].split("}", 1)[0]
        # Served from the art-owned docs subdir (nginx appends the single-segment
        # URI to root), so the files leave the root-owned public root untouched.
        assert f"root {PUBLIC_DOCS_DIR};" in block
        assert "application/json" in block


def test_nginx_serves_frame_control_as_exact_match():
    # frame-control.json is published into the same art-owned docs subdir and
    # served same-origin by an exact-match location, so it outranks the
    # room-picker `location /` prefix and never chowns the shared public root.
    conf = ART_CONF.read_text()
    marker = "location = /frame-control.json {"
    assert marker in conf, "no exact-match location for /frame-control.json"
    block = conf.split(marker, 1)[1].split("}", 1)[0]
    assert f"root {PUBLIC_DOCS_DIR};" in block
    assert "application/json" in block


def test_nginx_serves_thumbs_from_the_public_root():
    conf = ART_CONF.read_text()
    marker = "location /art-thumbs/ {"
    assert marker in conf, "no location for /art-thumbs/"
    block = conf.split(marker, 1)[1].split("}", 1)[0]
    assert "root /var/lib/snaphouse/public;" in block


def test_nginx_keeps_the_read_only_listing():
    # The existing /art/ autoindex listing (the pinned Art listing contract) must
    # stay; the frame still falls back to it until nightstand Task 4 lands.
    conf = ART_CONF.read_text()
    assert "location /art/ {" in conf
    assert "autoindex on;" in conf
