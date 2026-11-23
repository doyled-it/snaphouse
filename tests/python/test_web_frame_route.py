"""The web role serves a frame's own files at /nightstand/ on every frame host.

The hub reads each frame host's mirror from http://<host>.local/nightstand/
(house-status-targets.json.j2 lists every host whose roles include
nightstand_bootstrap). The nginx alias must exist on exactly those hosts, or the
page marks that frame "Not responding" while it works.
"""
from __future__ import annotations

import json
from pathlib import Path

import yaml
from jinja2 import Environment, FileSystemLoader

REPO = Path(__file__).resolve().parents[2]
WEB_TEMPLATE = "roles/web/templates/snaphouse.conf.j2"
TARGETS_TEMPLATE = "roles/room_picker/templates/house-status-targets.json.j2"


def _hosts() -> dict:
    with open(REPO / "vars" / "hosts.yml", encoding="utf-8") as handle:
        return yaml.safe_load(handle)["snaphouse_hosts"]


def _env() -> Environment:
    env = Environment(loader=FileSystemLoader(str(REPO)), keep_trailing_newline=True, trim_blocks=True)
    env.filters["to_nice_json"] = lambda obj: json.dumps(obj, indent=4, sort_keys=True)
    env.filters["dict2items"] = lambda d: [{"key": k, "value": v} for k, v in d.items()]
    return env


def _frame_hosts(hosts: dict) -> set:
    return {name for name, spec in hosts.items() if "nightstand_bootstrap" in spec["roles"]}


def test_every_frame_host_serves_the_nightstand_alias_and_no_other_host_does():
    hosts = _hosts()
    frames = _frame_hosts(hosts)
    assert {"nightstand", "office"} <= frames
    for name in hosts:
        conf = _env().get_template(WEB_TEMPLATE).render(snaphouse_host=name, snaphouse_hosts=hosts)
        has_alias = "location /nightstand/ {" in conf
        assert has_alias == (name in frames), name


def test_every_frame_host_gets_its_own_frame_health_target():
    hosts = _hosts()
    rendered = _env().get_template(TARGETS_TEMPLATE).render(snaphouse_hosts=hosts)
    targets = {t["name"]: t for t in json.loads(rendered)["targets"]}
    # The bedroom frame keeps its established label; other frames are <host>-frame.
    assert targets["nightstand-frame"]["url"] == "http://nightstand.local/nightstand/status.json"
    assert targets["office-frame"]["url"] == "http://office.local/nightstand/status.json"
    frame_targets = [n for n in targets if n.endswith("-frame")]
    assert len(frame_targets) == len(_frame_hosts(hosts))
