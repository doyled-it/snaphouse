"""Tests for the ente_sync role: the Ente CLI install, the vault key, the sync
unit and timer, and the hub wiring. They pin the intent the role encodes without
an Ansible apply, the way test_artadmin_role.py does."""

from __future__ import annotations

import re
from pathlib import Path

import yaml
from jinja2 import Environment, FileSystemLoader

REPO = Path(__file__).resolve().parents[2]

ROLE_DIR = REPO / "roles/ente_sync"
ROLE_TASKS = ROLE_DIR / "tasks/main.yml"
SERVICE_TEMPLATE = "roles/ente_sync/templates/snaphouse-ente-sync.service.j2"
TIMER_TEMPLATE = "roles/ente_sync/templates/snaphouse-ente-sync.timer.j2"
VERIFY_CHECK = ROLE_DIR / "files/verify.d/ente_sync.sh"
ART_CONF = REPO / "roles/art_share/files/art.conf"


def _vars() -> dict:
    return yaml.safe_load((REPO / "vars/all.yml").read_text())


def _tasks() -> list[dict]:
    return yaml.safe_load(ROLE_TASKS.read_text())


def _find(module: str) -> list[dict]:
    key = f"ansible.builtin.{module}"
    return [t for t in _tasks() if key in t]


def _render(template: str) -> str:
    env = Environment(loader=FileSystemLoader(str(REPO)), keep_trailing_newline=True)
    return env.get_template(template).render(**_vars())


def test_role_pins_cli_0_4_4_with_sha256_and_downloads_the_linux_amd64_asset():
    v = _vars()
    assert v["ente_cli_version"] == "0.4.4"
    assert re.fullmatch(r"[0-9a-f]{64}", v["ente_cli_sha256"])
    downloads = _find("get_url")
    assert len(downloads) == 1
    args = downloads[0]["ansible.builtin.get_url"]
    assert args["url"] == (
        "https://github.com/ente-io/ente/releases/download/"
        "ente-cli-v{{ ente_cli_version }}/ente-cli-v{{ ente_cli_version }}-linux-amd64.tar.gz"
    )
    assert args["checksum"] == "sha256:{{ ente_cli_sha256 }}"


def test_role_installs_the_ente_binary_and_checks_version_output():
    tasks = _tasks()
    links = [t["ansible.builtin.file"] for t in tasks if "ansible.builtin.file" in t]
    assert any(
        f.get("dest") == "/usr/local/bin/ente" and f.get("state") == "link" for f in links
    )
    assert _find("unarchive")
    checks = [
        t for t in tasks if "ente --version" in str(t.get("ansible.builtin.command", ""))
    ]
    assert checks
    assert "ente_cli_version" in str(checks[0].get("failed_when"))


def test_vault_key_generated_once_with_ente_vault_key_generate_mode_0400_owner_ente():
    tasks = _tasks()
    gen = [t for t in tasks if "vault key generate" in str(t.get("ansible.builtin.shell", ""))]
    assert len(gen) == 1
    task = gen[0]
    shell = task["ansible.builtin.shell"]
    assert "ente vault key generate > /var/lib/snaphouse/ente/vault.key" in shell["cmd"]
    # A failed generate must not leave an empty file that `creates` then trusts.
    assert "rm -f /var/lib/snaphouse/ente/vault.key" in shell["cmd"]
    assert shell["creates"] == "/var/lib/snaphouse/ente/vault.key"
    assert task["become_user"] == "ente"
    assert task["no_log"] is True
    assert "register" not in task
    perms = [
        t["ansible.builtin.file"]
        for t in tasks
        if t.get("ansible.builtin.file", {}).get("path") == "/var/lib/snaphouse/ente/vault.key"
    ]
    assert perms and perms[0]["mode"] == "0400" and perms[0]["owner"] == "ente"
    assert "vault_key" not in str(_vars()).lower()


def test_user_ente_system_user_no_login_and_art_in_group():
    tasks = _tasks()
    user = [t["ansible.builtin.user"] for t in tasks if "ansible.builtin.user" in t]
    ente = [u for u in user if u["name"] == "ente"][0]
    assert ente["system"] is True and ente["shell"] == "/usr/sbin/nologin"
    art = [u for u in user if u["name"] == "art"][0]
    assert art["groups"] == "ente" and art["append"] is True
    looped = [t for t in tasks if "/var/lib/snaphouse/ente/mirror" in t.get("loop", [])]
    assert looped
    d = looped[0]["ansible.builtin.file"]
    assert d["mode"] == "0750" and d["owner"] == "ente" and d["group"] == "ente"
    assert {"/var/lib/snaphouse/ente", "/var/lib/snaphouse/ente/home"} <= set(looped[0]["loop"])


