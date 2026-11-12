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
IDLE_THRESHOLD_MS = 10000

_DEFAULT_SOURCE = re.compile(r"^\s*default_source\s*=\s*(\S+)\s*$", re.MULTILINE)
_SOURCE_NAME = re.compile(r"^\s*source\s*=.*?[?&]name=([^&\s]+)", re.MULTILINE)


def render_snapserver_conf() -> str:
    env = Environment(
        loader=FileSystemLoader(str(REPO)), keep_trailing_newline=True
    )
    return env.get_template(SNAPSERVER_CONF).render(
        house_stream_name=HOUSE_STREAM_NAME,
        house_sample_format=SAMPLE_FORMAT,
        snapserver_idle_threshold_ms=IDLE_THRESHOLD_MS,
        snaphouse_host="house",
    )


def _alsa_source_line(conf: str, name: str) -> str:
    for line in conf.splitlines():
        if line.startswith("source = alsa:") and f"name={name}" in line:
            return line
    raise AssertionError(f"no alsa source named {name!r} in snapserver.conf")


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


def test_both_alsa_sources_pin_idle_threshold():
    # snapserver's default idle_threshold (100 ms) treats the digital silence
    # between spoken sentences as the stream stopping, which flaps the House meta
    # stream and chops every room during audiobooks and podcasts (issue #90).
    # Both alsa sources must carry a 10 s threshold so speech pauses stay
    # playing while a real stop still goes idle.
    conf = render_snapserver_conf()
    for name in ("AirPlay", "Bluetooth"):
        line = _alsa_source_line(conf, name)
        assert f"idle_threshold={IDLE_THRESHOLD_MS}" in line, (
            f"the {name} source must set idle_threshold={IDLE_THRESHOLD_MS}, "
            f"got: {line}"
        )
        # It must sit between the sampleformat and the controlscript, matching
        # the live hand fix on the hub.
        assert re.search(
            rf"sampleformat=[^&]+&idle_threshold={IDLE_THRESHOLD_MS}&controlscript=",
            line,
        ), f"the {name} source has idle_threshold out of place: {line}"


# --------------------------------------------------------------------------- #
# The standalone shairport-sync config (AirPlay input, roles/hub_input)
# --------------------------------------------------------------------------- #

SHAIRPORT_CONF = "roles/hub_input/templates/snaphouse-airplay.conf.j2"


def render_shairport_conf() -> str:
    env = Environment(
        loader=FileSystemLoader(str(REPO)), keep_trailing_newline=True
    )
    return env.get_template(SHAIRPORT_CONF).render(
        house_stream_name=HOUSE_STREAM_NAME, airplay_rtsp_port=7000
    )


def _setting(conf: str, key: str) -> str | None:
    match = re.search(rf"^\s*{re.escape(key)}\s*=\s*([^;]+);", conf, re.MULTILINE)
    return match.group(1).strip().strip('"') if match else None


def test_shairport_uses_soxr_into_the_loopback_directly():
    # The AirPlay 2 receiver runs in a container with no host ALSA config, so
    # the snaphouse plug device does not exist there: it opens the loopback
    # subdevice itself and pins the shared 44100 S16_LE stereo format.
    conf = render_shairport_conf()
    assert _setting(conf, "interpolation") == "soxr"
    assert _setting(conf, "output_backend") == "alsa"
    assert _setting(conf, "output_device") == "hw:Loopback,0,1"
    assert _setting(conf, "output_rate") == "44100"
    assert _setting(conf, "output_format") == "S16_LE"
    assert _setting(conf, "output_channels") == "2"
    assert _setting(conf, "name") == HOUSE_STREAM_NAME


def test_shairport_listens_on_the_airplay_2_port():
    # AirPlay 2 serves RTSP on 7000 (AirPlay 1 used 5000); the verify check
    # probes the same port.
    assert _setting(render_shairport_conf(), "port") == "7000"
    assert "7000" in (REPO / VERIFY_CHECK).read_text()


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


# --------------------------------------------------------------------------- #
# The AirPlay 2 receiver container (snaphouse-airplay-in.service)
# --------------------------------------------------------------------------- #

import yaml  # noqa: E402

