"""Tests for airplay_meta.py, the AirPlay metadata bridge for snapserver.

The bridge reads shairport-sync's metadata pipe and republishes the now-playing
track, playback status, and a cover *URL* as Snapcast stream properties. Pinned
here (all pure, no snapserver and no pipe):

1. The pipe framing: type/code decode, one item parsed, items split across reads.
2. The item-to-property mapping: text accumulates and commits on the bundle end,
   play-state codes map to a playback status, and the cover becomes a URL.
3. The cover is downscaled to a small JPEG and never ships as inline artData.
4. The protocol shape matches avrcp_meta: ready first, GetProperties answered,
   control declined.

The cover tests use Pillow, which is a dev dependency (and the hub `hub` extra).
"""

from __future__ import annotations

import io
import json
from pathlib import Path

from PIL import Image

from snaphouse import airplay_meta


# --------------------------------------------------------------------------- #
# Pipe parsing
# --------------------------------------------------------------------------- #


def _b64(text: bytes) -> str:
    import base64

    return base64.b64encode(text).decode("ascii")


def _text_item(code_hex: str, value: bytes) -> str:
    return (
        f"<item><type>636f7265</type><code>{code_hex}</code>"
        f"<length>{len(value)}</length>\n"
        f'<data encoding="base64">\n{_b64(value)}</data></item>'
    )


MINM = "6d696e6d"  # title
ASAR = "61736172"  # artist
ASAL = "6173616c"  # album


def test_decode_fourcc_turns_hex_into_ascii_tags():
    assert airplay_meta.decode_fourcc("636f7265") == "core"
    assert airplay_meta.decode_fourcc("73736e63") == "ssnc"
    assert airplay_meta.decode_fourcc("nothex") == ""


def test_parse_item_reads_type_code_and_base64_data():
    block = _text_item(MINM, "Hello".encode("utf-8"))[len("<item>"):-len("</item>")]
    type_str, code_str, data = airplay_meta.parse_item(block)
    assert type_str == "core"
    assert code_str == "minm"
    assert data == b"Hello"


def test_parse_item_handles_an_item_with_no_data():
    block = "<type>73736e63</type><code>6d64656e</code><length>0</length>"
    type_str, code_str, data = airplay_meta.parse_item(block)
    assert (type_str, code_str, data) == ("ssnc", "mden", b"")


