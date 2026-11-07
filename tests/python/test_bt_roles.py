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

REPO = Path(__file__).resolve().parents[2]

BT_IN_SERVICE = REPO / "roles/hub_input/files/snaphouse-bt-in.service"
BT_TASKS = REPO / "roles/bluetooth/tasks/main.yml"
LOCKDOWN = REPO / "roles/bluetooth/files/snaphouse-bt-lockdown.sh"
VERIFY = REPO / "roles/bluetooth/files/verify.d/bluetooth.sh"


def _exec_start(service_text: str) -> str:
    for line in service_text.splitlines():
        if line.startswith("ExecStart="):
            return line
    raise AssertionError("no ExecStart line in the unit")


def test_bt_input_service_drops_the_software_volume_flag():
    text = BT_IN_SERVICE.read_text()
    exec_start = _exec_start(text)
    # bluealsa 4.0 rejects --volume=software and applies software volume itself.
    assert "--volume=software" not in exec_start
    assert "--volume" not in exec_start
    # It still forwards any connected device's A2DP into the loopback PCM.
    assert "--pcm=snaphouse_bt_input" in exec_start
    assert "--profile-a2dp" in exec_start
    assert "00:00:00:00:00:00" in exec_start


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
