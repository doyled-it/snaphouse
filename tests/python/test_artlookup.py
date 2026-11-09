"""Tests for the hub's art lookup for tracks that arrive without a cover.

The network is injected as a ``Fetch`` callable, so no test touches the real
APIs: a fake fetch serves the trimmed, recorded fixtures under
``fixtures/artlookup/`` and a generated JPEG for any cover URL. The only real
work is Pillow decoding and the disk cache under ``tmp_path``.
"""

import json
import os
import time
import urllib.parse
from io import BytesIO
from pathlib import Path

import pytest

from snaphouse import artlookup
from snaphouse.artlookup import ArtLookup, keep_for_history, prune_history_covers, route

FIXTURES = Path(__file__).parent / "fixtures" / "artlookup"


def _fixture_bytes(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def _jpeg(color=(200, 60, 60), size=(800, 800)) -> bytes:
    from PIL import Image

    buffer = BytesIO()
    Image.new("RGB", size, color).save(buffer, format="JPEG")
    return buffer.getvalue()


class FakeFetch:
    """A ``Fetch`` that routes by URL substring and records every call.

    ``routes`` is a list of ``(substring, response)`` where response is bytes or
    a callable taking the URL. The first matching substring wins; an unmatched
    URL returns ``None`` (a failure), like the real fetch.
    """

    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def __call__(self, url):
        self.calls.append(url)
        for substring, response in self.routes:
            if substring in url:
                return response(url) if callable(response) else response
        return None


def _lookup_dir(cover_root: Path) -> Path:
    return cover_root / "lookup"


def _frozen_clock():
    """A clock stuck at one instant, so a MusicBrainz gate never waits real time."""
    return lambda: 1000.0


def _gate_recorder():
    """A (clock, sleep, slept) trio: sleep advances the clock and records waits."""
    now = [1000.0]
    slept = []

    def clock():
        return now[0]

    def sleep(seconds):
        slept.append(seconds)
        now[0] += seconds

    return clock, sleep, slept


# --------------------------------------------------------------------------- #
# route
# --------------------------------------------------------------------------- #


def test_route():
    assert route("long_form", None) == "podcast"
    assert route("music", ["Podcast"]) == "podcast"
    assert route("long_form", ["Audiobook"]) == "audiobook"
    assert route("long_form", ["Electronic"]) == "music"


def test_route_spoken_is_audiobook():
    assert route("music", ["Spoken Word"]) == "audiobook"


def test_route_music_with_no_genre_is_music():
    assert route("music", None) == "music"


# --------------------------------------------------------------------------- #
# podcasts
# --------------------------------------------------------------------------- #


def test_podcast_exact_match_saves_cover(tmp_path):
    fetch = FakeFetch(
        [
            ("itunes.apple.com", _fixture_bytes("itunes_podcast.json")),
            ("mzstatic.com", _jpeg()),
        ]
    )
    lookup = ArtLookup(tmp_path, fetch)

    url = lookup.find(
        "Episode 1", "This American Life", "This American Life", "long_form", ["Podcast"]
    )

    assert url is not None
    assert url.startswith("/cover/lookup/")
    assert url.endswith(".jpg")
    saved = _lookup_dir(tmp_path) / Path(url).name
    assert saved.is_file()

    from PIL import Image

    with Image.open(saved) as image:
        assert max(image.size) <= 600
        assert image.format == "JPEG"


def test_find_logs_one_line_per_outcome(tmp_path, caplog):
    import logging

    # A cover found, a real miss, and a transient failure each log one INFO line
    # naming the lookup and its outcome, carrying only the show name.
    found = FakeFetch(
        [
            ("itunes.apple.com", _fixture_bytes("itunes_podcast.json")),
            ("mzstatic.com", _jpeg()),
        ]
    )
    lookup = ArtLookup(tmp_path, found)
    with caplog.at_level(logging.INFO, logger="snaphouse.artlookup"):
        lookup.find(
            "Episode 1", "This American Life", "This American Life", "long_form", ["Podcast"]
        )
    assert any(
        record.levelno == logging.INFO
        and "This American Life" in record.getMessage()
        and "cover found" in record.getMessage()
        for record in caplog.records
    )

    # A music search whose body parses but names no matching release is a real
    # miss (no image fetch, a marker written).
    miss = FakeFetch([("musicbrainz.org", b'{"releases": []}')])
    lookup = ArtLookup(tmp_path, miss, clock=_frozen_clock(), sleep=lambda _s: None)
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="snaphouse.artlookup"):
        lookup.find("Track", "Nobody", "Unknown Album", "music", None)
    assert any("miss" in r.getMessage() and "Unknown Album" in r.getMessage() for r in caplog.records)

    # Every fetch fails (no routes match), so the search is a transient failure.
    failing = FakeFetch([])
    lookup = ArtLookup(tmp_path, failing)
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="snaphouse.artlookup"):
        lookup.find("Episode 1", "Fresh Show", "Fresh Show", "long_form", ["Podcast"])
    assert any("failure" in r.getMessage() and "Fresh Show" in r.getMessage() for r in caplog.records)


