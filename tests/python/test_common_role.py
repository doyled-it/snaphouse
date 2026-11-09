"""Issue #84: avahi must advertise .local names over IPv4 only.

A phone with a VPN on (Proton, Allow LAN connections) exempts private IPv4 from
the tunnel but not global IPv6. When avahi advertised the hub's global IPv6
address alongside its private IPv4 one, a phone that tried IPv6 first sent the
request into the tunnel and house.local failed while the IP worked. The common
role now pins both avahi settings on every host. These tests pin that intent in
config, without an Ansible apply or a running avahi.
"""

from __future__ import annotations

from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]

COMMON_TASKS = REPO / "roles/common/tasks/main.yml"
COMMON_HANDLERS = REPO / "roles/common/handlers/main.yml"
VERIFY = REPO / "roles/common/files/verify.d/avahi_mdns.sh"


def _tasks() -> list[dict]:
    return yaml.safe_load(COMMON_TASKS.read_text())


def _handlers() -> list[dict]:
    return yaml.safe_load(COMMON_HANDLERS.read_text())


def _module(task: dict) -> tuple[str, dict] | None:
    """The task's ansible.builtin module and its args, or None (e.g. a block)."""
    for key, value in task.items():
        if key.startswith("ansible.builtin."):
            return key, value
    return None


def _modules() -> list[tuple[dict, str, dict]]:
    out = []
    for task in _tasks():
        found = _module(task)
        if found is not None:
            out.append((task, found[0], found[1]))
    return out


def _avahi_lineinfile() -> tuple[dict, dict]:
    for task, module, args in _modules():
        if module.endswith("lineinfile") and "avahi-daemon.conf" in str(args.get("path", "")):
            return task, args
    raise AssertionError("no task edits /etc/avahi/avahi-daemon.conf")


def test_role_pins_both_mdns_settings_in_their_own_sections():
    task, _args = _avahi_lineinfile()
    items = task.get("loop") or []
    by_line = {str(item.get("line", "")): item for item in items}
    # use-ipv6=no keeps avahi off IPv6 entirely, under [server].
    assert "use-ipv6=no" in by_line
    assert "server" in by_line["use-ipv6=no"]["insertafter"]
    # publish-aaaa-on-ipv4=no stops AAAA records over the IPv4 socket, under [publish].
    assert "publish-aaaa-on-ipv4=no" in by_line
    assert "publish" in by_line["publish-aaaa-on-ipv4=no"]["insertafter"]
    # Each key is matched whether commented or already set, so a re-apply is a
    # no-op and the rest of the stock file is left untouched.
    for item in items:
        assert item["regexp"].startswith("^#?")
    # A change restarts avahi (a handler, so only when the file changes).
    assert "avahi" in str(task.get("notify", "")).lower()


def test_role_flushes_handlers_right_after_the_mdns_edit():
    # The restart must be bound to the edit, not deferred to the end of the play:
    # a later task failing would otherwise drop the notify and leave avahi on the
    # old config while the file (and the file-only part of the check) looks fine.
    tasks = _tasks()
    edit_idx = next(
        i
        for i, task in enumerate(tasks)
        if (found := _module(task))
        and found[0].endswith("lineinfile")
        and "avahi-daemon.conf" in str(found[1].get("path", ""))
    )
    flush_idx = next(
        (
            i
            for i, task in enumerate(tasks)
            if (found := _module(task))
            and found[0].endswith("meta")
            and found[1] == "flush_handlers"
        ),
        None,
    )
    assert flush_idx is not None, "no flush_handlers after the mDNS edit"
    # It comes right after the edit, before the verify-check install.
    assert flush_idx == edit_idx + 1


def test_restart_handler_is_gated_on_real_systemd_and_tagged_service():
    restart = [
        handler
        for handler in _handlers()
        if "avahi" in str(handler).lower() and "restart" in str(handler).lower()
    ]
    assert restart, "no handler restarts avahi-daemon"
    handler = restart[0]
    # A notified handler still must not run in the CI container (no init).
    assert "common_systemd_init" in str(handler.get("when"))
    # The service tag marks it for a real-host --skip-tags service run.
    assert "service" in (handler.get("tags") or [])


def test_role_installs_the_mdns_verify_check_with_a_static_dest():
    installs = [
        args
        for _task, module, args in _modules()
        if module.endswith("copy")
        and str(args.get("dest", "")).endswith("verify.d/avahi_mdns.sh")
    ]
    assert installs, "no task installs the mDNS verify check"
    # A bare, static dest line so the self-update pruner can read it.
    assert installs[0]["dest"] == "/etc/snaphouse/verify.d/avahi_mdns.sh"


def test_verify_check_asserts_both_settings_and_carries_the_managed_marker():
    text = VERIFY.read_text()
    assert "snaphouse-managed-check" in text
    assert "use-ipv6" in text and "publish-aaaa-on-ipv4" in text
    assert "avahi-daemon.conf" in text


def test_verify_check_names_the_offending_setting_on_failure():
    # `snaphouse verify` surfaces only the last stderr line, so a silent `grep
    # -q` would print an empty detail. The check must say which setting is wrong.
    text = VERIFY.read_text()
    assert "is not set to no" in text
    # The value is anchored to the whole line, so a prefix like "notreally" cannot
    # satisfy the key.
    assert "[[:space:]]*no[[:space:]]*" in text
