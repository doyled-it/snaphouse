"""Checks on the pinned Snapcast .deb checksums in vars/all.yml.

The snapcast_pkg role chooses the release .deb for a machine from that machine's
own Debian codename (ansible_distribution_release) and CPU architecture, then
verifies the download against a checksum pinned in the repo. The house is not on
one codename: the hub runs Debian bookworm (amd64) and the Raspberry Pis run
Debian trixie (arm64), and a bookworm .deb does not satisfy its shared-library
dependencies on trixie (libflac12 vs libflac14). So every (codename, arch) the
fleet actually uses must have a pinned checksum for both snapserver and
snapclient, or that machine's apply fails.

These checks guard that the checksum maps are keyed (codename -> arch -> sha256),
that every fleet target is present in both maps, and that each value is a real
sha256. They do not reach the network; they only read the repo.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
ALL_VARS_FILE = REPO_ROOT / "vars" / "all.yml"

# The (Debian codename, .deb arch) pairs that must have pinned checksums: the hub
# is bookworm/amd64 and the Raspberry Pis are trixie/arm64; trixie/amd64 covers
# the hub should it ever move to trixie; bookworm/arm64 keeps the container apply
# check green whether its runner is amd64 or arm64 (an arm64 host pulls the arm64
# base image). Add a pair here (and in vars/all.yml, both maps) before a machine
# on a new codename or arch can be bootstrapped.
SUPPORTED_TARGETS = {
    ("bookworm", "amd64"),
    ("bookworm", "arm64"),
    ("trixie", "amd64"),
    ("trixie", "arm64"),
}

# The two checksum maps, by the var name that holds each.
CHECKSUM_VARS = ("snapcast_server_sha256", "snapcast_client_sha256")

# A sha256 is 64 lowercase hex characters.
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def load_all_vars() -> dict:
    with ALL_VARS_FILE.open() as handle:
        data = yaml.safe_load(handle)
    assert isinstance(data, dict), "vars/all.yml must be a mapping"
    return data


def test_checksum_maps_are_keyed_by_codename_then_arch():
    data = load_all_vars()
    for var in CHECKSUM_VARS:
        assert var in data, f"vars/all.yml is missing {var}"
        by_codename = data[var]
        assert isinstance(by_codename, dict) and by_codename, (
            f"{var} must be a non-empty mapping of codename -> arch -> sha256"
        )
        for codename, by_arch in by_codename.items():
            assert isinstance(by_arch, dict) and by_arch, (
                f"{var}[{codename!r}] must be a mapping of arch -> sha256, "
                "so the .deb codename can be chosen per machine"
            )


def test_every_supported_target_has_a_pinned_checksum():
    data = load_all_vars()
    for var in CHECKSUM_VARS:
        by_codename = data[var]
        for codename, arch in sorted(SUPPORTED_TARGETS):
            assert codename in by_codename, (
                f"{var} has no entry for codename '{codename}'; "
                f"({codename}, {arch}) is a supported target"
            )
            assert arch in by_codename[codename], (
                f"{var}['{codename}'] has no entry for arch '{arch}'; "
                f"({codename}, {arch}) is a supported target"
            )


def test_every_pinned_checksum_is_a_real_sha256():
    data = load_all_vars()
    for var in CHECKSUM_VARS:
        for codename, by_arch in data[var].items():
            for arch, digest in by_arch.items():
                assert isinstance(digest, str) and SHA256_RE.match(digest), (
                    f"{var}['{codename}']['{arch}'] is not a 64-char lowercase "
                    f"hex sha256: {digest!r}"
                )


def test_no_single_house_wide_codename_var():
    # snapcast_deb_distro pinned one codename for the whole house, which broke the
    # trixie Pis. The codename is now read per machine from the Ansible facts, so
    # the static var must be gone.
    data = load_all_vars()
    assert "snapcast_deb_distro" not in data, (
        "snapcast_deb_distro must be removed: the .deb codename is chosen per "
        "machine from ansible_distribution_release, not pinned house-wide"
    )