def test_podcast_no_exact_match_returns_none(tmp_path):
    fetch = FakeFetch(
        [
            ("itunes.apple.com", _fixture_bytes("itunes_podcast.json")),
            ("mzstatic.com", _jpeg()),
        ]
    )
    lookup = ArtLookup(tmp_path, fetch)

    # The fixture only has "This American Life" and "School Colors"; neither
    # matches this show, so there is no cover and no image is fetched.
    url = lookup.find("Episode 1", "Some Host", "A Different Show", "long_form", ["Podcast"])

    assert url is None
    assert not any("mzstatic.com" in call for call in fetch.calls)


def test_long_form_podcast_miss_falls_back_to_open_library(tmp_path):
    fetch = FakeFetch(
        [
            # The podcast search has no match for a book title.
            ("itunes.apple.com", _fixture_bytes("itunes_podcast.json")),
            ("openlibrary.org/search", _fixture_bytes("openlibrary.json")),
            ("covers.openlibrary.org", _jpeg()),
        ]
    )
    lookup = ArtLookup(tmp_path, fetch)

    # No genre and long_form: route is podcast, which misses, then Open Library
    # by title (the album) and author (the artist) matches.
    url = lookup.find(
        "Chapter 1", "Jane Austen", "Pride and Prejudice", "long_form", None
    )

    assert url is not None
    assert url.startswith("/cover/lookup/")
    assert any("covers.openlibrary.org" in call for call in fetch.calls)


def test_audiobook_genre_uses_open_library(tmp_path):
    fetch = FakeFetch(
        [
            ("openlibrary.org/search", _fixture_bytes("openlibrary.json")),
            ("covers.openlibrary.org", _jpeg()),
        ]
    )
    lookup = ArtLookup(tmp_path, fetch)

    url = lookup.find(
        "Chapter 1", "Jane Austen", "Pride and Prejudice", "long_form", ["Audiobook"]
    )

    assert url is not None
    # It never asks iTunes for an audiobook.
    assert not any("itunes.apple.com" in call for call in fetch.calls)


# --------------------------------------------------------------------------- #
# base name before a suffix
# --------------------------------------------------------------------------- #


def _itunes_result(collection_name: str) -> bytes:
    # A one-result iTunes podcast search body with a given collectionName and an
    # artwork URL on the mzstatic CDN, so a matched result fetches a cover.
    return json.dumps(
        {
            "resultCount": 1,
            "results": [
                {
                    "collectionName": collection_name,
                    "artistName": collection_name,
                    "artworkUrl600": "https://is1-ssl.mzstatic.com/image/x/600x600bb.jpg",
                }
            ],
        }
    ).encode("utf-8")


