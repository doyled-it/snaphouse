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
import tempfile
from pathlib import Path

import pytest
from PIL import Image

from snaphouse import airplay_meta


# A metadata/cover-free scratch dir for the position/duration/genre tests, none
# of which write a cover (the only item here that would, PICT, is fed empty).
TMP = Path(tempfile.mkdtemp())


class FakeClock:
    """A monotonic clock the tests advance by hand, to pin position at emit time."""

    def __init__(self, now: float = 0.0) -> None:
        self.t = float(now)

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


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


# --------------------------------------------------------------------------- #
# Cover directory pruning (bounded growth)
# --------------------------------------------------------------------------- #


def _solid_png(color) -> bytes:
    image = Image.new("RGB", (700, 700), color)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def test_prune_covers_keeps_only_the_most_recent(tmp_path):
    import os as _os

    # Ten covers with increasing mtimes; keep the 3 newest.
    for i in range(10):
        path = tmp_path / f"cover{i:02d}.jpg"
        path.write_bytes(b"x")
        _os.utime(path, (1000 + i, 1000 + i))
    airplay_meta.prune_covers(tmp_path, keep=3)
    remaining = sorted(p.name for p in tmp_path.glob("*.jpg"))
    assert remaining == ["cover07.jpg", "cover08.jpg", "cover09.jpg"]


def test_prune_covers_sweeps_leftover_temp_files(tmp_path):
    (tmp_path / "keep.jpg").write_bytes(b"x")
    (tmp_path / ".cover.abc.jpg").write_bytes(b"x")
    airplay_meta.prune_covers(tmp_path, keep=50)
    names = sorted(p.name for p in tmp_path.iterdir())
    assert names == ["keep.jpg"]


def test_store_cover_prunes_after_writing(tmp_path):
    # Three distinct covers with keep=2 leaves two files.
    for color in ((10, 20, 30), (200, 100, 50), (0, 150, 250)):
        url = airplay_meta.store_cover(_solid_png(color), tmp_path, "house.local", keep=2)
        assert url is not None
    assert len(list(tmp_path.glob("*.jpg"))) == 2


# --------------------------------------------------------------------------- #
# Resilient pipe reader (pipe_items)
# --------------------------------------------------------------------------- #

CORE = "636f7265"
SSNC = "73736e63"
MINM_HEX = "6d696e6d"


def _item_text(type_hex, code_hex, data=None) -> str:
    if data is None:
        return f"<item><type>{type_hex}</type><code>{code_hex}</code><length>0</length></item>"
    return (
        f"<item><type>{type_hex}</type><code>{code_hex}</code>"
        f"<length>{len(data)}</length>\n"
        f'<data encoding="base64">\n{_b64(data)}</data></item>'
    )


def _sessions(seq):
    """An open_session() that returns each session in seq in turn.

    A session that is an exception instance is raised (an open/read failure); a
    list is returned as a chunk iterator.
    """
    it = iter(seq)

    def open_session():
        try:
            session = next(it)
        except StopIteration:
            return iter([])
        if isinstance(session, BaseException):
            raise session
        return iter(session)

    return open_session


def test_pipe_items_recovers_from_a_read_failure_and_keeps_reading(tmp_path):
    logs, sleeps = [], []
    good = _item_text(CORE, MINM_HEX, b"hi")
    items = list(
        airplay_meta.pipe_items(
            _sessions([RuntimeError("boom"), [good]]),
            log=logs.append,
            sleep=sleeps.append,
            backoff=0.0,
            max_sessions=2,
        )
    )
    assert items == [("item", "core", "minm", b"hi")]
    assert len(logs) == 1  # logged once for the failure
    assert sleeps == [0.0]  # backed off once


def test_pipe_items_logs_once_across_a_failure_streak(tmp_path):
    logs, sleeps = [], []
    good = _item_text(CORE, MINM_HEX, b"ok")
    list(
        airplay_meta.pipe_items(
            _sessions([RuntimeError("a"), RuntimeError("b"), [good]]),
            log=logs.append,
            sleep=sleeps.append,
            backoff=0.0,
            max_sessions=3,
        )
    )
    assert len(logs) == 1  # one log for the whole consecutive-failure streak
    assert len(sleeps) == 2  # but a backoff on each failed attempt


