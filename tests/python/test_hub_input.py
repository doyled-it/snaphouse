"""Tests for the snapserver configuration the hub renders (roles/hub_input).

snapserver assigns a client it has never seen to its default stream. snapcast's
default is the first valid stream in the config, which here is AirPlay (the
sources are listed AirPlay, Bluetooth, House so the meta stream can reference the
other two). That made every room default to the bare AirPlay input instead of
the House meta stream. snapserver exposes `[stream] default_source` to pin the
default, so the config sets it to the House stream name.

The template is rendered with Jinja2 (a dev-only dependency) so these hold
without an Ansible apply or any hardware.
"""

from __future__ import annotations

import re
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

REPO = Path(__file__).resolve().parents[2]

SNAPSERVER_CONF = "roles/hub_input/templates/snapserver.conf.j2"
HOUSE_STREAM_NAME = "House"
SAMPLE_FORMAT = "44100:16:2"

_DEFAULT_SOURCE = re.compile(r"^\s*default_source\s*=\s*(\S+)\s*$", re.MULTILINE)
_SOURCE_NAME = re.compile(r"^\s*source\s*=.*?[?&]name=([^&\s]+)", re.MULTILINE)


def render_snapserver_conf() -> str:
    env = Environment(
        loader=FileSystemLoader(str(REPO)), keep_trailing_newline=True
    )
    return env.get_template(SNAPSERVER_CONF).render(
        house_stream_name=HOUSE_STREAM_NAME,
        house_sample_format=SAMPLE_FORMAT,
        snaphouse_host="house",
    )


def test_default_source_is_the_house_meta_stream():
    # Without default_source, snapserver defaults a new client to the first
    # valid stream (AirPlay). The config must pin the default to the House meta
    # stream so rooms play the house, not the bare AirPlay input.
    conf = render_snapserver_conf()
    matches = _DEFAULT_SOURCE.findall(conf)
    assert matches, "snapserver.conf must set [stream] default_source"
    assert matches[-1] == HOUSE_STREAM_NAME, (
        f"default_source should be the House stream, got {matches[-1]!r}"
    )


def test_house_meta_stream_is_defined_and_lists_airplay_first():
    # The default_source target must actually exist as a stream, and the meta
    # stream must still prefer AirPlay over Bluetooth on a tie.
    conf = render_snapserver_conf()
    names = _SOURCE_NAME.findall(conf)
    assert HOUSE_STREAM_NAME in names, "the House stream must be defined"
    meta_line = next(
        line for line in conf.splitlines() if line.startswith("source = meta:")
    )
    assert meta_line.index("AirPlay") < meta_line.index("Bluetooth"), (
        "the meta stream must list AirPlay before Bluetooth"
    )


# --------------------------------------------------------------------------- #
# The standalone shairport-sync config (AirPlay input, roles/hub_input)
# --------------------------------------------------------------------------- #

SHAIRPORT_CONF = "roles/hub_input/templates/snaphouse-airplay.conf.j2"


def render_shairport_conf() -> str:
    env = Environment(
        loader=FileSystemLoader(str(REPO)), keep_trailing_newline=True
    )
    return env.get_template(SHAIRPORT_CONF).render(house_stream_name=HOUSE_STREAM_NAME)


def _setting(conf: str, key: str) -> str | None:
    match = re.search(rf"^\s*{re.escape(key)}\s*=\s*([^;]+);", conf, re.MULTILINE)
    return match.group(1).strip().strip('"') if match else None


def test_shairport_uses_soxr_into_the_loopback_plug():
    conf = render_shairport_conf()
    assert _setting(conf, "interpolation") == "soxr"
    assert _setting(conf, "output_backend") == "alsa"
    assert _setting(conf, "output_device") == "snaphouse_airplay_input"
    assert _setting(conf, "name") == HOUSE_STREAM_NAME


def test_shairport_volume_model_a_sender_controls_the_house():
    # Model A: the sender's volume drives the house (ignore_volume_control = no),
    # but over a 30 dB range at full-scale max, so a mid sender volume is usable
    # rather than the near-silent ~-43 dB the ~60 dB default gives.
    conf = render_shairport_conf()
    assert _setting(conf, "ignore_volume_control") == "no"
    assert _setting(conf, "volume_max_db") == "0.0"
    assert _setting(conf, "volume_range_db") == "30"


def test_shairport_publishes_metadata_with_cover_art():
    conf = render_shairport_conf()
    assert _setting(conf, "enabled") == "yes"
    assert _setting(conf, "include_cover_art") == "yes"
    assert _setting(conf, "pipe_name") == "/run/snaphouse/airplay-metadata"
