"""Tests for the ALSA configuration the roles render on the hub.

The hub both captures Bluetooth input (roles/hub_input) and plays a Bluetooth
output (roles/snapclient), so two snaphouse ALSA PCMs are defined on the one
machine:

  - the output alias snapclient plays into (asound-bt.conf.j2 -> /etc/asound.conf)
  - the loopback feed bluealsa-aplay writes the phone's audio into
    (snaphouse-btloop.conf.j2 -> /etc/alsa/conf.d)

snapclient resolves its --soundcard by a name match, so if one snaphouse PCM
name were a prefix of another (the old snaphouse_bt vs snaphouse_btloop),
--soundcard snaphouse_bt could bind the loopback input instead of the speaker
output and feed the hub's output back into its own input. These tests pin that
no rendered snaphouse PCM name is a prefix of another, and that the --soundcard
the snapclient drop-in uses names exactly one of them.

They also pin that the output alias renders nothing when the room's speaker has
no address yet (the hub before the Vizio is paired): a bluealsa device with an
empty address must never be written. keepalive keeps snapclient stopped until an
address exists, and `snaphouse pair speaker` prompts for a re-apply once it does.

The templates are rendered with Jinja2 (a dev-only dependency) so these hold
without an Ansible apply or any hardware.
"""

from __future__ import annotations

import re
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

REPO = Path(__file__).resolve().parents[2]

ASOUND_BT = "roles/snapclient/templates/asound-bt.conf.j2"
BTLOOP = "roles/hub_input/templates/snaphouse-btloop.conf.j2"
AIRPLAY_LOOP = "roles/hub_input/templates/snaphouse-airplay-loop.conf.j2"
SNAPCLIENT_DROPIN = "roles/snapclient/templates/snapclient-snaphouse.conf.j2"

# A synthetic hub matching vars/hosts.yml's `house`: both a hub_input host and a
# Bluetooth output, so both snaphouse PCMs are rendered on the one machine.
HUB_HOSTS = {
    "house": {
        "host_id": "living",
        "output": {"kind": "bluetooth", "device": None, "speaker": "vizio"},
    },
}
SAMPLE_FORMAT = "44100:16:2"
# A placeholder address, never a real household one.
VIZIO_MAC = "AA:BB:CC:DD:EE:01"

_PCM = re.compile(r"pcm\.(snaphouse_\w+)")
_SOUNDCARD = re.compile(r"--soundcard\s+(\S+)")


def render(template: str, context: dict) -> str:
    env = Environment(
        loader=FileSystemLoader(str(REPO)), keep_trailing_newline=True
    )
    return env.get_template(template).render(**context)


def pcm_names(text: str) -> list[str]:
    return _PCM.findall(text)


def test_no_rendered_snaphouse_pcm_name_is_a_prefix_of_another():
    output = render(
        ASOUND_BT,
        {
            "snaphouse_host": "house",
            "snaphouse_hosts": HUB_HOSTS,
            "speakers": {"vizio": VIZIO_MAC},
        },
    )
    loopback = render(
        BTLOOP,
        {
            "snaphouse_host": "house",
            "snaphouse_hosts": HUB_HOSTS,
            "house_sample_format": SAMPLE_FORMAT,
        },
    )
    airplay = render(
        AIRPLAY_LOOP,
        {
            "snaphouse_host": "house",
            "snaphouse_hosts": HUB_HOSTS,
            "house_sample_format": SAMPLE_FORMAT,
        },
    )

    names = pcm_names(output) + pcm_names(loopback) + pcm_names(airplay)
    # Three PCMs are present on the hub (speaker output, Bluetooth loopback,
    # AirPlay loopback), so there is something to collide.
    assert len(names) >= 3

    for a in names:
        for b in names:
            if a is b:
                continue
            assert not b.startswith(a), (
                f"snaphouse PCM {a!r} is a prefix of {b!r}; snapclient's "
                "--soundcard name match could bind the wrong PCM"
            )


def test_snapclient_soundcard_names_exactly_one_rendered_pcm():
    dropin = render(
        SNAPCLIENT_DROPIN,
        {
            "snaphouse_host": "house",
            "snaphouse_hosts": HUB_HOSTS,
        },
    )
    soundcards = _SOUNDCARD.findall(dropin)
    assert soundcards, "the drop-in must set --soundcard"
    soundcard = soundcards[0]
    assert soundcard.startswith("snaphouse_")

    output = render(
        ASOUND_BT,
        {
            "snaphouse_host": "house",
            "snaphouse_hosts": HUB_HOSTS,
            "speakers": {"vizio": VIZIO_MAC},
        },
    )
    loopback = render(
        BTLOOP,
        {
            "snaphouse_host": "house",
            "snaphouse_hosts": HUB_HOSTS,
            "house_sample_format": SAMPLE_FORMAT,
        },
    )
    names = pcm_names(output) + pcm_names(loopback)

    # The soundcard must name exactly one PCM, and must not be a prefix of any
    # other, so the match is unambiguous.
    assert soundcard in names
    others = [n for n in names if n != soundcard]
    for other in others:
        assert not other.startswith(soundcard)


def test_output_alias_renders_a_device_when_the_speaker_has_an_address():
    output = render(
        ASOUND_BT,
        {
            "snaphouse_host": "house",
            "snaphouse_hosts": HUB_HOSTS,
            "speakers": {"vizio": VIZIO_MAC},
        },
    )
    assert "pcm." in output
    assert f'device "{VIZIO_MAC}"' in output


def test_output_alias_renders_no_device_when_the_speaker_has_no_address():
    # The hub before the Vizio is paired: speakers.vizio is empty. A bluealsa
    # device with an empty address must never be written; the template renders no
    # PCM at all until an address exists.
    output = render(
        ASOUND_BT,
        {
            "snaphouse_host": "house",
            "snaphouse_hosts": HUB_HOSTS,
            "speakers": {"vizio": ""},
        },
    )
    assert "pcm." not in output
    assert 'device ""' not in output
    assert "bluealsa" not in output
