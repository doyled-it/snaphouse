"""Tests for the metadata role's now-playing service wiring (roles/metadata).

The role installs the long-running now-playing service on the hub: the systemd
unit (a template, so it carries the House stream name from the vars), its verify
check, the public-root ownership that lets it write the two JSON files, and the
restart-on-change digests. These pin the intent the role encodes without an
Ansible apply, the same way test_bt_roles.py does for the Bluetooth role. The
nginx locations that serve the files live in the room_picker role, checked here
too since they are part of the same seam.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from jinja2 import Environment, FileSystemLoader

REPO = Path(__file__).resolve().parents[2]

METADATA_TASKS = REPO / "roles/metadata/tasks/main.yml"
METADATA_HANDLERS = REPO / "roles/metadata/handlers/main.yml"
UNIT_TEMPLATE = "roles/metadata/templates/snaphouse-nowplaying.service.j2"
VERIFY_CHECK = REPO / "roles/metadata/files/verify.d/nowplaying.sh"
ROOM_PICKER_CONF = "roles/room_picker/templates/room_picker.conf.j2"


def _tasks() -> list[dict]:
    return yaml.safe_load(METADATA_TASKS.read_text())


def _handlers() -> list[dict]:
    return yaml.safe_load(METADATA_HANDLERS.read_text())


def _module(task: dict) -> tuple[str, dict]:
    for key, value in task.items():
        if key.startswith("ansible.builtin."):
            return key, value
    raise AssertionError(f"task has no module: {task.get('name')}")


def _render(path: str, **context) -> str:
    env = Environment(loader=FileSystemLoader(str(REPO)), keep_trailing_newline=True)
    return env.get_template(path).render(**context)


def _unit() -> str:
    return _render(UNIT_TEMPLATE, house_stream_name="House")


# --------------------------------------------------------------------------- #
# the systemd unit (a template, per ruling R3)
# --------------------------------------------------------------------------- #


def test_unit_passes_the_house_stream_from_the_vars():
    unit = _unit()
    # The unit is a template so it carries the House stream name rather than
    # hard-coding it; a different vars value would flow through.
    assert "--house-stream House" in unit
    # Rendered from the var, not a literal in the source template.
    assert "{{ house_stream_name }}" in Path(REPO / UNIT_TEMPLATE).read_text()


def test_unit_runs_the_module_from_the_on_device_venv():
    unit = _unit()
    assert "/opt/snaphouse/.venv/bin/python -m snaphouse.nowplaying" in unit
    assert "Environment=PYTHONPATH=/opt/snaphouse/lib" in unit


def test_unit_is_a_simple_restarting_service_after_snapserver():
    unit = _unit()
    assert "Type=simple" in unit
    assert "Restart=always" in unit
    assert "RestartSec=5" in unit
    assert "snapserver.service" in unit


def test_unit_runs_as_the_snapserver_user():
    unit = _unit()
    # It writes the cover directory the AirPlay bridge owns (snapserver), so it
    # runs as that user rather than root.
    assert "User=snapserver" in unit
    assert "Group=snapserver" in unit


# --------------------------------------------------------------------------- #
# the role tasks
# --------------------------------------------------------------------------- #


def test_role_installs_the_unit_template():
    installed = [
        task
        for task in _tasks()
        if _module(task)[0].endswith("template")
        and "snaphouse-nowplaying.service" in str(_module(task)[1].get("dest", ""))
    ]
    assert installed, "no task installs the now-playing unit template"
    _task, args = _module(installed[0]), None
    args = _module(installed[0])[1]
    assert args.get("src") == "snaphouse-nowplaying.service.j2"


def test_role_makes_the_public_root_writable_by_the_service():
    owned = []
    for task in _tasks():
        module, args = _module(task)
        if module.endswith("file") and str(args.get("path")) == "/var/lib/snaphouse/public":
            owned.append(args)
    assert owned, "no task gives the now-playing service write access to public/"
    args = owned[0]
    # The service runs as snapserver; owning the dir lets it write the JSON files
    # without widening the mode for group or other.
    assert args.get("owner") == "snapserver"
    assert args.get("mode") == "0755"


def test_enable_and_start_is_gated_on_real_systemd():
    started = []
    for task in _tasks():
        module, args = _module(task)
        if (
            module.endswith("systemd_service")
            and args.get("name") == "snaphouse-nowplaying.service"
            and args.get("state") == "started"
        ):
            started.append(task)
    assert started, "no task enables and starts the now-playing service"
    task = started[0]
    assert "common_systemd_init" in str(task.get("when"))
    assert "service" in (task.get("tags") or [])


def test_role_installs_the_verify_check():
    installed = [
        task
        for task in _tasks()
        if _module(task)[0].endswith("copy")
        and "verify.d/nowplaying.sh" in str(_module(task)[1].get("dest", ""))
    ]
    assert installed, "no task installs the now-playing verify check"


def test_handlers_are_gated_on_real_systemd():
    handlers = _handlers()
    assert handlers, "the metadata role has no handlers"
    restart = [h for h in handlers if "now-playing" in str(h).lower()]
    assert restart, "no handler restarts the now-playing service"
    for handler in handlers:
        # A notified handler ignores --skip-tags, so the when: is what keeps it
        # out of the CI container (systemd is not PID 1 there).
        assert "common_systemd_init" in str(handler.get("when"))


# --------------------------------------------------------------------------- #
# the verify check
# --------------------------------------------------------------------------- #


def test_verify_check_asserts_active_and_a_parsed_track():
    text = VERIFY_CHECK.read_text()
    assert "is-active" in text
    assert "snaphouse-nowplaying.service" in text
    assert "now-playing.json" in text
    # It parses the file and requires a track key.
    assert "json" in text
    assert "track" in text


# --------------------------------------------------------------------------- #
# the nginx locations (room_picker role, same seam)
# --------------------------------------------------------------------------- #


def test_nginx_serves_the_two_files_as_exact_match_locations():
    conf = _render(
        ROOM_PICKER_CONF,
        snaphouse_connection_upgrade="$connection_upgrade",
    )
    for name in ("/now-playing.json", "/play-history.json"):
        marker = f"location = {name} {{"
        assert marker in conf, f"no exact-match location for {name}"
        block = conf.split(marker, 1)[1].split("}", 1)[0]
        assert "root /var/lib/snaphouse/public;" in block
        assert "application/json" in block
