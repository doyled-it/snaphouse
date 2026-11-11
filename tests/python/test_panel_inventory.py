"""The control panel is a host but not a room or a snapserver client.

The panel host runs the room picker in a kiosk and plays no audio (no snapclient
role). Two inventory-driven consumers must skip it: the room list the page reads
(rooms.json) and the hub's client-settings map pushed into snapserver. Both filter
on the snapclient role, so the panel never shows as a selectable room with no
player and is never pushed to snapserver as a client that would never connect.
These pin that, and that the three audio rooms are unchanged.
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml
from jinja2 import Environment, FileSystemLoader

REPO = Path(__file__).resolve().parents[2]
HOSTS = yaml.safe_load((REPO / "vars/hosts.yml").read_text())["snaphouse_hosts"]
ROOMS_TEMPLATE = "rooms.json.j2"
SNAPCLIENT_TASKS = REPO / "roles/snapclient/tasks/main.yml"


def _render_rooms() -> dict:
    env = Environment(
        loader=FileSystemLoader(str(REPO / "roles/room_picker/templates")),
        keep_trailing_newline=True,
    )
    # Ansible's to_nice_json filter: indent 4, sorted keys. Enough to parse back.
    env.filters["to_nice_json"] = lambda obj, **_: json.dumps(obj, indent=4, sort_keys=True)
    rendered = env.get_template(ROOMS_TEMPLATE).render(snaphouse_hosts=HOSTS)
    return json.loads(rendered)


def _snapclient_hosts() -> set[str]:
    return {name for name, spec in HOSTS.items() if "snapclient" in spec["roles"]}


def _build_map_task() -> dict:
    for task in yaml.safe_load(SNAPCLIENT_TASKS.read_text()):
        if "set_fact" in str(task.get("name", "")).lower() or any(
            k.endswith("set_fact") for k in task
        ):
            if "snapclient_clients" in str(task):
                return task
    raise AssertionError("no build-client-map set_fact task in the snapclient role")


# --------------------------------------------------------------------------- #
# the panel host itself
# --------------------------------------------------------------------------- #


def test_panel_is_a_host_with_the_panel_role_and_no_audio():
    panel = HOSTS["panel"]
    assert panel["roles"] == ["common", "web", "selfupdate", "panel"]
    assert "snapclient" not in panel["roles"]
    # It carries a host id (every host does) but none of the audio room fields.
    assert panel["host_id"] == "panel"
    assert "room_label" not in panel
    assert "latency_ms" not in panel


# --------------------------------------------------------------------------- #
# not a room
# --------------------------------------------------------------------------- #


def test_panel_is_not_in_the_room_list():
    rooms = _render_rooms()["rooms"]
    ids = [room["id"] for room in rooms]
    assert "panel" not in ids


def test_the_three_audio_rooms_are_unchanged():
    rooms = _render_rooms()["rooms"]
    assert rooms == [
        {"id": "living", "label": "Living room"},
        {"id": "office", "label": "Office"},
        {"id": "bedroom", "label": "Bedroom"},
    ]


# --------------------------------------------------------------------------- #
# not a snapserver client
# --------------------------------------------------------------------------- #


def test_the_client_map_is_filtered_to_snapclient_hosts():
    # The build-map set_fact guards each item on the snapclient role, so the panel
    # (no snapclient) never enters the map.
    task = _build_map_task()
    when = task.get("when")
    assert isinstance(when, list), "the client-map when must guard per item"
    assert any("'snapclient' in item.value.roles" in cond for cond in when), when


def test_only_the_audio_hosts_are_snapclient_hosts():
    # The filter above resolves to exactly the three audio hosts; the panel is out.
    assert _snapclient_hosts() == {"house", "office", "nightstand"}
    assert "panel" not in _snapclient_hosts()
