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


# The kiosk shows no mouse pointer. The only input is the touchscreen, but cage
# (wlroots' xcursor manager) and Chromium both draw a cursor from the XCURSOR_*
# theme, so the role installs a fully transparent theme and the unit points at it.
CURSOR_THEME = "default"  # the name cage and wlroots load; XCURSOR_THEME is not read
CURSOR_ROOT = "/usr/local/share/snaphouse/icons"
CURSOR_SRC = ROLE / "files/cursors/left_ptr"


def _unit_env() -> dict[str, str]:
    env = {}
    for line in KIOSK_UNIT.splitlines():
        m = re.match(r'Environment="?(\w+)=([^"]*)"?$', line)
        if m:
            env[m.group(1)] = m.group(2)
    return env


def test_blank_cursor_file_is_a_valid_fully_transparent_xcursor():
    import struct

    data = CURSOR_SRC.read_bytes()
    magic, header, _version, ntoc = struct.unpack_from("<4sIII", data, 0)
    assert magic == b"Xcur" and header == 16 and ntoc >= 1
    ctype, _sub, pos = struct.unpack_from("<III", data, 16)
    assert ctype == 0xFFFD0002
    _h, _t, _s, _v, w, h, _xh, _yh, _delay = struct.unpack_from("<9I", data, pos)
    pixels = data[pos + 36 : pos + 36 + 4 * w * h]
    assert len(pixels) == 4 * w * h and w > 0 and h > 0
    assert set(pixels) == {0}, "every pixel must be fully transparent"


def test_kiosk_unit_path_resolves_default_left_ptr_to_the_blank_image():
    env = _unit_env()
    assert env.get("XCURSOR_PATH") == CURSOR_ROOT
    assert "XCURSOR_THEME" not in env, "cage and Chromium do not read XCURSOR_THEME"
    base = f"{env['XCURSOR_PATH']}/default"
    dests = {
        _module(t)[1].get("dest")
        for t in _tasks()
        if _module(t)[0].endswith("copy")
    }
    assert f"{base}/cursors/left_ptr" in dests


def test_role_installs_the_blank_cursor_theme_and_names():
    tasks = _tasks()
    base = f"{CURSOR_ROOT}/{CURSOR_THEME}"
    copies = [
        _module(t)[1]
        for t in tasks
        if _module(t)[0].endswith("copy") and str(_module(t)[1].get("dest", "")).startswith(base)
    ]
    dests = {c["dest"] for c in copies}
    assert f"{base}/cursors/left_ptr" in dests
    assert f"{base}/index.theme" in dests
    links = [t for t in tasks if _module(t)[0].endswith(".file") and _module(t)[1].get("state") == "link"]
    assert links, "no task links the other cursor names to the blank image"
    loop = links[0].get("loop") or []
    assert {"default", "pointer", "text"} <= set(loop)


# The panel's SD card moves between a Pi 3 (display auto-detected) and a Pi 5 (the
# 7 inch v1 needs its overlay named). The overlay goes in a [pi5] conditional
# section, so the same card boots on both boards.
CONFIG_TXT = "/boot/firmware/config.txt"


def _config_block_task() -> dict:
    for task in _tasks():
        key, args = next(
            ((k, v) for k, v in task.items() if k.startswith("ansible.builtin.")),
            (None, None),
        )
        if key and key.endswith("blockinfile") and args.get("path") == CONFIG_TXT:
            return task
    raise AssertionError("no blockinfile task edits config.txt")


def test_pi5_display_overlay_is_in_a_pi5_conditional_section():
    task = _config_block_task()
    block = task["ansible.builtin.blockinfile"]["block"]
    lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
    assert lines[0] == "[pi5]"
    assert "dtoverlay=vc4-kms-dsi-7inch" in block
    assert "display_auto_detect=0" in lines
    # Back to [all] so later lines in the file do not become Pi 5 only.
    assert lines[-1] == "[all]"
    assert "hardware" in (task.get("tags") or [])


def test_pi5_overlay_names_the_dsi_port_from_a_role_var():
    block = _config_block_task()["ansible.builtin.blockinfile"]["block"]
    rendered = Environment().from_string(block).render(panel_pi5_dsi_port=0)
    assert "dtoverlay=vc4-kms-dsi-7inch,dsi0" in rendered
    rendered = Environment().from_string(block).render(panel_pi5_dsi_port=1)
    assert "dtoverlay=vc4-kms-dsi-7inch\n" in rendered + "\n"
    assert "dsi0" not in rendered
    assert ROLE_VARS["panel_pi5_dsi_port"] in (0, 1)


# --------------------------------------------------------------------------- #
# the screensaver switches
# --------------------------------------------------------------------------- #

ALL_VARS = yaml.safe_load((REPO / "vars/all.yml").read_text())


