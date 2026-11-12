"""Tests for the hub web pairing service wiring in the hub_pairing role.

issue #128 adds the pairing service: a loopback HTTP API behind nginx that opens
the Bluetooth pairing window, its systemd unit (hardened, running as root for
BlueZ), its verify check, the nginx /api/pair/ proxy, and the restart-on-change
digests. These pin the intent the role encodes without an Ansible apply, the way
test_artadmin_role.py does for the art admin service.

Every substantive task is gated on a non-empty bt_receive_adapter (the hub), so
the service lands only where a phone's audio is actually received. The port is
pinned once in vars/all.yml and shared by the unit and the nginx proxy.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml
from jinja2 import Environment, FileSystemLoader

from snaphouse import update

REPO = Path(__file__).resolve().parents[2]

ROLE_TASKS = REPO / "roles/hub_pairing/tasks/main.yml"
ROLE_HANDLERS = REPO / "roles/hub_pairing/handlers/main.yml"
UNIT_TEMPLATE = "roles/hub_pairing/templates/snaphouse-pairing.service.j2"
NGINX_TEMPLATE = "roles/hub_pairing/templates/pair.conf.j2"
VERIFY_CHECK = REPO / "roles/hub_pairing/files/verify.d/pairing.sh"


def _all_vars() -> dict:
    return yaml.safe_load((REPO / "vars/all.yml").read_text())


PAIRING_PORT = _all_vars()["pairing_port"]


def _raw_tasks() -> list[dict]:
    return yaml.safe_load(ROLE_TASKS.read_text())


def _tasks() -> list[dict]:
    """Every leaf task, flattening the role's gating block."""
    leaves: list[dict] = []
    for task in _raw_tasks():
        if "block" in task:
            leaves.extend(task["block"])
        else:
            leaves.append(task)
    return leaves


def _handlers() -> list[dict]:
    return yaml.safe_load(ROLE_HANDLERS.read_text())


def _module(task: dict) -> tuple[str, dict]:
    for key, value in task.items():
        if key.startswith("ansible.builtin."):
            return key, value
    raise AssertionError(f"task has no module: {task.get('name')}")


def _hosts() -> dict:
    return yaml.safe_load((REPO / "vars/hosts.yml").read_text())["snaphouse_hosts"]


def _render(template: str) -> str:
    env = Environment(loader=FileSystemLoader(str(REPO)), keep_trailing_newline=True)
    return env.get_template(template).render(pairing_port=PAIRING_PORT)


def _unit() -> str:
    return _render(UNIT_TEMPLATE)


def _nginx() -> str:
    return _render(NGINX_TEMPLATE)


# --------------------------------------------------------------------------- #
# inventory wiring
# --------------------------------------------------------------------------- #


def test_role_is_applied_on_the_hub_only():
    hosts = _hosts()
    assert "hub_pairing" in hosts["house"]["roles"]
    for name, host in hosts.items():
        if name == "house":
            continue
        assert "hub_pairing" not in host["roles"], f"{name} should not run hub_pairing"


def test_port_is_pinned_in_vars_and_distinct_from_the_art_service():
    # Fixed, loopback-only, and not the art admin port (8781).
    assert isinstance(PAIRING_PORT, int)
    assert PAIRING_PORT != 8781


def test_expected_checks_for_the_hub_include_the_pairing_check():
    house = update.expected_checks(REPO, "house")
    assert "pairing.sh" in house


# --------------------------------------------------------------------------- #
# the systemd unit
# --------------------------------------------------------------------------- #


def test_unit_runs_the_module_from_the_on_device_venv():
    unit = _unit()
    assert "/opt/snaphouse/.venv/bin/python -m snaphouse.pairservice" in unit
    assert "Environment=PYTHONPATH=/opt/snaphouse/lib" in unit


def test_unit_binds_loopback_on_the_pinned_port():
    unit = _unit()
    assert "--host 127.0.0.1" in unit
    assert f"--port {PAIRING_PORT}" in unit


def test_unit_runs_as_root_for_bluez():
    # Pairing needs BlueZ rights the art user must not have, and the stock Debian
    # BlueZ D-Bus policy grants agent registration to root only, so the service
    # runs as root (no User= override) and is hardened instead.
    unit = _unit()
    assert "User=art" not in unit
    assert re.search(r"^User=", unit, re.MULTILINE) is None


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


# --------------------------------------------------------------------------- #
# the role tasks
# --------------------------------------------------------------------------- #


