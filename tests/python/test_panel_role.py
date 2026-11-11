"""Tests for the panel role: the control-panel kiosk, backlight, and rotation.

The panel host (a Pi 3 with the official 7 inch DSI touch display) boots straight
into cage running Chromium on the hub's room picker, dims on a schedule, and
rotates display and touch together. These pin the role's intent without an Ansible
apply, the way test_artadmin_role.py does: the systemd units (restart policy,
network ordering, the pre-start hub wait), the kiosk launch flags, the hub-URL
wait loop, the rotation mapping, the verify check, and the container-safety gates
(hardware packages skipped, service start gated on real systemd).
"""

from __future__ import annotations

from pathlib import Path

import yaml
from jinja2 import Environment, FileSystemLoader

REPO = Path(__file__).resolve().parents[2]
ROLE = REPO / "roles/panel"

TASKS = ROLE / "tasks/main.yml"
HANDLERS = ROLE / "handlers/main.yml"
ROLE_VARS = yaml.safe_load((ROLE / "vars/main.yml").read_text())

KIOSK_UNIT = (ROLE / "files/snaphouse-kiosk.service").read_text()
KIOSK_LAUNCHER = (ROLE / "files/snaphouse-kiosk").read_text()
KIOSK_WAIT = (ROLE / "files/snaphouse-kiosk-wait").read_text()
VERIFY_CHECK = (ROLE / "files/verify.d/panel.sh").read_text()


def _tasks() -> list[dict]:
    return yaml.safe_load(TASKS.read_text())


def _handlers() -> list[dict]:
    return yaml.safe_load(HANDLERS.read_text())


def _module(task: dict) -> tuple[str, dict]:
    for key, value in task.items():
        if key.startswith("ansible.builtin."):
            return key, value
    raise AssertionError(f"task has no module: {task.get('name')}")


def _render(template: str, **context) -> str:
    env = Environment(loader=FileSystemLoader(str(ROLE / "templates")), keep_trailing_newline=True)
    base = {"ansible_managed": "managed by Ansible", **ROLE_VARS}
    base.update(context)
    return env.get_template(template).render(**base)


# --------------------------------------------------------------------------- #
# the kiosk systemd unit
# --------------------------------------------------------------------------- #


def test_kiosk_unit_restarts_always_and_waits_for_the_network():
    assert "Restart=always" in KIOSK_UNIT
    assert "After=network-online.target" in KIOSK_UNIT
    assert "Wants=network-online.target" in KIOSK_UNIT


def test_kiosk_unit_waits_for_the_hub_before_opening_the_page():
    # The pre-start wait is what keeps a cold boot from flashing an error page
    # before the hub is up (Review Focus 4).
    assert "ExecStartPre=/usr/local/bin/snaphouse-kiosk-wait" in KIOSK_UNIT
    assert "ExecStart=/usr/local/bin/snaphouse-kiosk" in KIOSK_UNIT


def test_kiosk_unit_runs_as_the_unprivileged_kiosk_user_on_tty1():
    assert "User=kiosk" in KIOSK_UNIT
    assert "Group=kiosk" in KIOSK_UNIT
    assert "TTYPath=/dev/tty1" in KIOSK_UNIT
    # Takes tty1 over from the login prompt so a power cut boots into the panel.
    assert "Conflicts=getty@tty1.service" in KIOSK_UNIT


def test_kiosk_unit_reads_the_hub_url_from_the_env_file():
    assert "EnvironmentFile=/etc/snaphouse/kiosk.env" in KIOSK_UNIT


# --------------------------------------------------------------------------- #
# the kiosk launcher: cage + Chromium flags
# --------------------------------------------------------------------------- #


def test_launcher_runs_cage_with_the_kiosk_chromium_flags():
    assert "exec cage --" in KIOSK_LAUNCHER
    assert "chromium" in KIOSK_LAUNCHER
    for flag in (
        "--kiosk",
        "--noerrdialogs",
        "--disable-pinch",
        "--overscroll-history-navigation=0",
    ):
        assert flag in KIOSK_LAUNCHER, flag
    # Opens the hub URL the service passes in; the address never lives in the repo.
    assert '"${url}"' in KIOSK_LAUNCHER
    assert 'url="${PANEL_URL' in KIOSK_LAUNCHER


# --------------------------------------------------------------------------- #
# the hub-URL wait loop (Review Focus 4)
# --------------------------------------------------------------------------- #


def test_wait_loop_retries_the_hub_then_gives_up_without_blocking_forever():
    assert 'url="${PANEL_URL' in KIOSK_WAIT
    assert "curl" in KIOSK_WAIT
    # A bounded retry loop: a capped attempt count and a sleep between tries.
    assert "attempts" in KIOSK_WAIT
    assert "sleep" in KIOSK_WAIT
    # After the cap it still exits 0, so the kiosk comes up even if the hub is
    # late: no blank screen forever.
    assert KIOSK_WAIT.rstrip().endswith("exit 0")


# --------------------------------------------------------------------------- #
# the backlight service unit
# --------------------------------------------------------------------------- #


def test_backlight_unit_runs_the_module_with_the_configured_levels():
    unit = _render(
        "snaphouse-backlight.service.j2",
        panel_backlight={
            "full_pct": 100,
            "dim_pct": 15,
            "dim_timeout_s": 60,
            "quiet_start": "22:00",
            "quiet_end": "06:00",
        },
    )
    assert "/opt/snaphouse/.venv/bin/python -m snaphouse.backlight" in unit
    assert "Environment=PYTHONPATH=/opt/snaphouse/lib" in unit
    assert "--full-pct 100" in unit
    assert "--dim-pct 15" in unit
    assert "--dim-timeout 60" in unit
    assert "--quiet-start 22:00" in unit
    assert "--quiet-end 06:00" in unit
    assert "Restart=always" in unit


