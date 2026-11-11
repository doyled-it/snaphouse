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

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml
from jinja2 import Environment, FileSystemLoader

REPO = Path(__file__).resolve().parents[2]
ROLE = REPO / "roles/panel"

# The levels, timeout and quiet-hours window the backlight unit template inlines.
BACKLIGHT_CTX = {
    "full_pct": 100,
    "dim_pct": 15,
    "dim_timeout_s": 60,
    "quiet_start": "22:00",
    "quiet_end": "06:00",
}

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


def test_kiosk_unit_opens_the_page_through_the_launcher():
    # The hub wait lives inside the launcher (ExecStart), not a pre-start step, so
    # the service reads as active while it waits for the hub rather than stuck in
    # "activating" (which the verify check would read as down). Review Focus 4.
    assert "ExecStart=/usr/local/bin/snaphouse-kiosk" in KIOSK_UNIT
    assert "ExecStartPre=/usr/local/bin/snaphouse-kiosk-wait" not in KIOSK_UNIT


def test_kiosk_unit_disables_the_start_rate_limit():
    # A fast crash loop at boot (cage losing the DRM race, GPU init, seat not
    # ready) must not trip systemd's default start limit and leave the panel dead
    # until a reboot. Unlimited restart attempts, with a sane backoff.
    assert "StartLimitIntervalSec=0" in KIOSK_UNIT
    assert "RestartSec=" in KIOSK_UNIT


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


def test_launcher_waits_for_the_hub_before_opening_chromium():
    # The launcher blocks on the hub wait, then execs cage/Chromium. Because it is
    # the service's main process (Type=simple), the service is active throughout
    # the wait, and Chromium only opens once the hub answers: never on a dead page.
    wait_idx = KIOSK_LAUNCHER.find("snaphouse-kiosk-wait")
    cage_idx = KIOSK_LAUNCHER.find("exec cage")
    assert wait_idx != -1, "launcher does not run the hub wait"
    assert cage_idx != -1
    assert wait_idx < cage_idx, "the launcher must wait for the hub before cage"


# --------------------------------------------------------------------------- #
# the hub-URL wait loop (Review Focus 4)
# --------------------------------------------------------------------------- #


def test_wait_loop_is_a_retry_loop_with_no_unconditional_giveup():
    assert 'url="${PANEL_URL' in KIOSK_WAIT
    assert "curl" in KIOSK_WAIT
    assert "sleep" in KIOSK_WAIT
    # The only success is the hub answering. There is no "start the kiosk anyway"
    # branch that returns success on a dead hub (which would open an error page).
    assert "anyway" not in KIOSK_WAIT


@pytest.mark.skipif(
    shutil.which("curl") is None or shutil.which("bash") is None,
    reason="needs curl and bash to run the wait script",
)
def test_wait_script_fails_when_the_hub_never_answers():
    # Against an unreachable hub, with the attempt cap reached, the script must
    # report failure, never a false success. The launcher only execs Chromium once
    # the wait succeeds, so a dead hub can never put Chromium on an error page.
    result = subprocess.run(
        ["bash", str(ROLE / "files/snaphouse-kiosk-wait")],
        env={
            "PATH": os.environ.get("PATH", ""),
            "PANEL_URL": "http://127.0.0.1:1/",  # loopback, port 1: refused at once
            "KIOSK_WAIT_ATTEMPTS": "1",
            "KIOSK_WAIT_DELAY": "0",
        },
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode != 0, result.stderr


# --------------------------------------------------------------------------- #
# the backlight service unit
# --------------------------------------------------------------------------- #


def test_backlight_unit_runs_the_module_with_the_configured_levels():
    unit = _render("snaphouse-backlight.service.j2", panel_backlight=BACKLIGHT_CTX)
    assert "/opt/snaphouse/.venv/bin/python -m snaphouse.backlight" in unit
    assert "Environment=PYTHONPATH=/opt/snaphouse/lib" in unit
    assert "--full-pct 100" in unit
    assert "--dim-pct 15" in unit
    assert "--dim-timeout 60" in unit
    assert "--quiet-start 22:00" in unit
    assert "--quiet-end 06:00" in unit
    assert "Restart=always" in unit


def test_backlight_unit_disables_the_start_rate_limit_and_orders_after_sysinit():
    unit = _render("snaphouse-backlight.service.j2", panel_backlight=BACKLIGHT_CTX)
    # The backlight device (or the touchscreen) can bind late after a power cut;
    # NoBacklightError then exits the daemon. Unlimited restarts let it retry until
    # the device is there, rather than hitting the start limit and staying dead.
    assert "StartLimitIntervalSec=0" in unit
    assert "RestartSec=" in unit
    # sysinit.target is the usual ordering for a WantedBy=multi-user.target unit;
    # the daemon rescans for the input node, so it does not need to wait for later.
    assert "After=sysinit.target" in unit


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


def test_display_rotation_regexp_appends_strips_stale_and_is_idempotent():
    # The cmdline rotation task's regexp is hardware-gated out of CI, so pin its
    # three cases with Python's re (Ansible's replace uses the same engine): append
    # the video token when absent, strip a stale token and append the current one,
    # and leave an already-correct line unchanged (changed=0 on re-apply).
    display = next(
        t for t in _tasks()
        if _module(t)[0].endswith("replace")
        and "cmdline.txt" in str(_module(t)[1].get("path", ""))
    )
    args = _module(display)[1]

    env = Environment()
    ctx = {**ROLE_VARS, "panel_rotation": 0}
    # Resolve panel_display_video first (Ansible templates vars recursively).
    ctx["panel_display_video"] = env.from_string(ROLE_VARS["panel_display_video"]).render(**ctx)
    token = ctx["panel_display_video"]
    regexp = env.from_string(args["regexp"]).render(**ctx)
    replace = env.from_string(args["replace"]).render(**ctx)

    def apply(line: str) -> str:
        return re.sub(regexp, replace, line, flags=re.MULTILINE)

    base = "console=serial0,115200 root=PARTUUID=abcd-02 rootwait"

    # Absent: the token is appended.
    appended = apply(base)
    assert appended.endswith(" " + token)

    # Already correct: no change (the idempotency the report claims).
    assert apply(appended) == appended

    # Stale token for a different rotation/mode: stripped, current one appended.
    stale = base + " video=DSI-1:640x480@60,rotate=90"
    fixed = apply(stale)
    assert "rotate=90" not in fixed
    assert fixed.endswith(" " + token)


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
