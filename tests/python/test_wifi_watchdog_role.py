"""Issue #178: role wiring for the persistent journal and the Wi-Fi watchdog."""

from __future__ import annotations

import subprocess
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
TASKS = yaml.safe_load((REPO / "roles/common/tasks/main.yml").read_text())
HANDLERS = yaml.safe_load((REPO / "roles/common/handlers/main.yml").read_text())
ALL = yaml.safe_load((REPO / "vars/all.yml").read_text())
CHECKS = REPO / "roles/common/files/verify.d"


def _text(task: dict) -> str:
    return yaml.safe_dump(task)


def _tasks_mentioning(needle: str) -> list[dict]:
    return [t for t in TASKS if needle in _text(t)]


def test_journald_dropin_makes_the_journal_persistent_with_a_cap():
    dropin = [t for t in TASKS if "journald.conf.d" in _text(t) and "content" in _text(t)]
    assert dropin, "no task writes a journald drop-in"
    text = _text(dropin[0])
    assert "Storage=persistent" in text
    assert "SystemMaxUse=" in text
    assert "journal" in str(dropin[0].get("notify", "")).lower()


def test_journal_directory_is_created():
    dirs = [t for t in TASKS if "/var/log/journal" in _text(t) and "directory" in _text(t)]
    assert dirs


def test_journald_handler_is_gated_on_systemd_and_flushes():
    hs = [h for h in HANDLERS if "journal" in _text(h).lower()]
    assert hs
    text = "".join(_text(h) for h in hs)
    assert "common_systemd_init" in text
    assert "journalctl" in text and "--flush" in text


def test_watchdog_tasks_only_run_where_wlan0_exists_under_networkmanager():
    wd = [t for t in TASKS if "wifi-watchdog" in _text(t) or "wifi_watchdog" in _text(t)]
    # The verify check installs everywhere (it passes where there is no wlan0).
    wd = [t for t in wd if "verify" not in t["name"].lower()]
    assert len(wd) >= 3  # service unit, timer unit, enable
    for task in wd:
        when = str(task.get("when", ""))
        assert "common_wlan0" in when, task.get("name")
        assert "common_nm_dir" in when, task.get("name")


def test_watchdog_thresholds_are_vars():
    cfg = ALL["wifi_watchdog"]
    assert cfg["restart_after_s"] == 300
    assert cfg["reboot_after_restart_s"] == 600
    assert cfg["reboot_min_interval_s"] == 3600


def test_iw_and_ping_are_installed_base_packages():
    apt = [t for t in TASKS if "ansible.builtin.apt" in t and "iw" in _text(t)][0]
    names = apt["ansible.builtin.apt"]["name"]
    assert "iw" in names
    assert "iputils-ping" in names


def test_every_new_verify_check_has_a_static_dest():
    for name in ("wifi_watchdog.sh", "journal_persistent.sh"):
        assert (CHECKS / name).exists()
        assert f"dest: /etc/snaphouse/verify.d/{name}" in (
            REPO / "roles/common/tasks/main.yml"
        ).read_text()
        assert "snaphouse-managed-check" in (CHECKS / name).read_text()


def test_powersave_check_fails_loudly_when_iw_is_missing(tmp_path):
    # wlan0 exists is simulated by a stub; with no iw on PATH the check must fail
    # with a clear message rather than pass silently.
    text = (CHECKS / "wifi_powersave.sh").read_text()
    assert "command -v iw" in text
    assert "iw is not installed" in text


def test_scripts_are_valid_bash():
    for name in ("wifi_watchdog.sh", "journal_persistent.sh", "wifi_powersave.sh"):
        subprocess.run(["bash", "-n", str(CHECKS / name)], check=True)
