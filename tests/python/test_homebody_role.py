"""Tests for the homebody_bootstrap role (Task 1 of the homebody plan).

snaphouse installs homebody on the hub the way it installs nightstand on the
bedside frame: it clones the public repo once, runs homebody's own installer
once, and from then on homebody self-updates on its own timer. Unlike
nightstand, the systemd units homebody runs under live in this role as templates
(the install interface the two repos share pins that): the Matter controller
service and the self-update service and timer. The role also installs a verify.d
check that calls homebody's own deploy/verify.sh.

These pin the intent the role encodes without an Ansible apply, the way
test_artadmin_role.py does for the art admin service; the container apply and the
focused idempotency check (tests/container/homebody_bootstrap.sh) prove the
runtime behaviour.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from jinja2 import Environment, FileSystemLoader

REPO = Path(__file__).resolve().parents[2]

ROLE = REPO / "roles/homebody_bootstrap"
ROLE_TASKS = ROLE / "tasks/main.yml"
ROLE_HANDLERS = ROLE / "handlers/main.yml"
ROLE_DEFAULTS = ROLE / "defaults/main.yml"
VERIFY_CHECK = ROLE / "files/verify.d/homebody.sh"

MATTER_UNIT = "roles/homebody_bootstrap/templates/homebody-matter.service.j2"
UPDATE_SERVICE = "roles/homebody_bootstrap/templates/homebody-update.service.j2"
UPDATE_TIMER = "roles/homebody_bootstrap/templates/homebody-update.timer.j2"


def _all_yaml() -> dict:
    return yaml.safe_load((REPO / "vars/all.yml").read_text())


def _tasks() -> list[dict]:
    return yaml.safe_load(ROLE_TASKS.read_text())


def _handlers() -> list[dict]:
    return yaml.safe_load(ROLE_HANDLERS.read_text())


def _module(task: dict) -> tuple[str, dict]:
    for key, value in task.items():
        if key.startswith("ansible.builtin."):
            return key, value
    raise AssertionError(f"task has no module: {task.get('name')}")


def _render(template: str) -> str:
    env = Environment(loader=FileSystemLoader(str(REPO)), keep_trailing_newline=True)
    return env.get_template(template).render(**_all_yaml())


# --------------------------------------------------------------------------- #
# the clone and the one-time installer (mirrors nightstand_bootstrap)
# --------------------------------------------------------------------------- #


def test_repo_url_defaults_to_the_public_homebody_repo():
    defaults = yaml.safe_load(ROLE_DEFAULTS.read_text())
    assert (
        defaults["homebody_bootstrap_repo_url"]
        == "https://github.com/doyled-it/homebody"
    )


def test_clone_is_guarded_by_creates_and_uses_the_repo_url_var():
    clones = [
        args
        for task in _tasks()
        for module, args in [_module(task)]
        if module.endswith("command")
        and "git clone" in str(args.get("cmd", ""))
    ]
    assert clones, "no task clones homebody"
    args = clones[0]
    # Cloned once: the creates guard keeps snaphouse from re-touching a
    # homebody-owned path after the hand-off, exactly like nightstand.
    assert args.get("creates") == "/opt/homebody"
    assert "{{ homebody_bootstrap_repo_url }}" in args["cmd"]
    assert "/opt/homebody" in args["cmd"]


def test_installer_runs_once_as_homebody_first_install():
    runs = [
        args
        for task in _tasks()
        for module, args in [_module(task)]
        if module.endswith("command")
        and str(args.get("cmd", "")).strip().startswith("./deploy/install.sh")
    ]
    assert runs, "no task runs homebody's installer"
    args = runs[0]
    assert args.get("chdir") == "/opt/homebody"
    # install.sh creates /etc/homebody/local.toml from the example; the creates
    # guard runs it exactly once, and homebody self-updates from then on.
    assert args.get("creates") == "/etc/homebody/local.toml"


# --------------------------------------------------------------------------- #
# the Matter controller unit
# --------------------------------------------------------------------------- #


def test_matter_unit_runs_the_controller_on_loopback():
    unit = _render(MATTER_UNIT)
    # Exactly the command the controller worker pinned: the matter-server entry
    # point from homebody's own venv, storage under /var/lib/homebody, bound to
    # loopback only.
    assert (
        "ExecStart=/opt/homebody/.venv/bin/matter-server "
        "--storage-path /var/lib/homebody/matter --port 5580" in unit
    )


def test_matter_unit_runs_as_homebody_and_restarts():
    unit = _render(MATTER_UNIT)
    assert "User=homebody" in unit
    assert "Group=homebody" in unit
    assert "Restart=always" in unit


def test_matter_unit_wants_the_network():
    unit = _render(MATTER_UNIT)
    assert "After=network-online.target" in unit
    assert "Wants=network-online.target" in unit
    assert "WantedBy=multi-user.target" in unit


def test_matter_unit_does_not_cut_itself_off_from_the_lan():
    # Matter over Wi-Fi needs IPv6 link-local and mDNS on the LAN (the design's
    # open risk). The unit must not sandbox the network away or drop IPv6 /
    # netlink, or commissioning cannot discover the Nest.
    unit = _render(MATTER_UNIT)
    assert "PrivateNetwork=yes" not in unit
    assert "IPAddressDeny" not in unit
    if "RestrictAddressFamilies=" in unit:
        line = next(
            ln for ln in unit.splitlines() if ln.startswith("RestrictAddressFamilies=")
        )
        assert "AF_INET6" in line
        assert "AF_NETLINK" in line


def test_matter_unit_can_write_only_its_state():
    unit = _render(MATTER_UNIT)
    assert "ProtectSystem=strict" in unit
    assert "NoNewPrivileges=yes" in unit
    rwp = [ln for ln in unit.splitlines() if ln.startswith("ReadWritePaths=")]
    assert rwp, "the unit sets no ReadWritePaths"
    paths = rwp[0].split("=", 1)[1].split()
    assert paths == ["/var/lib/homebody"]


# --------------------------------------------------------------------------- #
# the self-update service and timer
# --------------------------------------------------------------------------- #


def test_update_service_is_a_oneshot_that_runs_homebodys_updater():
    unit = _render(UPDATE_SERVICE)
    assert "Type=oneshot" in unit
    assert "ExecStart=/opt/homebody/deploy/update.sh" in unit
    assert "WorkingDirectory=/opt/homebody" in unit


def test_update_timer_uses_the_shared_cadence():
    unit = _render(UPDATE_TIMER)
    cadence = _all_yaml()["update_interval"]
    assert f"OnBootSec={cadence}" in unit
    assert f"OnUnitActiveSec={cadence}" in unit
    assert f"RandomizedDelaySec={_all_yaml()['update_randomized_delay']}" in unit
    assert "WantedBy=timers.target" in unit


# --------------------------------------------------------------------------- #
# the role tasks: unit install, enablement, verify
# --------------------------------------------------------------------------- #


def _installs_template(dest_name: str) -> dict:
    found = [
        args
        for task in _tasks()
        for module, args in [_module(task)]
        if module.endswith("template") and dest_name in str(args.get("dest", ""))
    ]
    assert found, f"no task installs the {dest_name} template"
    return found[0]


def test_role_installs_the_three_units():
    matter = _installs_template("homebody-matter.service")
    assert matter.get("src") == "homebody-matter.service.j2"
    assert matter.get("dest") == "/etc/systemd/system/homebody-matter.service"
    service = _installs_template("homebody-update.service")
    assert service.get("src") == "homebody-update.service.j2"
    timer = _installs_template("homebody-update.timer")
    assert timer.get("src") == "homebody-update.timer.j2"


def _started(name: str) -> list[dict]:
    return [
        task
        for task in _tasks()
        for module, args in [_module(task)]
        if module.endswith("systemd_service")
        and args.get("name") == name
        and args.get("state") == "started"
    ]


def test_matter_service_enable_is_gated_on_real_systemd():
    started = _started("homebody-matter.service")
    assert started, "no task enables and starts the Matter controller"
    task = started[0]
    assert task["ansible.builtin.systemd_service"].get("enabled") is True
    assert "common_systemd_init" in str(task.get("when"))
    assert "service" in (task.get("tags") or [])


def test_update_timer_enable_is_gated_on_real_systemd():
    started = _started("homebody-update.timer")
    assert started, "no task enables and starts the update timer"
    task = started[0]
    assert task["ansible.builtin.systemd_service"].get("enabled") is True
    assert "common_systemd_init" in str(task.get("when"))
    assert "service" in (task.get("tags") or [])


def test_handlers_reload_systemd_and_are_gated_on_systemd():
    handlers = _handlers()
    assert handlers, "the role defines no handlers"
    daemon_reloads = [
        h
        for h in handlers
        if _module(h)[0].endswith("systemd_service")
        and _module(h)[1].get("daemon_reload") is True
    ]
    assert daemon_reloads, "no handler reloads systemd after a unit change"
    for handler in handlers:
        assert "common_systemd_init" in str(handler.get("when"))
        assert "service" in (handler.get("tags") or [])


# --------------------------------------------------------------------------- #
# the verify check
# --------------------------------------------------------------------------- #


def test_role_installs_the_verify_check_with_a_static_dest():
    installed = [
        args
        for task in _tasks()
        for module, args in [_module(task)]
        if module.endswith("copy")
        and "verify.d/homebody.sh" in str(args.get("dest", ""))
    ]
    assert installed, "no task installs the homebody verify check"
    # The self-update pruner needs one static dest line (AGENTS.md), no loop,
    # variable, or templated path.
    assert installed[0].get("dest") == "/etc/snaphouse/verify.d/homebody.sh"


def test_verify_check_delegates_to_homebodys_own_verify():
    text = VERIFY_CHECK.read_text()
    assert "snaphouse-managed-check" in text
    assert "/opt/homebody/deploy/verify.sh" in text


# --------------------------------------------------------------------------- #
# inventory: the hub runs the role
# --------------------------------------------------------------------------- #


def test_hub_runs_the_homebody_bootstrap_role():
    hosts = yaml.safe_load((REPO / "vars/hosts.yml").read_text())["snaphouse_hosts"]
    assert "homebody_bootstrap" in hosts["house"]["roles"]
    # Only the hub: the Pis and the frame never run it.
    for name in ("office", "nightstand"):
        assert "homebody_bootstrap" not in hosts[name]["roles"]