def test_base_name_cuts_at_earliest_separator():
    # The earliest-occurring separator wins, and the base is trimmed.
    assert artlookup._base_name("Harbor Notes - Patreon Feed") == "Harbor Notes"
    assert artlookup._base_name("Night Harbor: A Novel") == "Night Harbor"
    assert artlookup._base_name("Night Harbor (Deluxe Edition)") == "Night Harbor"
    assert artlookup._base_name("Radio One | Morning") == "Radio One"
    # The colon before the dash is earliest, so "Lane" wins over "Lane: Alpha".
    assert artlookup._base_name("Lane: Alpha - Beta") == "Lane"


def test_base_name_rejects_short_or_separatorless():
    # No separator: nothing to retry on.
    assert artlookup._base_name("Seaside Review") is None
    # A base shorter than three characters is rejected.
    assert artlookup._base_name("AB - The Rest") is None
    # A leading separator leaves an empty base.
    assert artlookup._base_name(" - Patreon Feed") is None


def test_podcast_suffix_finds_base_show(tmp_path):
    # A podcast app sends "<Show> - Patreon Feed"; the public show is "<Show>".
    # The full name finds no exact match, so the base before the separator does.
    fetch = FakeFetch(
        [
            ("itunes.apple.com", _itunes_result("Harbor Notes")),
            ("mzstatic.com", _jpeg()),
        ]
    )
    lookup = ArtLookup(tmp_path, fetch)

    url = lookup.find(
        "Episode 7",
        "Harbor Notes - Patreon Feed",
        "Harbor Notes - Patreon Feed",
        "long_form",
        ["Podcast"],
    )

    assert url is not None
    assert url.startswith("/cover/lookup/")
    # Two searches ran: the full name, then the base name. Both carry only names.
    itunes_calls = [c for c in fetch.calls if "itunes.apple.com" in c]
    assert len(itunes_calls) == 2
    terms = [
        urllib.parse.parse_qs(urllib.parse.urlsplit(c).query).get("term")[0]
        for c in itunes_calls
    ]
    assert terms == ["Harbor Notes - Patreon Feed", "Harbor Notes"]
    for call in itunes_calls:
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(call).query)
        assert set(query) == {"media", "entity", "term"}
    # No miss marker: the second attempt found a cover.
    assert not list(_lookup_dir(tmp_path).glob("*.none"))


def test_podcast_no_separator_single_search(tmp_path):
    # A show with no separator behaves as today: one search, and a parsed miss.
    fetch = FakeFetch([("itunes.apple.com", _itunes_result("Harbor Notes"))])
    lookup = ArtLookup(tmp_path, fetch)

    # Music kind with a Podcast genre routes to the podcast lookup without the
    # long-form Open Library fallback, so this exercises only the one search.
    url = lookup.find(
        "Episode 7", "Seaside Review", "Seaside Review", "music", ["Podcast"]
    )

    assert url is None
    itunes_calls = [c for c in fetch.calls if "itunes.apple.com" in c]
    assert len(itunes_calls) == 1
    # A single parsed miss is cached.
    assert list(_lookup_dir(tmp_path).glob("*.none"))


def test_podcast_base_must_match_exactly(tmp_path):
    # The base name search returns a near name, not an exact match, so it is still
    # a miss and the marker is written.
    fetch = FakeFetch([("itunes.apple.com", _itunes_result("Harbor Notes Daily"))])
    lookup = ArtLookup(tmp_path, fetch)

    # Music kind with a Podcast genre keeps the podcast route without the
    # long-form fallback, so both attempts parse and a single marker is written.
    url = lookup.find(
        "Episode 7",
        "Harbor Notes - Patreon Feed",
        "Harbor Notes - Patreon Feed",
        "music",
        ["Podcast"],
    )

    assert url is None
    itunes_calls = [c for c in fetch.calls if "itunes.apple.com" in c]
    assert len(itunes_calls) == 2
    assert not any("mzstatic.com" in c for c in fetch.calls)
    assert list(_lookup_dir(tmp_path).glob("*.none"))