def test_timer_every_15_minutes_and_service_runs_as_ente_hardened():
    timer = _render(TIMER_TEMPLATE)
    assert "OnBootSec=2min" in timer
    assert "OnUnitActiveSec=15min" in timer
    assert "RandomizedDelaySec=60" in timer
    svc = _render(SERVICE_TEMPLATE)
    assert "Type=oneshot" in svc and "User=ente" in svc
    assert (
        "ExecStart=/opt/snaphouse/.venv/bin/python -m snaphouse.enterun "
        "--cli /usr/local/bin/ente --mirror-root /var/lib/snaphouse/ente/mirror "
        "--home /var/lib/snaphouse/ente/home --vault-key /var/lib/snaphouse/ente/vault.key "
        "--status /var/lib/snaphouse/ente/status.json"
    ) in svc
    for line in (
        "Environment=PYTHONPATH=/opt/snaphouse/lib",
        "ProtectSystem=strict",
        "NoNewPrivileges=yes",
        "PrivateTmp=yes",
        "ReadWritePaths=/var/lib/snaphouse/ente",
    ):
        assert line in svc


def test_no_password_or_account_name_in_unit_or_vars():
    blob = (
        _render(SERVICE_TEMPLATE)
        + _render(TIMER_TEMPLATE)
        + (REPO / "vars/all.yml").read_text()
        + ROLE_TASKS.read_text()
    ).lower()
    assert "password" not in blob
    assert "--account" not in blob
    assert "ente_cli_vault_key=" not in blob


def test_artadmin_unit_passes_ente_paths_and_writes_art_ente():
    env = Environment(loader=FileSystemLoader(str(REPO)), keep_trailing_newline=True)
    hosts = yaml.safe_load((REPO / "vars/hosts.yml").read_text())["snaphouse_hosts"]
    unit = env.get_template("roles/art_share/templates/snaphouse-artadmin.service.j2").render(
        snaphouse_hosts=hosts
    )
    assert "--ente-root /var/lib/snaphouse/ente/mirror" in unit
    assert "--ente-cache /var/lib/snaphouse/public/art-ente" in unit
    assert "--ente-status /var/lib/snaphouse/ente/status.json" in unit
    rw = [l for l in unit.splitlines() if l.startswith("ReadWritePaths=")][0]
    assert "/var/lib/snaphouse/public/art-ente" in rw.split()
    tasks = yaml.safe_load((REPO / "roles/art_share/tasks/main.yml").read_text())
    dirs = [t for t in tasks if "/var/lib/snaphouse/public/art-ente" in str(t)]
    assert dirs and "owner: art" in yaml.safe_dump(dirs[0])


def test_nginx_serves_ente_renditions_by_regex_alias_only():
    conf = ART_CONF.read_text()
    pattern = (
        r'location ~ "^/art/(ente-[0-9A-Za-z]{1,24}/[0-9A-Za-z]{1,24}\.jpg)$" {'
    )
    assert pattern in conf
    block = conf[conf.index(pattern) :]
    block = block[: block.index("\n}") + 2]
    assert "alias /var/lib/snaphouse/public/art-ente/$1;" in block
    assert "autoindex" not in block
    assert conf.index(pattern) < conf.index("location /art/ {")
    assert "/var/lib/snaphouse/ente/mirror" not in conf


def test_hub_roles_include_ente_sync():
    hosts = yaml.safe_load((REPO / "vars/hosts.yml").read_text())["snaphouse_hosts"]
    roles = hosts["house"]["roles"]
    assert "ente_sync" in roles
    assert roles.index("ente_sync") > roles.index("art_share")
    for name, host in hosts.items():
        if name != "house":
            assert "ente_sync" not in host["roles"]


def test_verify_check_passes_when_not_set_up():
    text = VERIFY_CHECK.read_text()
    assert "snaphouse-managed-check" in text
    assert "snaphouse-ente-sync.timer" in text
    assert "jq -e '.state'" in text


def test_group_add_restarts_artadmin_so_it_picks_up_the_ente_group():
    tasks = _tasks()
    adds = [
        t for t in tasks
        if t.get("ansible.builtin.user", {}).get("name") == "art"
    ]
    assert len(adds) == 1
    notify = adds[0]["notify"]
    assert "Restart the art admin service" in ([notify] if isinstance(notify, str) else notify)