def test_pipe_items_resets_the_parser_between_sessions(tmp_path):
    # Session one leaves a partial <item> (no closing tag); a fresh parser on the
    # next session must not merge it with the next session's complete item.
    partial = "<item><type>636f7265</type><code>6d696e6d</code>"
    full = _item_text(CORE, MINM_HEX, b"ok")
    items = list(
        airplay_meta.pipe_items(
            _sessions([[partial], [full]]),
            log=lambda m: None,
            sleep=lambda s: None,
            max_sessions=2,
        )
    )
    assert items == [("item", "core", "minm", b"ok")]


# --------------------------------------------------------------------------- #
# Player state reset on a new track / stop
# --------------------------------------------------------------------------- #


def _feed(player, items, *, cover_dir, host="house.local"):
    for type_str, code_str, data in items:
        airplay_meta.apply_item(player, type_str, code_str, data, cover_dir=cover_dir, host=host)


def test_a_new_track_without_art_clears_the_previous_cover(tmp_path):
    player = airplay_meta.Player()
    # Track A: metadata bundle + persistent id + a cover.
    _feed(
        player,
        [
            ("ssnc", "mdst", b""),
            ("core", "mper", b"AAAAAAAA"),
            ("core", "minm", b"Song A"),
            ("ssnc", "mden", b""),
            ("ssnc", "PICT", _solid_png((20, 40, 60))),
        ],
        cover_dir=tmp_path,
    )
    assert player.art_url is not None
    # Track B: a different persistent id, no cover.
    _feed(
        player,
        [
            ("ssnc", "mdst", b""),
            ("core", "mper", b"BBBBBBBB"),
            ("core", "minm", b"Song B"),
            ("ssnc", "mden", b""),
        ],
        cover_dir=tmp_path,
    )
    assert player.title == "Song B"
    assert player.art_url is None  # the old cover did not carry over


def test_a_metadata_resend_of_the_same_track_keeps_the_cover(tmp_path):
    player = airplay_meta.Player()
    _feed(
        player,
        [
            ("ssnc", "mdst", b""),
            ("core", "mper", b"AAAAAAAA"),
            ("core", "minm", b"Song A"),
            ("ssnc", "mden", b""),
            ("ssnc", "PICT", _solid_png((20, 40, 60))),
        ],
        cover_dir=tmp_path,
    )
    art = player.art_url
    assert art is not None
    # The same track's metadata is sent again (same id): the cover stays.
    _feed(
        player,
        [
            ("ssnc", "mdst", b""),
            ("core", "mper", b"AAAAAAAA"),
            ("core", "minm", b"Song A"),
            ("ssnc", "mden", b""),
        ],
        cover_dir=tmp_path,
    )
    assert player.art_url == art


def test_mdst_clears_a_field_the_new_bundle_omits(tmp_path):
    player = airplay_meta.Player()
    _feed(
        player,
        [
            ("ssnc", "mdst", b""),
            ("core", "mper", b"AAAAAAAA"),
            ("core", "minm", b"Song A"),
            ("core", "asal", b"Album A"),
            ("ssnc", "mden", b""),
        ],
        cover_dir=tmp_path,
    )
    assert player.album == "Album A"
    # New track with no album: the previous album must not carry over.
    _feed(
        player,
        [
            ("ssnc", "mdst", b""),
            ("core", "mper", b"BBBBBBBB"),
            ("core", "minm", b"Song B"),
            ("ssnc", "mden", b""),
        ],
        cover_dir=tmp_path,
    )
    assert player.album is None


def test_stop_sets_playback_status_stopped():
    player = airplay_meta.Player()
    airplay_meta.apply_item(player, "ssnc", "pbeg", b"", cover_dir=Path("/x"), host="h")
    assert player.playback_status == "playing"
    airplay_meta.apply_item(player, "ssnc", "pend", b"", cover_dir=Path("/x"), host="h")
    assert player.playback_status == "stopped"


# --------------------------------------------------------------------------- #
# Duration, position, and genre (prgr / astm / asgn)
# --------------------------------------------------------------------------- #