def test_music_suffix_finds_base_release(tmp_path):
    # The release title carries a suffix; the base matches, artist still enforced.
    body = json.dumps(
        {
            "releases": [
                {
                    "id": "11111111-2222-3333-4444-555555555555",
                    "title": "Night Harbor",
                    "artist-credit": [{"name": "The Lantern Keepers"}],
                }
            ]
        }
    ).encode("utf-8")
    fetch = FakeFetch(
        [
            ("musicbrainz.org", body),
            ("coverartarchive.org", _jpeg()),
        ]
    )
    clock, sleep, slept = _gate_recorder()
    lookup = ArtLookup(tmp_path, fetch, clock=clock, sleep=sleep)

    url = lookup.find(
        "Track 2",
        "The Lantern Keepers",
        "Night Harbor (Deluxe Edition)",
        "music",
        None,
    )

    assert url is not None
    mb_calls = [c for c in fetch.calls if "musicbrainz.org" in c]
    assert len(mb_calls) == 2
    queries = [
        urllib.parse.parse_qs(urllib.parse.urlsplit(c).query)["query"][0]
        for c in mb_calls
    ]
    assert queries == [
        'artist:"The Lantern Keepers" AND release:"Night Harbor (Deluxe Edition)"',
        'artist:"The Lantern Keepers" AND release:"Night Harbor"',
    ]
    # Each MusicBrainz request keeps only the query, format, and limit fields.
    for call in mb_calls:
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(call).query)
        assert set(query) == {"query", "fmt", "limit"}
    # The base attempt waited out the one-per-second gate after the full name.
    assert slept == pytest.approx([1.0], abs=0.01)


def test_music_base_miss_writes_marker_once(tmp_path):
    # Both attempts parse and miss, so a single miss marker is cached.
    fetch = FakeFetch([("musicbrainz.org", _empty_musicbrainz())])
    clock, sleep, slept = _gate_recorder()
    lookup = ArtLookup(tmp_path, fetch, clock=clock, sleep=sleep)

    url = lookup.find(
        "Track 2", "The Lantern Keepers", "Night Harbor: Live", "music", None
    )

    assert url is None
    assert len(list(_lookup_dir(tmp_path).glob("*.none"))) == 1
    assert len([c for c in fetch.calls if "musicbrainz.org" in c]) == 2
    # The base attempt waited out the gate after the full-name attempt.
    assert slept == pytest.approx([1.0], abs=0.01)


def test_base_attempt_error_is_not_cached(tmp_path):
    # The full-name search parses and misses, but the base-name search fetch fails
    # (returns None). That is transient, so no miss marker is written. The music
    # route has no fallback leg, so this exercises the base-attempt error in
    # isolation: a mutation that caches a base-attempt error cannot pass.
    first = {"done": False}

    def musicbrainz(url):
        if not first["done"]:
            first["done"] = True
            return _empty_musicbrainz()  # full name parses and misses
        return None  # base name fetch fails (transient)

    fetch = FakeFetch([("musicbrainz.org", musicbrainz)])
    lookup = ArtLookup(tmp_path, fetch, clock=_frozen_clock(), sleep=lambda _s: None)

    url = lookup.find(
        "Track 2", "The Lantern Keepers", "Night Harbor: Live", "music", None
    )

    assert url is None
    # Two MusicBrainz requests: the full name, then the base name that failed.
    assert len([c for c in fetch.calls if "musicbrainz.org" in c]) == 2
    lookup_dir = _lookup_dir(tmp_path)
    assert not (lookup_dir.exists() and list(lookup_dir.glob("*.none")))


