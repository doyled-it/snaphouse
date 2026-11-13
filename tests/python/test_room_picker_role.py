"""Tests for the room_picker role's templates.

The hub republishes the frame mirror (frame.png and frame.json) into a
timer-owned subdir of the public web root; nginx serves each from an exact-match
location so it outranks the `location /` prefix. These pin the two locations, the
subdir root they serve from, and the Cache-Control header that makes a phone
revalidate the mirror instead of showing a stale `<img>` from heuristic
freshness. They read the template text directly (the frame locations are static,
no Jinja), the way test_artadmin_role.py pins role intent without an apply.

The hub also republishes each frame's art history and mirror per frame into
`<frame subdir>/frames/<id>/`, served by one `location /frames/` prefix. The
house-status poll targets file is built from the inventory and gains a `frames`
list naming exactly the hosts whose roles include `nightstand_bootstrap`; these
render that template with Jinja2 (a dev-only dependency) and pin the frame list
and the nginx prefix location.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import yaml
from jinja2 import Environment, FileSystemLoader

REPO = Path(__file__).resolve().parents[2]
TEMPLATE = REPO / "roles/room_picker/templates/room_picker.conf.j2"
TARGETS_TEMPLATE = "roles/room_picker/templates/house-status-targets.json.j2"
HOSTS_FILE = REPO / "vars/hosts.yml"

FRAME_SUBDIR = "/var/lib/snaphouse/public/frame"


def _location_block(conf: str, name: str) -> str:
    """The body of the exact-match `location = <name>` block."""
    match = re.search(
        r"location\s*=\s*" + re.escape(name) + r"\s*\{(.*?)\}",
        conf,
        re.DOTALL,
    )
    assert match is not None, f"no exact-match location for {name}"
    return match.group(1)


def _prefix_location_block(conf: str, prefix: str) -> str:
    """The body of the `location <prefix>` (no `=`) prefix block."""
    match = re.search(
        r"location\s+" + re.escape(prefix) + r"\s*\{(.*?)\}",
        conf,
        re.DOTALL,
    )
    assert match is not None, f"no prefix location for {prefix}"
    return match.group(1)


def _load_hosts() -> dict:
    with HOSTS_FILE.open() as handle:
        return yaml.safe_load(handle)["snaphouse_hosts"]


def _render_targets() -> dict:
    """Render the house-status-targets template with the real inventory.

    The template uses Ansible's to_nice_json and dict2items filters, which are
    not stock Jinja2, so they are registered with simple equivalents here (the
    test only parses the resulting JSON, so the exact formatting does not matter).
    """
    env = Environment(loader=FileSystemLoader(str(REPO)), keep_trailing_newline=True)
    env.filters["to_nice_json"] = lambda obj: json.dumps(obj, indent=4, sort_keys=True)
    env.filters["dict2items"] = lambda d: [{"key": k, "value": v} for k, v in d.items()]
    rendered = env.get_template(TARGETS_TEMPLATE).render(snaphouse_hosts=_load_hosts())
    return json.loads(rendered)


def _frame_hosts() -> set:
    return {
        name
        for name, spec in _load_hosts().items()
        if "nightstand_bootstrap" in spec["roles"]
    }


def test_targets_file_is_an_object_with_targets_and_frames():
    config = _render_targets()
    assert isinstance(config, dict)
    assert isinstance(config["targets"], list)
    assert isinstance(config["frames"], list)


def test_targets_frames_list_is_exactly_the_frame_hosts():
    config = _render_targets()
    ids = {frame["id"] for frame in config["frames"]}
    assert ids == _frame_hosts()
    # The office gained the frame role; the hub has never been a frame.
    assert "office" in ids
    assert "nightstand" in ids
    assert "house" not in ids


def test_targets_frames_carry_the_nightstand_base_url_per_id():
    config = _render_targets()
    for frame in config["frames"]:
        assert frame["base"] == f"http://{frame['id']}.local/nightstand/"


def test_targets_still_lists_every_host_status_and_the_frame():
    config = _render_targets()
    names = {target["name"] for target in config["targets"]}
    assert _load_hosts().keys() <= names
    assert "nightstand-frame" in names


def test_frames_prefix_location_serves_the_subdir_with_no_cache():
    block = _prefix_location_block(TEMPLATE.read_text(), "/frames/")
    assert f"root {FRAME_SUBDIR};" in block
    assert 'add_header Cache-Control "no-cache";' in block


def test_frame_png_location_serves_the_subdir_with_no_cache():
    block = _location_block(TEMPLATE.read_text(), "/frame.png")
    assert f"root {FRAME_SUBDIR};" in block
    assert "default_type image/png;" in block
    assert 'add_header Cache-Control "no-cache";' in block


def test_frame_json_location_serves_the_subdir_with_no_cache():
    block = _location_block(TEMPLATE.read_text(), "/frame.json")
    assert f"root {FRAME_SUBDIR};" in block
    assert "default_type application/json;" in block
    assert 'add_header Cache-Control "no-cache";' in block


def test_page_location_revalidates_so_a_phone_never_mixes_old_and_new_modules():
    # The page is one HTML file and several ES modules that import each other by
    # name. With no Cache-Control a phone applies heuristic freshness per file, so
    # a new rooms.js can load beside a cached older rooms-model.js. A name the old
    # module lacks stops the whole module graph, and the page then does nothing
    # until a reload. no-cache makes every load revalidate (a cheap 304 on the
    # ETag), so the files always arrive as one consistent set.
    block = _prefix_location_block(TEMPLATE.read_text(), "/")
    assert "root /var/lib/snaphouse/room-picker;" in block
    assert 'add_header Cache-Control "no-cache";' in block


BT_TEMPLATE = "roles/room_picker/templates/bluetooth.json.j2"
TASKS_FILE = REPO / "roles/room_picker/tasks/main.yml"
PLACEHOLDER_ALIAS = "Den Speaker"


def _render_bluetooth(**extra) -> dict:
    """Render bluetooth.json.j2 with Ansible's to_json (json.dumps here)."""
    env = Environment(loader=FileSystemLoader(str(REPO)), keep_trailing_newline=True)
    env.filters["to_json"] = json.dumps
    variables = {"house_stream_name": "House", **extra}
    return json.loads(env.get_template(BT_TEMPLATE).render(**variables))