AIRPLAY_UNIT = "roles/hub_input/templates/snaphouse-airplay-in.service.j2"
VERIFY_CHECK = "roles/hub_input/files/verify.d/hub_input.sh"
TASKS = "roles/hub_input/tasks/main.yml"
IMAGE = "mikebrady/shairport-sync:5.6.3@sha256:" + "a" * 64


def render_unit() -> str:
    env = Environment(
        loader=FileSystemLoader(str(REPO)), keep_trailing_newline=True
    )
    return env.get_template(AIRPLAY_UNIT).render(shairport_image=IMAGE)


def _exec_start(unit: str) -> str:
    match = re.search(r"^ExecStart=((?:[^\n]*\\\n)*[^\n]*)", unit, re.MULTILINE)
    assert match, "no ExecStart"
    return match.group(1).replace("\\\n", " ")


def test_vars_pin_the_image_by_tag_and_digest():
    allvars = yaml.safe_load((REPO / "vars/all.yml").read_text())
    assert re.fullmatch(r"5\.\d+(\.\d+)*", str(allvars["shairport_image_tag"]))
    assert re.fullmatch(r"sha256:[0-9a-f]{64}", allvars["shairport_image_digest"])
    assert allvars["shairport_image_repo"] == "mikebrady/shairport-sync"
    assert allvars["airplay_rtsp_port"] == 7000


def test_unit_runs_the_pinned_image_with_host_networking_and_sound():
    unit = render_unit()
    cmd = _exec_start(unit)
    assert "docker run" in cmd and "--rm" in cmd
    assert IMAGE in cmd
    assert "--network host" in cmd or "--net=host" in cmd or "--network=host" in cmd
    assert "--device /dev/snd" in cmd
    assert "Restart=always" in unit
    assert "docker.service" in unit


def test_unit_never_mounts_host_dbus_without_disabling_the_image_avahi():
    # The image's startup launches its own dbus-daemon unless ENABLE_AVAHI=0, and
    # that destroyed the host's system bus socket when /run/dbus was mounted
    # (issue #138). Mounting the host bus is only safe with ENABLE_AVAHI=0.
    cmd = _exec_start(render_unit())
    assert "-e ENABLE_AVAHI=0" in cmd
    for name in ("dbus", "avahi-daemon"):
        assert f"-v /run/{name}:/run/{name}" in cmd, name
        assert f"-v /run/{name}:/var/run/{name}" in cmd, name


def test_unit_mounts_metadata_dir_and_config_read_only():
    cmd = _exec_start(render_unit())
    assert "-v /run/snaphouse:/run/snaphouse" in cmd
    assert "-v /etc/snaphouse/shairport-airplay.conf:/etc/shairport-sync.conf:ro" in cmd


def test_dbus_mount_implies_avahi_disabled_in_any_unit_text():
    # Guard the invariant on the rendered text itself, whatever the layout.
    unit = render_unit()
    if "/run/dbus" in _exec_start(unit):
        assert "ENABLE_AVAHI=0" in unit


def _tasks() -> list[dict]:
    return yaml.safe_load((REPO / TASKS).read_text())


def test_docker_and_image_tasks_are_skipped_by_the_container_apply():
    # The CI container apply skips the hardware and service tags, so anything
    # that needs a docker daemon or the network must carry one of them.
    by_name = {t["name"]: t for t in _tasks()}
    for name, task in by_name.items():
        text = yaml.safe_dump(task)
        if "docker" in text and "snaphouse-airplay-in" not in name.lower():
            tags = task.get("tags") or []
            assert {"hardware", "service"} & set(tags), name
    pull = next(t for n, t in by_name.items() if "pull" in n.lower())
    assert "service" in pull["tags"] or "hardware" in pull["tags"]


def test_debian_shairport_package_is_removed_not_installed():
    tasks = _tasks()
    pkgs = [t for t in tasks if "ansible.builtin.apt" in t]
    for t in pkgs:
        apt = t["ansible.builtin.apt"]
        if apt.get("name") == "shairport-sync":
            assert apt["state"] == "absent"


def test_verify_check_probes_the_unit_and_the_rtsp_port():
    text = (REPO / VERIFY_CHECK).read_text()
    assert "snaphouse-airplay-in.service" in text
    assert "/dev/tcp/127.0.0.1/7000" in text
    assert re.search(r"^\s*dest:\s*/etc/snaphouse/verify.d/hub_input.sh\s*$", (REPO / TASKS).read_text(), re.M)