def test_full_name_error_skips_base_attempt(tmp_path):
    # A full-name search that fails (returns None) is transient. The base attempt
    # must not run, and no miss marker is written.
    fetch = FakeFetch([])  # every URL is unmatched, so the fetch returns None
    lookup = ArtLookup(tmp_path, fetch, clock=_frozen_clock(), sleep=lambda _s: None)

    url = lookup.find(
        "Track 2", "The Lantern Keepers", "Night Harbor: Live", "music", None
    )

    assert url is None
    # Only the full-name request ran; the base attempt was skipped.
    assert len([c for c in fetch.calls if "musicbrainz.org" in c]) == 1
    lookup_dir = _lookup_dir(tmp_path)
    assert not (lookup_dir.exists() and list(lookup_dir.glob("*.none")))


def test_audiobook_suffix_finds_base_title(tmp_path):
    # The Open Library route retries on the base title too, and its base request
    # carries only the title and author names.
    body = json.dumps(
        {"docs": [{"title": "Night Harbor", "cover_i": 4242}]}
    ).encode("utf-8")
    fetch = FakeFetch(
        [
            ("openlibrary.org/search", body),
            ("covers.openlibrary.org", _jpeg()),
        ]
    )
    lookup = ArtLookup(tmp_path, fetch)

    url = lookup.find(
        "Chapter 1",
        "Marisol Vane",
        "Night Harbor: A Novel",
        "long_form",
        ["Audiobook"],
    )

    assert url is not None
    search_calls = [c for c in fetch.calls if "openlibrary.org/search" in c]
    assert len(search_calls) == 2
    titles = [
        urllib.parse.parse_qs(urllib.parse.urlsplit(c).query)["title"][0]
        for c in search_calls
    ]
    assert titles == ["Night Harbor: A Novel", "Night Harbor"]
    for call in search_calls:
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(call).query)
        # Only the title and author names go out.
        assert set(query) == {"title", "author"}
        assert query["author"] == ["Marisol Vane"]


# --------------------------------------------------------------------------- #
# music
# --------------------------------------------------------------------------- #


def test_music_requires_artist_match(tmp_path):
    fetch = FakeFetch(
        [
            ("musicbrainz.org", _fixture_bytes("musicbrainz.json")),
            ("coverartarchive.org", _jpeg()),
        ]
    )
    lookup = ArtLookup(tmp_path, fetch)

    matched = lookup.find("Airbag", "Radiohead", "OK Computer", "music", None)
    assert matched is not None
    assert matched.startswith("/cover/lookup/")

    # The release matches the album but the artist does not, so no cover.
    mismatched = lookup.find("Airbag", "Not Radiohead", "OK Computer", "music", None)
    assert mismatched is None


def test_no_artist_no_album_music_skips_lookup(tmp_path):
    fetch = FakeFetch([("", _jpeg())])
    lookup = ArtLookup(tmp_path, fetch)

    url = lookup.find("Some Internet Radio", None, None, "music", None)

    assert url is None
    assert fetch.calls == []


# --------------------------------------------------------------------------- #
# miss cache
# --------------------------------------------------------------------------- #


def _empty_musicbrainz() -> bytes:
    return json.dumps({"count": 0, "releases": []}).encode("utf-8")


def test_miss_is_cached_for_seven_days(tmp_path):
    fetch = FakeFetch([("musicbrainz.org", _empty_musicbrainz())])
    lookup = ArtLookup(tmp_path, fetch)

    assert lookup.find("Song", "Nobody", "No Such Album", "music", None) is None
    first = len(fetch.calls)
    assert first >= 1

    # A second lookup within the window makes no request.
    assert lookup.find("Song", "Nobody", "No Such Album", "music", None) is None
    assert len(fetch.calls) == first

    # Age the miss marker past the seven-day window; the lookup retries.
    marker = next(_lookup_dir(tmp_path).glob("*.none"))
    eight_days_ago = time.time() - 8 * 86400
    os.utime(marker, (eight_days_ago, eight_days_ago))

    assert lookup.find("Song", "Nobody", "No Such Album", "music", None) is None
    assert len(fetch.calls) > first


