"""Checks on the host inventory and the repo's no-identifying-data rule.

These guard two invariants that the whole system leans on:

1. Every host applies the base roles (common, web, selfupdate) before anything
   else, names only roles that exist, and carries a unique, known host id.
2. Nothing in vars/ or local.example.yml looks like a real MAC address or a
   private IPv4 address. Identifying values live in the untracked local.yml,
   never in the repo.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
VARS_DIR = REPO_ROOT / "vars"
HOSTS_FILE = VARS_DIR / "hosts.yml"
ROLES_DIR = REPO_ROOT / "roles"

BASE_ROLES_IN_ORDER = ["common", "web", "selfupdate"]
VALID_HOST_IDS = {"living", "office", "bedroom"}

# A MAC is six colon-separated hex octets. The all-zero address is a wildcard
# used by tooling (for example bluealsa-aplay) and is not identifying.
MAC_RE = re.compile(r"(?:[0-9A-F]{2}:){5}[0-9A-F]{2}", re.IGNORECASE)
MAC_WILDCARD = "00:00:00:00:00:00"

# RFC 1918 private IPv4 ranges: 10/8, 172.16/12, 192.168/16.
PRIVATE_IPV4_RE = re.compile(
    r"\b(?:"
    r"10\.\d{1,3}\.\d{1,3}\.\d{1,3}"
    r"|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}"
    r"|192\.168\.\d{1,3}\.\d{1,3}"
    r")\b"
)


def load_hosts() -> dict:
    with HOSTS_FILE.open() as handle:
        data = yaml.safe_load(handle)
    assert isinstance(data, dict), "hosts.yml must be a mapping"
    assert "snaphouse_hosts" in data, (
        "hosts.yml must have a top-level 'snaphouse_hosts' key "
        "('hosts' is an Ansible reserved keyword)"
    )
    return data["snaphouse_hosts"]


def scanned_files() -> list[Path]:
    files = sorted(VARS_DIR.rglob("*.yml")) + sorted(VARS_DIR.rglob("*.yaml"))
    example = REPO_ROOT / "local.example.yml"
    if example.exists():
        files.append(example)
    return files


def macs_in(text: str) -> list[str]:
    return [m for m in MAC_RE.findall(text) if m.upper() != MAC_WILDCARD.upper()]


def test_every_host_starts_with_the_base_roles():
    hosts = load_hosts()
    assert hosts, "hosts.yml lists no hosts"
    for name, spec in hosts.items():
        roles = spec["roles"]
        assert roles[: len(BASE_ROLES_IN_ORDER)] == BASE_ROLES_IN_ORDER, (
            f"{name} must list {BASE_ROLES_IN_ORDER} first, got {roles}"
        )


def test_every_named_role_exists():
    hosts = load_hosts()
    for name, spec in hosts.items():
        for role in spec["roles"]:
            role_tasks = ROLES_DIR / role / "tasks" / "main.yml"
            assert role_tasks.is_file(), (
                f"{name} names role '{role}' but {role_tasks} is missing"
            )


def test_host_ids_are_unique_and_known():
    hosts = load_hosts()
    ids = [spec["host_id"] for spec in hosts.values()]
    assert len(ids) == len(set(ids)), f"host_id values are not unique: {ids}"
    assert set(ids) <= VALID_HOST_IDS, (
        f"host_id values must be within {VALID_HOST_IDS}, got {set(ids)}"
    )


def test_no_mac_addresses_in_repo_config():
    offenders = {}
    for path in scanned_files():
        found = macs_in(path.read_text())
        if found:
            offenders[str(path.relative_to(REPO_ROOT))] = found
    assert not offenders, f"MAC-like values found in tracked config: {offenders}"


def test_no_private_ipv4_addresses_in_repo_config():
    offenders = {}
    for path in scanned_files():
        found = PRIVATE_IPV4_RE.findall(path.read_text())
        if found:
            offenders[str(path.relative_to(REPO_ROOT))] = found
    assert not offenders, f"private IPv4 values found in tracked config: {offenders}"