# --------------------------------------------------------------------------- #
# the hub URL env file
# --------------------------------------------------------------------------- #


def test_kiosk_env_takes_the_hub_url_from_local_config():
    rendered = _render("snaphouse-kiosk.env.j2", panel_url="http://10-0-0-9.example/")
    assert "PANEL_URL=http://10-0-0-9.example/" in rendered


def test_kiosk_env_defaults_to_the_mdns_hub_name():
    # No panel_url in local config: fall back to the hub's .local name, no repo
    # address needed.
    rendered = _render("snaphouse-kiosk.env.j2")
    assert "PANEL_URL=http://house.local/" in rendered


# --------------------------------------------------------------------------- #
# rotation: display and touch from one setting
# --------------------------------------------------------------------------- #


def test_touch_calibration_matrix_follows_the_rotation():
    matrices = {
        0: "1 0 0 0 1 0",
        90: "0 -1 1 1 0 0",
        180: "-1 0 1 0 -1 1",
        270: "0 1 0 -1 0 1",
    }
    for rotation, matrix in matrices.items():
        rule = _render("99-snaphouse-touch-calibration.rules.j2", panel_rotation=rotation)
        assert f'LIBINPUT_CALIBRATION_MATRIX}}="{matrix}"' in rule, rotation
        assert "ID_INPUT_TOUCHSCREEN" in rule


def test_rotation_tasks_drive_display_and_touch_from_panel_rotation():
    display = [
        t for t in _tasks()
        if _module(t)[0].endswith("replace")
        and "cmdline.txt" in str(_module(t)[1].get("path", ""))
    ]
    assert display, "no task rotates the display on the kernel command line"
    # The video token the task writes is assembled from panel_rotation.
    assert "panel_display_video" in str(_module(display[0])[1])
    assert "panel_rotation" in ROLE_VARS["panel_display_video"]
    # The display rotation is hardware-only (the container has no boot partition).
    assert "hardware" in (display[0].get("tags") or [])

    touch = [
        t for t in _tasks()
        if _module(t)[0].endswith("template")
        and "touch-calibration" in str(_module(t)[1].get("dest", ""))
    ]
    assert touch, "no task installs the touch-rotation udev rule"
    assert "hardware" in (touch[0].get("tags") or [])


# --------------------------------------------------------------------------- #
# the role tasks: user, packages, verify, enablement, digests
# --------------------------------------------------------------------------- #


def test_role_creates_the_unprivileged_kiosk_user():
    users = [
        _module(t)[1] for t in _tasks()
        if _module(t)[0].endswith("user") and _module(t)[1].get("create_home")
    ]
    assert users, "no task creates the kiosk user"
    assert "panel_kiosk_user" in str(users[0].get("name"))
    assert ROLE_VARS["panel_kiosk_user"] == "kiosk"
    assert users[0].get("system") is True


def test_kiosk_packages_are_hardware_skipped_in_the_container():
    apt = [t for t in _tasks() if _module(t)[0].endswith("apt")]
    assert apt, "no apt task installs the kiosk packages"
    names = apt[0] and _module(apt[0])[1].get("name")
    assert "cage" in names and "chromium" in names
    # cage and Chromium are a heavy, display-only install; the container skips it.
    assert "hardware" in (apt[0].get("tags") or [])


def test_role_installs_the_verify_check_with_a_static_dest():
    installed = [
        _module(t)[1] for t in _tasks()
        if _module(t)[0].endswith("copy")
        and str(_module(t)[1].get("dest", "")).endswith("verify.d/panel.sh")
    ]
    assert installed, "no task installs the panel verify check"
    # A bare static dest, so the self-update pruner can read the expected set.
    assert installed[0]["dest"] == "/etc/snaphouse/verify.d/panel.sh"


def test_verify_check_asserts_the_kiosk_service_is_active():
    assert "is-active" in VERIFY_CHECK
    assert "snaphouse-kiosk.service" in VERIFY_CHECK
    assert "snaphouse-managed-check" in VERIFY_CHECK


def test_both_services_are_enabled_only_on_real_systemd():
    for service in ("snaphouse-kiosk.service", "snaphouse-backlight.service"):
        started = [
            t for t in _tasks()
            for module, args in [_module(t)]
            if module.endswith("systemd_service")
            and args.get("name") == service
            and args.get("state") == "started"
        ]
        assert started, f"no task enables and starts {service}"
        task = started[0]
        assert "common_systemd_init" in str(task.get("when")), service
        assert "service" in (task.get("tags") or []), service


def test_role_records_the_backlight_digest_for_restart_on_change():
    digests = [
        t for t in _tasks()
        if _module(t)[0].endswith("copy")
        and ".sha1" in str(_module(t)[1].get("dest", ""))
    ]
    assert digests, "no task records the backlight module digest"
    task = digests[0]
    assert "backlight.py" in str(_module(task)[1].get("content", ""))
    assert "common_systemd_init" in str(task.get("when"))
    assert "Restart the backlight service" in str(task.get("notify"))


# --------------------------------------------------------------------------- #
# handlers
# --------------------------------------------------------------------------- #


def test_handlers_restart_both_services_and_are_gated_on_systemd():
    handlers = _handlers()
    names = {h.get("name") for h in handlers}
    assert "Restart the kiosk service" in names
    assert "Restart the backlight service" in names
    for handler in handlers:
        assert "common_systemd_init" in str(handler.get("when")), handler.get("name")
        assert "service" in (handler.get("tags") or []), handler.get("name")