def test_a_task_templates_bluetooth_json_into_the_web_root():
    with open(TASKS_FILE, encoding="utf-8") as handle:
        tasks = yaml.safe_load(handle)
    matches = [
        t["ansible.builtin.template"]
        for t in tasks
        if "ansible.builtin.template" in t
        and t["ansible.builtin.template"].get("src") == "bluetooth.json.j2"
    ]
    assert len(matches) == 1
    assert matches[0]["dest"] == "/var/lib/snaphouse/room-picker/bluetooth.json"
    assert matches[0]["mode"] == "0644"


def test_bluetooth_json_carries_the_alias_when_set():
    assert _render_bluetooth(bt_receive_alias=PLACEHOLDER_ALIAS) == {
        "name": PLACEHOLDER_ALIAS
    }


def test_bluetooth_json_falls_back_to_the_house_stream_name():
    assert _render_bluetooth(bt_receive_alias="") == {"name": "House"}
    assert _render_bluetooth() == {"name": "House"}


def test_bluetooth_json_escapes_the_name():
    assert _render_bluetooth(bt_receive_alias='Den "Quote" \\ Speaker') == {
        "name": 'Den "Quote" \\ Speaker'
    }


def test_bluetooth_template_has_no_literal_name_and_example_is_a_placeholder():
    text = (REPO / BT_TEMPLATE).read_text(encoding="utf-8")
    assert "bt_receive_alias" in text and "house_stream_name" in text
    assert re.findall(r'"name"\s*:\s*"[^"{]', text) == []
    example = (REPO / "local.example.yml").read_text(encoding="utf-8")
    assert "# bt_receive_alias:" in example


def test_targets_mark_a_host_not_installed_from_the_inventory_flag():
    config = _render_targets()
    flags = {t["name"]: t.get("installed") for t in config["targets"]}
    for name, spec in _load_hosts().items():
        assert flags[name] is spec.get("installed", True)
    assert flags["nightstand-frame"] is True
    assert flags["panel"] is False