def test_fetch_failure_is_not_cached_as_miss(tmp_path):
    # A search fetch that fails (returns None, a Wi-Fi blip) must not be cached as
    # a 7-day miss. The next lookup tries the network again.
    fetch = FakeFetch([])  # every URL is unmatched, so the fetch returns None
    lookup = ArtLookup(tmp_path, fetch)

    assert lookup.find("Song", "An Artist", "An Album", "music", None) is None
    first = len(fetch.calls)
    assert first >= 1

    lookup_dir = _lookup_dir(tmp_path)
    assert not (lookup_dir.exists() and list(lookup_dir.glob("*.none")))

    # Not suppressed: the second lookup reaches the network again.
    assert lookup.find("Song", "An Artist", "An Album", "music", None) is None
    assert len(fetch.calls) > first


def test_cover_fetch_failure_is_not_cached_as_miss(tmp_path):
    # The search finds an exact match, but fetching the cover image itself fails.
    # That is transient too, so no miss marker is written.
    fetch = FakeFetch([("musicbrainz.org", _fixture_bytes("musicbrainz.json"))])
    lookup = ArtLookup(tmp_path, fetch)

    assert lookup.find("Airbag", "Radiohead", "OK Computer", "music", None) is None
    assert any("coverartarchive.org" in call for call in fetch.calls)

    lookup_dir = _lookup_dir(tmp_path)
    assert not (lookup_dir.exists() and list(lookup_dir.glob("*.none")))


def test_parsed_search_with_no_match_is_cached_as_miss(tmp_path):
    # A search that returned and parsed but found no exact match is a real miss
    # and is cached, so a findable-nowhere track is not searched for every play.
    fetch = FakeFetch([("musicbrainz.org", _empty_musicbrainz())])
    lookup = ArtLookup(tmp_path, fetch)

    assert lookup.find("Song", "Nobody", "No Such Album", "music", None) is None
    assert list(_lookup_dir(tmp_path).glob("*.none"))


def test_musicbrainz_query_escapes_lucene_specials(tmp_path):
    # A quote or backslash in a name must be escaped inside the quoted Lucene
    # phrase, or it breaks out of the phrase (or corrupts the query).
    fetch = FakeFetch([("musicbrainz.org", _empty_musicbrainz())])
    lookup = ArtLookup(tmp_path, fetch)

    lookup.find("Song", 'A"B', "C\\D", "music", None)

    mb_calls = [c for c in fetch.calls if "musicbrainz.org" in c]
    assert mb_calls
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(mb_calls[0]).query)["query"][0]
    assert query == 'artist:"A\\"B" AND release:"C\\\\D"'


def test_musicbrainz_gate_sleeps(tmp_path):
    now = [1000.0]
    slept = []

    def clock():
        return now[0]

    def sleep(seconds):
        slept.append(seconds)
        now[0] += seconds

    fetch = FakeFetch([("musicbrainz.org", _empty_musicbrainz())])
    lookup = ArtLookup(tmp_path, fetch, clock=clock, sleep=sleep)

    # First lookup sets the gate; no wait needed.
    lookup.find("A", "Artist One", "Album One", "music", None)
    assert slept == []

    # Second lookup, no time elapsed, must wait out the one-per-second gate.
    lookup.find("B", "Artist Two", "Album Two", "music", None)
    assert slept
    assert slept[0] == pytest.approx(1.0, abs=0.01)


# --------------------------------------------------------------------------- #
# pruning
# --------------------------------------------------------------------------- #