def test_dim_level_is_unchanged_by_the_screensaver_work():
    # The night dim level is tuned separately on the live panel.
    assert ALL_VARS["panel_backlight"]["dim_pct"] == 15


def test_screensaver_settings_live_in_vars():
    assert ALL_VARS["panel_screensaver"] == {"enabled": True, "day_idle_s": 300}
    assert ALL_VARS["panel_backlight"]["grab_wake_tap"] is False


def test_backlight_unit_drops_the_grab_when_the_screensaver_handles_the_tap():
    unit = _render(
        "snaphouse-backlight.service.j2",
        panel_backlight={**BACKLIGHT_CTX, "grab_wake_tap": False},
        panel_screensaver={"enabled": True, "day_idle_s": 300},
    )
    assert "--no-grab" in unit


def test_backlight_unit_keeps_the_grab_without_the_screensaver():
    unit = _render(
        "snaphouse-backlight.service.j2",
        panel_backlight={**BACKLIGHT_CTX, "grab_wake_tap": False},
        panel_screensaver={"enabled": False, "day_idle_s": 300},
    )
    assert "--no-grab" not in unit


def test_backlight_unit_keeps_the_grab_when_grab_wake_tap_is_set():
    unit = _render(
        "snaphouse-backlight.service.j2",
        panel_backlight={**BACKLIGHT_CTX, "grab_wake_tap": True},
        panel_screensaver={"enabled": True, "day_idle_s": 300},
    )
    assert "--no-grab" not in unit


# --------------------------------------------------------------------------- #
# the kiosk URL carries the screensaver parameters
# --------------------------------------------------------------------------- #

SS_CTX = {
    "panel_screensaver": {"enabled": True, "day_idle_s": 300},
    "panel_backlight": {**BACKLIGHT_CTX, "grab_wake_tap": False},
}
SS_QUERY = "panel=1&idle=300&nidle=60&qs=22:00&qe=06:00"


def _panel_url(**context) -> str:
    rendered = _render("snaphouse-kiosk.env.j2", **context)
    (line,) = [ln for ln in rendered.splitlines() if ln.startswith("PANEL_URL=")]
    return line.removeprefix("PANEL_URL=")


def test_kiosk_url_gets_the_screensaver_query():
    assert _panel_url(panel_url="http://10-0-0-9.example/", **SS_CTX) == (
        f"http://10-0-0-9.example/?{SS_QUERY}"
    )


def test_kiosk_url_default_hub_name_gets_the_query_too():
    assert _panel_url(**SS_CTX) == f"http://house.local/?{SS_QUERY}"


def test_kiosk_url_merges_with_an_existing_query():
    assert _panel_url(panel_url="http://h.example/?x=1", **SS_CTX) == (
        f"http://h.example/?x=1&{SS_QUERY}"
    )


def test_kiosk_url_puts_the_query_before_the_fragment():
    assert _panel_url(panel_url="http://h.example/#listen", **SS_CTX) == (
        f"http://h.example/?{SS_QUERY}#listen"
    )
    assert _panel_url(panel_url="http://h.example/?x=1#listen", **SS_CTX) == (
        f"http://h.example/?x=1&{SS_QUERY}#listen"
    )


def test_kiosk_url_handles_a_path_without_a_slash_and_a_trailing_question_mark():
    assert _panel_url(panel_url="http://h.example/app", **SS_CTX) == (
        f"http://h.example/app?{SS_QUERY}"
    )
    assert _panel_url(panel_url="http://h.example/?", **SS_CTX) == (
        f"http://h.example/?{SS_QUERY}"
    )


def test_kiosk_url_uses_the_configured_timing():
    url = _panel_url(
        panel_screensaver={"enabled": True, "day_idle_s": 120},
        panel_backlight={**BACKLIGHT_CTX, "dim_timeout_s": 45, "quiet_start": "23:30"},
    )
    assert "idle=120&nidle=45&qs=23:30&qe=06:00" in url


def test_kiosk_url_is_unchanged_when_the_screensaver_is_off():
    assert _panel_url(
        panel_url="http://h.example/#x",
        panel_screensaver={"enabled": False, "day_idle_s": 300},
        panel_backlight=BACKLIGHT_CTX,
    ) == "http://h.example/#x"


def test_kiosk_env_change_restarts_the_kiosk():
    # The page reads its parameters once at load, so a changed env file must
    # restart the kiosk.
    (task,) = [
        t
        for t in _tasks()
        if isinstance(t.get("ansible.builtin.template"), dict)
        and t["ansible.builtin.template"].get("dest") == "/etc/snaphouse/kiosk.env"
    ]
    assert task["notify"] == "Restart the kiosk service"
    names = [h["name"] for h in _handlers()]
    assert "Restart the kiosk service" in names
