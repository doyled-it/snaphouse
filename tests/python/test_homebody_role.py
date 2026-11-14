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
    # Contract with homebody's deploy/install.sh: it writes
    # /var/lib/homebody/update/update.state (HOMEBODY_STATE under the default
    # HOMEBODY_STATE_ROOT) only at the very end of a successful install. The
    # creates guard must be that file, not /etc/homebody/local.toml, which
    # install.sh writes before its dependency sync: a first run that failed
    # after that point would otherwise never be retried (issue #118).
    assert args.get("creates") == "/var/lib/homebody/update/update.state"


# --------------------------------------------------------------------------- #
# the Matter controller unit
# --------------------------------------------------------------------------- #


def test_matter_unit_runs_the_controller_on_loopback():
    unit = _render(MATTER_UNIT)
    exec_lines = [l for l in unit.splitlines() if l.startswith("ExecStart=")]
    assert len(exec_lines) == 1
    argv = exec_lines[0].removeprefix("ExecStart=").split()
    # The matter-server entry point from homebody's own venv.
    assert argv[0] == "/opt/homebody/.venv/bin/matter-server"

    def flag(name):
        assert argv.count(name) == 1, f"{name} must appear exactly once in {argv}"
        return argv[argv.index(name) + 1]

    assert flag("--storage-path") == "/var/lib/homebody/matter"
    assert flag("--port") == "5580"
    # python-matter-server binds every interface unless told otherwise; the
    # controller API is unauthenticated, so it must listen on loopback only.
    assert flag("--listen-address") == "127.0.0.1"


def test_matter_unit_passes_the_paa_root_cert_dir():
    # python-matter-server otherwise stores the PAA root certificates inside its
    # own package directory, which is read-only under ProtectSystem=strict.
    unit = _render(MATTER_UNIT)
    argv = [l for l in unit.splitlines() if l.startswith("ExecStart=")][0].split()
    assert argv.count("--paa-root-cert-dir") == 1
    assert argv[argv.index("--paa-root-cert-dir") + 1] == (
        "/var/lib/homebody/paa-root-certs"
    )


def test_matter_unit_binds_a_homebody_dir_onto_chip_data():
    # The CHIP SDK writes its factory config under a hard-coded /data. A bare host
    # has none, so a homebody-owned directory is bound there.
    unit = _render(MATTER_UNIT)
    binds = [ln for ln in unit.splitlines() if ln.startswith("BindPaths=")]
    assert binds == ["BindPaths=/var/lib/homebody/chip-data:/data"]


def test_role_creates_the_chip_data_and_paa_dirs_owned_by_homebody():
    tasks = _tasks()
    names = [t.get("name") for t in tasks]
    for path in ("/var/lib/homebody/chip-data", "/var/lib/homebody/paa-root-certs"):
        found = [
            (i, args)
            for i, task in enumerate(tasks)
            for module, args in [_module(task)]
            if module.endswith(".file") and args.get("path") == path
        ]
        assert found, f"no task creates {path}"
        idx, args = found[0]
        assert args.get("state") == "directory"
        assert args.get("owner") == "homebody"
        assert args.get("group") == "homebody"
        assert args.get("mode") == "0750"
        # Created after the installer (which makes the homebody user) and before
        # the unit is started.
        started = names.index("Enable and start the homebody Matter controller")
        installer = names.index("Run the homebody installer once")
        assert installer < idx < started


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


# --------------------------------------------------------------------------- #
# the homebody daemon unit, its status dir, and its nginx routes (Task 4)
# --------------------------------------------------------------------------- #

DAEMON_UNIT = "roles/homebody_bootstrap/templates/homebody.service.j2"
NGINX_CONF = "roles/homebody_bootstrap/templates/homebody.conf.j2"
STATUS_VERIFY = ROLE / "files/verify.d/homebody_status.sh"
STATUS_DIR = "/var/lib/snaphouse/public/homebody"


def _render_all(template: str) -> str:
    env = Environment(loader=FileSystemLoader(str(REPO)), keep_trailing_newline=True)
    return env.get_template(template).render(
        **_all_yaml(), **yaml.safe_load(ROLE_DEFAULTS.read_text())
    )


def _tasks_by_name(name: str) -> dict:
    found = [t for t in _tasks() if t.get("name") == name]
    assert found, f"no task named {name!r}"
    return found[0]


def test_api_port_is_pinned_at_8783():
    assert _all_yaml()["homebody_api_port"] == 8783


def test_daemon_unit_runs_homebody_run_as_homebody():
    unit = _render_all(DAEMON_UNIT)
    assert "ExecStart=/opt/homebody/.venv/bin/homebody run" in unit.splitlines()
    assert "User=homebody" in unit
    assert "Group=homebody" in unit
    assert "Restart=always" in unit
    assert "WantedBy=multi-user.target" in unit


def test_daemon_unit_follows_the_matter_controller():
    unit = _render_all(DAEMON_UNIT)
    assert "After=network-online.target homebody-matter.service" in unit
    assert "Wants=network-online.target homebody-matter.service" in unit


