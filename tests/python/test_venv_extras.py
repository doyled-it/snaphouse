"""The on-device venv installs the Pillow `hub` extra only on the hub.

Pillow is needed only by the AirPlay cover downscaling in
lib/snaphouse/airplay_meta.py, which runs on the hub. The selfupdate role syncs
the venv on every machine, so the `--extra hub` must be gated on something only
the hub has, or the Raspberry Pis would install a large binary wheel they never
use. The gate is the `hub_input` role.

These checks read the committed role and inventory (no Ansible apply), so they
hold in CI:

  - the sync command gates `--extra hub` on `hub_input` being in the host's roles;
  - in the inventory, `hub_input` is the hub's role alone, so only the hub syncs
    the extra and the Pis never do;
  - the extra is declared in pyproject.toml.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
SELFUPDATE_TASKS = REPO / "roles/selfupdate/tasks/main.yml"
HOSTS = REPO / "vars/hosts.yml"
PYPROJECT = REPO / "pyproject.toml"


def _sync_cmd() -> str:
    tasks = yaml.safe_load(SELFUPDATE_TASKS.read_text())
    for task in tasks:
        if task.get("name") == "Sync the on-device Python venv":
            return task["ansible.builtin.command"]["cmd"]
    raise AssertionError("the venv sync task was not found in roles/selfupdate")


def _hosts() -> dict:
    return yaml.safe_load(HOSTS.read_text())["snaphouse_hosts"]


def test_sync_gates_the_hub_extra_on_the_hub_input_role():
    cmd = _sync_cmd()
    assert "uv sync --frozen --no-dev" in cmd
    assert "--extra hub" in cmd
    # The extra must be conditional on the hub_input role, not unconditional.
    assert "'hub_input' in snaphouse_hosts[snaphouse_host].roles" in cmd


def test_only_the_hub_would_sync_the_extra():
    # hub_input is the gate, so the set of hosts that sync the extra is exactly
    # the set whose roles include hub_input. In the inventory that is only the
    # hub; the Pis (office, nightstand) never request it.
    hosts = _hosts()
    with_extra = {name for name, host in hosts.items() if "hub_input" in host["roles"]}
    assert with_extra == {"house"}
    assert "hub_input" not in hosts["office"]["roles"]
    assert "hub_input" not in hosts["nightstand"]["roles"]


def test_pyproject_declares_the_hub_extra_with_pillow():
    data = tomllib.loads(PYPROJECT.read_text())
    extras = data["project"].get("optional-dependencies", {})
    assert "hub" in extras, "pyproject must declare the [project.optional-dependencies] hub extra"
    assert any(dep.lower().startswith("pillow") for dep in extras["hub"])