def test_prgr_sets_duration_and_position():
    # ssnc/prgr is ASCII start/current/end RTP frame numbers at 44100 Hz:
    # duration is (end - start) / 44100, position is (current - start) / 44100.
    clock = FakeClock(100.0)
    p = airplay_meta.Player(clock=clock)
    airplay_meta.apply_item(p, "ssnc", "prgr", b"1000/45100/1324000", cover_dir=TMP, host="h")
    props = p.properties()
    assert props["metadata"]["duration"] == pytest.approx((1324000 - 1000) / 44100)
    assert props["position"] == pytest.approx(1.0, abs=0.01)


def test_position_extrapolates_at_emit_time_while_playing():
    # An unrelated resend (art arriving) after a long play must report the
    # position at emit time, not the stale anchor (Review Focus 1).
    clock = FakeClock(100.0)
    p = airplay_meta.Player(clock=clock)
    airplay_meta.apply_item(p, "ssnc", "pbeg", b"", cover_dir=TMP, host="h")
    # 0/0/44100000 is a 1000 s track, long enough that 300 s stays within it.
    airplay_meta.apply_item(p, "ssnc", "prgr", b"0/0/44100000", cover_dir=TMP, host="h")
    clock.advance(300)
    airplay_meta.apply_item(p, "ssnc", "PICT", b"", cover_dir=TMP, host="h")  # unrelated resend
    assert p.properties()["position"] == pytest.approx(300.0)


def test_position_frozen_while_paused():
    clock = FakeClock(100.0)
    p = airplay_meta.Player(clock=clock)
    airplay_meta.apply_item(p, "ssnc", "pbeg", b"", cover_dir=TMP, host="h")
    airplay_meta.apply_item(p, "ssnc", "prgr", b"0/0/44100000", cover_dir=TMP, host="h")
    clock.advance(60)
    airplay_meta.apply_item(p, "ssnc", "pfls", b"", cover_dir=TMP, host="h")  # pause
    clock.advance(600)
    # Paused freezes the position at the moment of the pause.
    assert p.properties()["position"] == pytest.approx(60.0)


def test_position_clamps_to_duration():
    # Playing past the end (no new prgr) must not report beyond the duration.
    clock = FakeClock(0.0)
    p = airplay_meta.Player(clock=clock)
    airplay_meta.apply_item(p, "ssnc", "pbeg", b"", cover_dir=TMP, host="h")
    airplay_meta.apply_item(p, "ssnc", "prgr", b"0/0/4410000", cover_dir=TMP, host="h")  # 100 s
    clock.advance(500)
    assert p.properties()["position"] == pytest.approx(100.0)


def test_astm_is_fallback_duration_only():
    # core/astm (4-byte big-endian ms) is a duration fallback when prgr is absent.
    p = airplay_meta.Player()
    airplay_meta.apply_item(p, "core", "astm", (322000).to_bytes(4, "big"), cover_dir=TMP, host="h")
    assert p.properties()["metadata"]["duration"] == pytest.approx(322.0)
    # With a prgr duration already known, astm must not override it.
    p2 = airplay_meta.Player()
    airplay_meta.apply_item(p2, "ssnc", "prgr", b"0/0/44100000", cover_dir=TMP, host="h")  # 1000 s
    airplay_meta.apply_item(p2, "core", "astm", (322000).to_bytes(4, "big"), cover_dir=TMP, host="h")
    assert p2.properties()["metadata"]["duration"] == pytest.approx(1000.0)


def test_asgn_sets_genre_and_mdst_clears_it():
    p = airplay_meta.Player()
    airplay_meta.apply_item(p, "ssnc", "mdst", b"", cover_dir=TMP, host="h")
    airplay_meta.apply_item(p, "core", "mper", b"AAAAAAAA", cover_dir=TMP, host="h")
    airplay_meta.apply_item(p, "core", "asgn", b"Podcast", cover_dir=TMP, host="h")
    airplay_meta.apply_item(p, "ssnc", "mden", b"", cover_dir=TMP, host="h")
    assert p.properties()["metadata"]["genre"] == ["Podcast"]
    # A new track with no genre: mdst clears it, so no genre key survives.
    airplay_meta.apply_item(p, "ssnc", "mdst", b"", cover_dir=TMP, host="h")
    airplay_meta.apply_item(p, "core", "mper", b"BBBBBBBB", cover_dir=TMP, host="h")
    airplay_meta.apply_item(p, "ssnc", "mden", b"", cover_dir=TMP, host="h")
    assert "genre" not in p.properties().get("metadata", {})