def test_daemon_unit_can_probe_presence_but_not_escalate():
    unit = _render_all(DAEMON_UNIT)
    # arping and l2ping need raw sockets.
    assert "AmbientCapabilities=CAP_NET_RAW" in unit
    assert "CapabilityBoundingSet=CAP_NET_RAW" in unit
    assert "ProtectSystem=strict" in unit
    assert "NoNewPrivileges=yes" in unit
    assert "PrivateNetwork=yes" not in unit
    rwp = [ln for ln in unit.splitlines() if ln.startswith("ReadWritePaths=")]
    assert rwp, "the unit sets no ReadWritePaths"
    assert rwp[0].split("=", 1)[1].split() == ["/var/lib/homebody", STATUS_DIR]


def test_update_script_and_role_agree_on_the_unit_name():
    # deploy/update.sh in homebody restarts "homebody-matter homebody".
    assert "ExecStart=/opt/homebody/.venv/bin/homebody run" in _render_all(DAEMON_UNIT)
    assert _installs_template("homebody.service")["dest"] == (
        "/etc/systemd/system/homebody.service"
    )
    assert _started("homebody.service")


def test_status_dir_is_owned_by_homebody_and_the_public_root_is_not_touched():
    tasks = _tasks()
    dirs = [
        (i, args)
        for i, task in enumerate(tasks)
        for module, args in [_module(task)]
        if module.endswith(".file") and args.get("path") == "{{ homebody_bootstrap_status_dir }}"
    ]
    assert dirs, "no task creates the homebody status dir"
    assert yaml.safe_load(ROLE_DEFAULTS.read_text())["homebody_bootstrap_status_dir"] == STATUS_DIR
    idx, args = dirs[0]
    assert args["state"] == "directory"
    assert args["owner"] == "homebody"
    assert args["group"] == "homebody"
    assert args["mode"] == "0755"
    names = [t.get("name") for t in tasks]
    assert names.index("Run the homebody installer once") < idx
    assert idx < names.index("Enable and start the homebody daemon")
    # The shared public root is the web role's; never chown it from here.
    for task in tasks:
        module, args = _module(task)
        if not isinstance(args, dict):
            continue
        assert args.get("path") != "/var/lib/snaphouse/public"
        assert args.get("recurse") is not True


def test_nginx_serves_the_status_file_exact_match():
    conf = _render_all(NGINX_CONF)
    assert "location = /homebody.json {" in conf
    assert f"root {STATUS_DIR};" in conf
    assert "default_type application/json;" in conf


def test_nginx_proxies_the_settings_api_to_the_pinned_port():
    conf = _render_all(NGINX_CONF)
    assert "location /api/homebody/ {" in conf
    assert "proxy_pass http://127.0.0.1:8783;" in conf
    assert "proxy_set_header Host $host;" in conf
    assert "client_max_body_size 64k;" in conf


def test_role_installs_the_nginx_conf_and_reloads_nginx():
    tmpl = _installs_template("snaphouse.d/homebody.conf")
    assert tmpl["src"] == "homebody.conf.j2"
    assert tmpl["dest"] == "/etc/nginx/snaphouse.d/homebody.conf"
    task = next(
        t for t in _tasks() if "snaphouse.d/homebody.conf" in str(_module(t)[1].get("dest"))
    )
    assert "Reload nginx for homebody_bootstrap" in task["notify"]
    handler = next(h for h in _handlers() if h["name"] == "Reload nginx for homebody_bootstrap")
    assert "common_systemd_init" in str(handler.get("when"))
    assert "service" in handler.get("tags")


def test_daemon_start_is_gated_on_real_systemd_so_a_missing_run_cannot_fail_ci():
    # The CI container has no init, so the unit never starts there, and a stub
    # /opt/homebody without `homebody run` cannot fail the play.
    task = _started("homebody.service")[0]
    assert task["ansible.builtin.systemd_service"]["enabled"] is True
    assert "common_systemd_init" in str(task.get("when"))
    assert "service" in task.get("tags")
    restart = next(h for h in _handlers() if h["name"] == "Restart the homebody daemon")
    assert _module(restart)[1]["name"] == "homebody.service"
    assert "common_systemd_init" in str(restart.get("when"))


def test_status_verify_check_is_installed_statically_and_probes_the_file():
    installed = [
        args
        for task in _tasks()
        for module, args in [_module(task)]
        if module.endswith("copy")
        and "verify.d/homebody_status.sh" in str(args.get("dest", ""))
    ]
    assert installed
    assert installed[0]["dest"] == "/etc/snaphouse/verify.d/homebody_status.sh"
    text = STATUS_VERIFY.read_text()
    assert "snaphouse-managed-check" in text
    assert "systemctl is-active" in text
    assert "homebody.service" in text
    assert "http://localhost/homebody.json" in text
    assert "\u2014" not in text


def test_role_installs_the_presence_probe_tools():
    # homebody's Wi-Fi presence probe runs `arping`, which Debian does not ship by
    # default; without it every probe fails and nobody is ever seen home.
    packages = []
    for task in _tasks():
        apt = task.get("ansible.builtin.apt")
        if apt:
            name = apt.get("name")
            packages.extend(name if isinstance(name, list) else [name])
    assert "iputils-arping" in packages


def test_homebody_api_route_sets_the_client_address():
    conf = _render_all(NGINX_CONF)
    block = conf.split("location /api/homebody/ {", 1)[1].split("}", 1)[0]
    assert "proxy_set_header X-Real-IP $remote_addr;" in block
