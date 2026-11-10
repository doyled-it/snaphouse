"""Tests for the Bluetooth role and the hub's Bluetooth input service.

These pin the hardware fixes from issue #41 that are expressed in config rather
than Python, so they hold without an Ansible apply or a real BlueZ stack:

  - the input service no longer passes bluealsa-aplay the removed
    `--volume=software` flag (bluealsa 4.0 applies software volume by default);
  - the role stops, disables, and masks Debian's stock bluealsa-aplay.service,
    which would otherwise grab the incoming stream;
  - the role names the hub's receive adapter after the house speaker;
  - the standing lockdown and the verify check keep every adapter non-pairable
    and non-discoverable outside a pairing window.

The on-device behaviour (that BlueZ actually applies these) is confirmed on the
hub by the deploy; here we pin the intent the role encodes.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from jinja2 import Environment, FileSystemLoader

REPO = Path(__file__).resolve().parents[2]

BT_IN_SERVICE_TEMPLATE = "roles/hub_input/templates/snaphouse-bt-in.service.j2"
BT_INPUT_BUFFER_TIME_US = 1500000
BT_INPUT_PERIOD_TIME_US = 100000
BT_TASKS = REPO / "roles/bluetooth/tasks/main.yml"
BT_HANDLERS = REPO / "roles/bluetooth/handlers/main.yml"
LOCKDOWN = REPO / "roles/bluetooth/files/snaphouse-bt-lockdown.sh"
VERIFY = REPO / "roles/bluetooth/files/verify.d/bluetooth.sh"
UDEV_RULE = REPO / "roles/bluetooth/files/snaphouse-bt-lockdown.rules"


def _exec_start(service_text: str) -> str:
    for line in service_text.splitlines():
        if line.startswith("ExecStart="):
            return line
    raise AssertionError("no ExecStart line in the unit")


def render_bt_in_service() -> str:
    env = Environment(
        loader=FileSystemLoader(str(REPO)), keep_trailing_newline=True
    )
    return env.get_template(BT_IN_SERVICE_TEMPLATE).render(
        bt_input_buffer_time_us=BT_INPUT_BUFFER_TIME_US,
        bt_input_period_time_us=BT_INPUT_PERIOD_TIME_US,
    )


def test_bt_input_service_drops_the_software_volume_flag():
    text = render_bt_in_service()
    exec_start = _exec_start(text)
    # bluealsa 4.0 rejects --volume=software and applies software volume itself.
    assert "--volume=software" not in exec_start
    assert "--volume" not in exec_start
    # It still forwards any connected device's A2DP into the loopback PCM.
    assert "--pcm=snaphouse_bt_input" in exec_start
    assert "--profile-a2dp" in exec_start
    assert "00:00:00:00:00:00" in exec_start


def test_bt_input_service_sizes_the_receive_buffer_to_ride_phone_stalls():
    # A phone pauses its A2DP sender when it buffers ahead; the default 500 ms
    # loopback buffer ran dry and the house skipped (issue #92). A 1.5 s buffer
    # with a 100 ms period, placed right after the binary, matches the hand test
    # drop-in that logged zero underruns on the hub.
    text = render_bt_in_service()
    exec_start = _exec_start(text)
    assert f"--pcm-buffer-time={BT_INPUT_BUFFER_TIME_US}" in exec_start
    assert f"--pcm-period-time={BT_INPUT_PERIOD_TIME_US}" in exec_start
    # The buffer sizing flags come before the --pcm the device is played into.
    binary = "/usr/bin/bluealsa-aplay"
    assert exec_start.index(binary) < exec_start.index("--pcm-buffer-time")
    assert exec_start.index("--pcm-buffer-time") < exec_start.index("--pcm=")
    assert exec_start.index("--pcm-period-time") < exec_start.index("--pcm=")


def _tasks() -> list[dict]:
    return yaml.safe_load(BT_TASKS.read_text())


def _module(task: dict) -> tuple[str, dict]:
    for key, value in task.items():
        if key.startswith("ansible.builtin."):
            return key, value
    raise AssertionError(f"task has no module: {task.get('name')}")


def test_role_stops_disables_and_masks_the_stock_bluealsa_aplay_service():
    masking = []
    for task in _tasks():
        module, args = _module(task)
        if module.endswith("systemd_service") and args.get("name") == "bluealsa-aplay.service":
            masking.append((task, args))
    assert masking, "no task manages Debian's stock bluealsa-aplay.service"
    task, args = masking[0]
    assert args.get("masked") is True
    assert args.get("enabled") is False
    assert args.get("state") == "stopped"
    # Service actions only run where systemd is PID 1, never in the CI container.
    assert "common_systemd_init" in str(task.get("when"))


def test_role_names_the_receive_adapter_after_the_house_speaker():
    named = []
    for task in _tasks():
        module, args = _module(task)
        text = str(args)
        if "system-alias" in text:
            named.append(task)
    assert named, "no task sets the receive adapter's Bluetooth alias"
    task = named[0]
    text = str(task)
    # The alias is the shared house speaker name, not a hard-coded string.
    assert "house_stream_name" in text
    # Only on a host that receives, and only on real systemd; the address is
    # read from the var and kept out of Ansible's logs.
    assert "bt_receive_adapter" in str(task.get("when"))
    assert "common_systemd_init" in str(task.get("when"))
    assert task.get("no_log") is True


def test_lockdown_powers_each_controller_before_closing_it():
    text = LOCKDOWN.read_text()
    # A powered controller is what makes pairable/discoverable off stick; an
    # unpowered one can come up pairable later (the Pairable: yes seen on the hub).
    # Match the command lines (on their own line) so a comment cannot satisfy it.
    power = text.index("\npower on\n")
    assert power < text.index("\npairable off\n")
    assert power < text.index("\ndiscoverable off\n")


def test_verify_check_asserts_pairable_and_discoverable_off_on_every_adapter():
    text = VERIFY.read_text()
    # The check enumerates all controllers, not only the two named in local.yml.
    assert "bluetoothctl" in text and "list" in text
    assert "Discoverable: yes" in text
    assert "Pairable: yes" in text


def test_lockdown_strips_ansi_before_parsing_the_controller_list():
    text = LOCKDOWN.read_text()
    # verify.d strips ANSI from the same `bluetoothctl list`; the lockdown must
    # too, or a coloured address field is mangled into an unselectable token and
    # that controller is never closed (review Important #2).
    assert "bluetoothctl list" in text
    list_at = text.index("bluetoothctl list")
    awk_at = text.index("awk", list_at)
    # An ANSI-stripping step sits between the list and the field extraction.
    assert "sed" in text[list_at:awk_at]


def _lineinfile_main_conf() -> list[tuple[dict, dict]]:
    out = []
    for task in _tasks():
        module, args = _module(task)
        if module.endswith("lineinfile") and "main.conf" in str(args.get("path", "")):
            out.append((task, args))
    return out


def test_role_sets_daemon_level_pairable_defaults_in_main_conf():
    tasks = _lineinfile_main_conf()
    assert tasks, "no task edits /etc/bluetooth/main.conf"
    # The keys may be a loop, so scan the whole task, not just a static `line`.
    blob = " ".join(str(task) for task, _args in tasks)
    # Pairable is off by default at the daemon, and a stray pairable-on reverts.
    assert "AlwaysPairable" in blob and "false" in blob.lower()
    assert "PairableTimeout" in blob
    for task, args in tasks:
        # Each edit restarts bluetooth, and only when the file changes (a handler).
        assert "bluetooth" in str(task.get("notify", "")).lower()
        # The keys land under [General], not appended into some other section.
        assert "General" in str(args.get("insertafter", ""))


def _handlers() -> list[dict]:
    return yaml.safe_load(BT_HANDLERS.read_text())


def test_bluetooth_restart_handler_is_gated_on_real_systemd():
    restart = [
        handler
        for handler in _handlers()
        if "bluetooth" in str(handler).lower() and "restart" in str(handler).lower()
    ]
    assert restart, "no handler restarts bluetooth"
    # A notified handler still must not run in the CI container.
    assert any("common_systemd_init" in str(h.get("when")) for h in restart)


def test_role_installs_a_udev_rule_that_relocks_a_replugged_adapter():
    # A USB re-plug does not restart bluetooth.service, so PartOf does not cover
    # it; a udev add event on an hci device re-runs the lockdown oneshot.
    assert UDEV_RULE.exists(), "no udev rule shipped"
    rule = UDEV_RULE.read_text()
    assert 'ACTION=="add"' in rule
    assert 'SUBSYSTEM=="bluetooth"' in rule
    assert "hci" in rule
    assert "snaphouse-bt-lockdown.service" in rule
    installed = [
        task
        for task in _tasks()
        if _module(task)[0].endswith("copy")
        and "rules.d" in str(_module(task)[1].get("dest", ""))
    ]
    assert installed, "no task installs the udev rule into /etc/udev/rules.d"