def test_every_task_is_gated_on_a_receive_adapter():
    # The whole role sits behind one block gated on a non-empty bt_receive_adapter,
    # so it installs only on a host that receives Bluetooth audio (the hub).
    raw = _raw_tasks()
    assert len(raw) == 1 and "block" in raw[0], "the role must be one gated block"
    when = str(raw[0].get("when"))
    assert "bt_receive_adapter is defined" in when
    assert "bt_receive_adapter | length > 0" in when


def test_role_installs_the_unit_template():
    installed = [
        _module(task)[1]
        for task in _tasks()
        if _module(task)[0].endswith("template")
        and "snaphouse-pairing.service" in str(_module(task)[1].get("dest", ""))
    ]
    assert installed, "no task installs the pairing unit template"
    assert installed[0].get("src") == "snaphouse-pairing.service.j2"


def test_role_installs_the_nginx_proxy_template():
    installed = [
        _module(task)[1]
        for task in _tasks()
        if _module(task)[0].endswith("template")
        and "snaphouse.d/pair.conf" in str(_module(task)[1].get("dest", ""))
    ]
    assert installed, "no task installs the nginx proxy template"
    assert installed[0].get("src") == "pair.conf.j2"


def test_enable_and_start_is_gated_on_real_systemd():
    started = [
        task
        for task in _tasks()
        for module, args in [_module(task)]
        if module.endswith("systemd_service")
        and args.get("name") == "snaphouse-pairing.service"
        and args.get("state") == "started"
    ]
    assert started, "no task enables and starts the pairing service"
    task = started[0]
    assert "common_systemd_init" in str(task.get("when"))
    assert "service" in (task.get("tags") or [])


def test_role_installs_the_verify_check_with_a_static_dest():
    installed = [
        _module(task)[1]
        for task in _tasks()
        if _module(task)[0].endswith("copy")
        and "verify.d/pairing.sh" in str(_module(task)[1].get("dest", ""))
    ]
    assert installed, "no task installs the pairing verify check"
    # A bare static dest, so the self-update pruner can read the check name
    # (AGENTS.md: static dest per shipped check).
    assert installed[0]["dest"] == "/etc/snaphouse/verify.d/pairing.sh"


def test_role_records_module_digests_for_restart_on_change():
    digest_loops = [
        task.get("loop")
        for task in _tasks()
        for module, args in [_module(task)]
        if module.endswith("copy") and ".sha1" in str(args.get("dest", ""))
    ]
    flattened = [item for loop in digest_loops if loop for item in loop]
    assert "pairservice.py" in flattened
    assert "pairing.py" in flattened


def test_handlers_restart_the_service_and_are_gated_on_systemd():
    handlers = _handlers()
    restart = [h for h in handlers if "pairing service" in str(h).lower()]
    assert restart, "no handler restarts the pairing service"
    for handler in handlers:
        assert "common_systemd_init" in str(handler.get("when"))


# --------------------------------------------------------------------------- #
# the verify check
# --------------------------------------------------------------------------- #


def test_verify_check_asserts_active_and_a_parsed_state():
    text = VERIFY_CHECK.read_text()
    assert "is-active" in text
    assert "snaphouse-pairing.service" in text
    assert "/api/pair/state" in text


def test_verify_check_carries_the_managed_marker():
    assert update.verify.MANAGED_MARKER in VERIFY_CHECK.read_text()


def test_verify_check_retries_under_the_harness_timeout():
    text = VERIFY_CHECK.read_text()
    assert "sleep" in text
    assert "deadline" in text
    windows = [int(n) for n in re.findall(r"date \+%s\)\s*\+\s*(\d+)", text)]
    assert windows, "no retry window found"
    assert max(windows) < 60, "the retry window must stay under the 60 s harness timeout"


def test_verify_check_never_prints_an_address():
    text = VERIFY_CHECK.read_text()
    # No adapter/device fields are read; the state endpoint is the only call and
    # the check greps no address out of it.
    assert "passkey" not in text
    assert "device" not in text


# --------------------------------------------------------------------------- #
# nginx proxy
# --------------------------------------------------------------------------- #


def test_nginx_proxies_the_pair_api_to_the_loopback_service():
    conf = _nginx()
    assert "location /api/pair/ {" in conf
    assert f"proxy_pass http://127.0.0.1:{PAIRING_PORT};" in conf
    # A small cap: the pairing bodies are tiny JSON.
    assert "client_max_body_size" in conf


def test_nginx_proxy_is_an_exact_prefix_like_the_other_apis():
    # /api/pair/ is its own prefix block, the same shape as /api/art/ and
    # /api/frame/, so the room picker's POSTs do not fall through to `location /`.
    conf = _nginx()
    block = conf.split("location /api/pair/ {", 1)[1].split("}", 1)[0]
    assert "proxy_pass" in block