def test_prune_only_touches_lookup_dir(tmp_path):
    # An AirPlay cover lives directly in cover/, not under cover/lookup/.
    airplay_cover = tmp_path / "airplaycover0000000000000000000.jpg"
    airplay_cover.write_bytes(b"airplay")

    lookup_dir = _lookup_dir(tmp_path)
    lookup_dir.mkdir(parents=True)
    for index in range(4):
        stale = lookup_dir / f"stale{index}.jpg"
        stale.write_bytes(b"stale")
        old = time.time() - (100 - index)
        os.utime(stale, (old, old))

    fetch = FakeFetch(
        [
            ("itunes.apple.com", _fixture_bytes("itunes_podcast.json")),
            ("mzstatic.com", _jpeg()),
        ]
    )
    lookup = ArtLookup(tmp_path, fetch, max_files=2)

    url = lookup.find(
        "Episode 1", "This American Life", "This American Life", "long_form", ["Podcast"]
    )
    assert url is not None

    # The AirPlay cover in cover/ is untouched.
    assert airplay_cover.is_file()
    # The lookup dir is pruned to max_files.
    assert len(list(lookup_dir.glob("*.jpg"))) == 2


# --------------------------------------------------------------------------- #
# history covers
# --------------------------------------------------------------------------- #


def test_keep_for_history_and_prune(tmp_path):
    public_root = tmp_path
    lookup_dir = public_root / "cover" / "lookup"
    lookup_dir.mkdir(parents=True)
    source = lookup_dir / "abc123.jpg"
    source.write_bytes(_jpeg())

    kept = keep_for_history("/cover/lookup/abc123.jpg", public_root)
    assert kept == "/cover/history/abc123.jpg"
    assert (public_root / "cover" / "history" / "abc123.jpg").is_file()

    # Nothing to copy, not under /cover/, or missing on disk: None.
    assert keep_for_history(None, public_root) is None
    assert keep_for_history("/art/elsewhere.jpg", public_root) is None
    assert keep_for_history("/cover/missing.jpg", public_root) is None

    # A second history cover that is no longer referenced.
    orphan = public_root / "cover" / "history" / "orphan.jpg"
    orphan.write_bytes(_jpeg())

    prune_history_covers(public_root, {"/cover/history/abc123.jpg"})

    assert (public_root / "cover" / "history" / "abc123.jpg").is_file()
    assert not orphan.exists()


def test_keep_for_history_handles_airplay_cover(tmp_path):
    public_root = tmp_path
    cover_dir = public_root / "cover"
    cover_dir.mkdir(parents=True)
    (cover_dir / "deadbeef.jpg").write_bytes(_jpeg())

    kept = keep_for_history("/cover/deadbeef.jpg", public_root)

    assert kept == "/cover/history/deadbeef.jpg"
    assert (cover_dir / "history" / "deadbeef.jpg").is_file()


# --------------------------------------------------------------------------- #
# privacy
# --------------------------------------------------------------------------- #


def test_request_urls_carry_only_names(tmp_path):
    fetch = FakeFetch(
        [
            ("itunes.apple.com", _fixture_bytes("itunes_podcast.json")),
            ("mzstatic.com", _jpeg()),
            ("musicbrainz.org", _fixture_bytes("musicbrainz.json")),
            ("coverartarchive.org", _jpeg()),
        ]
    )
    lookup = ArtLookup(tmp_path, fetch)

    lookup.find("Airbag", "Radiohead", "OK Computer", "music", None)
    lookup.find("Episode 1", "This American Life", "This American Life", "long_form", ["Podcast"])

    itunes_calls = [c for c in fetch.calls if "itunes.apple.com" in c]
    assert itunes_calls
    for call in itunes_calls:
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(call).query)
        # The only free-text field is the show name.
        assert query.get("term") == ["This American Life"]

    mb_calls = [c for c in fetch.calls if "musicbrainz.org" in c]
    assert mb_calls
    for call in mb_calls:
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(call).query)
        assert query.get("query") == ['artist:"Radiohead" AND release:"OK Computer"']


def test_user_agent_names_the_public_repo():
    agent = artlookup._user_agent()
    assert agent.startswith("snaphouse/")
    assert "github.com/doyled-it/snaphouse" in agent