def test_pipe_parser_extracts_complete_items_across_feeds():
    parser = airplay_meta.PipeParser()
    full = _text_item(MINM, "NUEVAYoL".encode("utf-8"))
    # Split mid-item: the first feed yields nothing, the second completes it.
    first = parser.feed(full[: len(full) // 2])
    assert first == []
    second = parser.feed(full[len(full) // 2:])
    assert second == [("core", "minm", b"NUEVAYoL")]


# --------------------------------------------------------------------------- #
# Item-to-property mapping
# --------------------------------------------------------------------------- #


def _drive(events, *, cover_dir, host="house.local"):
    out = io.StringIO()
    airplay_meta.serve(airplay_meta.Player(), events, out, cover_dir=Path(cover_dir), host=host)
    lines = [json.loads(line) for line in out.getvalue().splitlines() if line]
    return lines


def test_serve_announces_ready_first(tmp_path):
    lines = _drive([], cover_dir=tmp_path)
    assert lines == [{"jsonrpc": "2.0", "method": "Plugin.Stream.Ready"}]


def test_text_metadata_commits_on_the_bundle_end(tmp_path):
    events = [
        ("item", "core", "minm", b"NUEVAYoL"),
        ("item", "core", "asar", b"Bad Bunny"),
        ("item", "core", "asal", b"DeBI TiRAR MAS FOToS"),
        ("item", "ssnc", "mden", b""),
    ]
    lines = _drive(events, cover_dir=tmp_path)
    # Ready, then exactly one properties notification once the bundle ends.
    assert lines[0]["method"] == "Plugin.Stream.Ready"
    assert len(lines) == 2
    props = lines[1]["params"]
    assert lines[1]["method"] == "Plugin.Stream.Player.Properties"
    assert props["metadata"]["title"] == "NUEVAYoL"
    assert props["metadata"]["artist"] == ["Bad Bunny"]
    assert props["metadata"]["album"] == "DeBI TiRAR MAS FOToS"
    assert props["canControl"] is False


def test_play_states_map_to_playback_status(tmp_path):
    for code, expected in [("pbeg", "playing"), ("prsm", "playing"),
                           ("pfls", "paused"), ("pend", "stopped")]:
        lines = _drive([("item", "ssnc", code, b"")], cover_dir=tmp_path)
        assert lines[-1]["params"]["playbackStatus"] == expected, code


def test_a_cover_is_published_as_a_url_never_as_artdata(tmp_path):
    png = _png_bytes((800, 600))
    lines = _drive([("item", "ssnc", "PICT", png)], cover_dir=tmp_path)
    props = lines[-1]["params"]
    art_url = props["metadata"]["artUrl"]
    assert art_url.startswith("http://house.local/cover/")
    assert art_url.endswith(".jpg")
    # The image bytes are never embedded.
    assert "artData" not in props["metadata"]
    assert "artData" not in json.dumps(props)
    # The file was written where nginx serves /cover/ from.
    name = art_url.rsplit("/", 1)[-1]
    assert (tmp_path / name).is_file()


def test_build_properties_omits_metadata_when_there_is_nothing_to_show():
    props = airplay_meta.build_properties(airplay_meta.Player())
    assert "metadata" not in props
    assert props["playbackStatus"] == "stopped"


# --------------------------------------------------------------------------- #
# Cover downscaling
# --------------------------------------------------------------------------- #


def _png_bytes(size) -> bytes:
    gradient = Image.linear_gradient("L").resize(size).convert("RGB")
    buffer = io.BytesIO()
    gradient.save(buffer, format="PNG")
    return buffer.getvalue()


def test_downscale_cover_caps_dimensions_and_size_and_is_jpeg():
    jpeg = airplay_meta.downscale_cover(_png_bytes((1400, 1000)))
    image = Image.open(io.BytesIO(jpeg))
    assert image.format == "JPEG"
    assert max(image.size) <= airplay_meta.COVER_MAX_PX
    assert len(jpeg) <= airplay_meta.COVER_MAX_BYTES


def test_store_cover_is_content_addressed_and_idempotent(tmp_path):
    png = _png_bytes((900, 900))
    first = airplay_meta.store_cover(png, tmp_path, "house.local")
    second = airplay_meta.store_cover(png, tmp_path, "house.local")
    assert first == second
    assert first.startswith("http://house.local/cover/")
    # One file for identical art.
    assert len(list(tmp_path.glob("*.jpg"))) == 1


def test_store_cover_returns_none_for_unusable_bytes(tmp_path):
    assert airplay_meta.store_cover(b"not an image", tmp_path, "house.local") is None
    assert airplay_meta.store_cover(b"", tmp_path, "house.local") is None


# --------------------------------------------------------------------------- #
# Protocol shape (shared with avrcp_meta)
# --------------------------------------------------------------------------- #


def test_get_properties_is_answered_with_the_current_properties():
    player = airplay_meta.Player()
    player.title = "NUEVAYoL"
    response = airplay_meta.handle_request(
        {"jsonrpc": "2.0", "id": 7, "method": "Plugin.Stream.Player.GetProperties"}, player
    )
    assert response["id"] == 7
    assert response["result"]["metadata"]["title"] == "NUEVAYoL"


def test_a_control_request_is_declined():
    response = airplay_meta.handle_request(
        {"jsonrpc": "2.0", "id": 1, "method": "Plugin.Stream.Player.SetProperty"},
        airplay_meta.Player(),
    )
    assert response["error"]["code"] == -32601


def test_a_notification_gets_no_reply():
    assert (
        airplay_meta.handle_request(
            {"jsonrpc": "2.0", "method": "Plugin.Stream.Player.GetProperties"},
            airplay_meta.Player(),
        )
        is None
    )
