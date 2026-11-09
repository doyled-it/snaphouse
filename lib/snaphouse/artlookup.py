"""Art lookup for House tracks that arrive without a cover.

A track from AirPlay carries its own cover, but a track from Bluetooth does not.
This module finds one from public catalogues, keyed by what the track names:

- podcasts from Apple's iTunes Search API,
- audiobooks from Open Library,
- music from MusicBrainz plus the Cover Art Archive.

Nothing identifying goes out. A request carries only the track, show, book, or
artist names, and a User-Agent naming the public repository. The design is in
``docs/superpowers/specs/2026-11-08-now-playing-design.md``.

Every looked-up image is cached under ``cover_root/lookup/`` keyed by a hash of
the lookup, resized to at most 600 px, and written atomically so nginx never
serves a half-written file. A miss is remembered as a ``<key>.none`` marker and
suppresses that lookup for seven days, so a track with no findable cover is not
searched for on every play. Pruning touches only ``cover_root/lookup/``, so it
never deletes the AirPlay bridge's own covers that sit directly in
``cover_root/``.

The network is injected as a ``Fetch`` callable. ``urllib_fetch`` is the real
one; the tests pass a fake that serves recorded fixtures, so no test touches the
network. All HTTP is stdlib ``urllib``: the hub gains no new runtime dependency,
and Pillow (already a hub dependency for the AirPlay bridge) does the resizing.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import re
import tempfile
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable, Optional

from .airplay_meta import downscale_cover

# A fetch gets a URL and returns the body, or None on any failure (a bad status,
# a timeout, a transport error, or an oversized body). The caller never sees the
# reason: a failed fetch is just "no data", handled the same as an empty result.
Fetch = Callable[[str], Optional[bytes]]

_TIMEOUT = 10.0
# A cover is small; refuse a body larger than this so a hostile or broken source
# cannot stream an unbounded response into memory on the appliance.
_MAX_BYTES = 10 * 1024 * 1024

_ITUNES_SEARCH = "https://itunes.apple.com/search"
_OPEN_LIBRARY_SEARCH = "https://openlibrary.org/search.json"
_OPEN_LIBRARY_COVER = "https://covers.openlibrary.org/b/id/{cover_id}-L.jpg"
_MUSICBRAINZ_SEARCH = "https://musicbrainz.org/ws/2/release/"
_COVER_ART_ARCHIVE = "https://coverartarchive.org/release/{mbid}/front-500"

# MusicBrainz asks for no more than one request per second.
_MUSICBRAINZ_MIN_INTERVAL = 1.0

_NONE_SUFFIX = ".none"
_DEFAULT_MISS_TTL = 7 * 86400
_DEFAULT_MAX_FILES = 300

_user_agent_cache: str | None = None


def _package_version() -> str:
    # snaphouse runs from a checkout, not an installed package, so the metadata
    # lookup usually misses; fall back to a neutral placeholder. The version is
    # cosmetic here. What matters is the repo URL, which carries nothing personal.
    try:
        return importlib.metadata.version("snaphouse")
    except importlib.metadata.PackageNotFoundError:
        return "0"


def _user_agent() -> str:
    global _user_agent_cache
    if _user_agent_cache is None:
        _user_agent_cache = (
            f"snaphouse/{_package_version()} "
            "(+https://github.com/doyled-it/snaphouse)"
        )
    return _user_agent_cache


def urllib_fetch(url: str) -> Optional[bytes]:
    """GET ``url`` with a short timeout, returning the body or None on failure.

    The body is capped at ``_MAX_BYTES`` and compression is refused so a small
    compressed response cannot amplify past the cap. Any error (bad status,
    timeout, transport failure, oversize body) resolves to None, so a lookup
    never raises into the background thread that calls it.
    """
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": _user_agent(),
            "Accept-Encoding": "identity",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT) as response:
            data = response.read(_MAX_BYTES + 1)
    except (urllib.error.URLError, OSError, ValueError):
        return None
    if len(data) > _MAX_BYTES:
        return None
    return data


def route(kind: str, genre: Optional[list[str]]) -> str:
    """The lookup to use: ``"podcast"``, ``"audiobook"``, or ``"music"``.

    A genre naming a podcast wins; one naming an audiobook or spoken word gives
    audiobook; any other genre gives music, so genre outranks duration (a long
    DJ mix tagged music goes to the music lookup despite its length). With no
    genre, a ``long_form`` track starts at the podcast lookup (``find`` falls
    back to Open Library on a miss) and anything else is music.
    """
    genres = [value.casefold() for value in (genre or [])]
    if genres:
        if any("podcast" in text for text in genres):
            return "podcast"
        if any("audiobook" in text or "spoken" in text for text in genres):
            return "audiobook"
        return "music"
    return "podcast" if kind == "long_form" else "music"


def _normalize(value: Optional[str]) -> str:
    """A name reduced for comparison: case-folded, punctuation and accents gone.

    Two names match when their normalized forms are equal. NFKD plus dropping
    combining marks folds accents, casefold folds case, non-alphanumeric runs
    collapse to single spaces, and the ends are trimmed.
    """
    if not value:
        return ""
    decomposed = unicodedata.normalize("NFKD", value)
    without_marks = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    folded = without_marks.casefold()
    collapsed = re.sub(r"[^0-9a-z]+", " ", folded)
    return collapsed.strip()


def _names_match(left: Optional[str], right: Optional[str]) -> bool:
    normalized = _normalize(left)
    return bool(normalized) and normalized == _normalize(right)


def keep_for_history(art_url: Optional[str], public_root: Path) -> Optional[str]:
    """Copy a referenced cover into ``cover/history/`` so it survives pruning.

    ``art_url`` is a same-origin cover path such as ``/cover/lookup/<sha1>.jpg``
    or ``/cover/<sha>.jpg``. The file is copied into ``public_root/cover/history/``
    under its own name and a ``/cover/history/<name>`` path is returned. Anything
    that is not a ``/cover/`` path, escapes the cover directory, or is missing on
    disk returns None, so a bad or absent cover simply leaves the history entry
    without one.
    """
    if not art_url:
        return None
    cover_dir = (public_root / "cover").resolve()
    source = _cover_path(art_url, public_root)
    if source is None:
        return None
    try:
        resolved = source.resolve()
        resolved.relative_to(cover_dir)
    except (OSError, ValueError):
        return None
    if not resolved.is_file():
        return None

    history_dir = public_root / "cover" / "history"
    target = history_dir / resolved.name
    try:
        history_dir.mkdir(parents=True, exist_ok=True)
        _atomic_write(target, resolved.read_bytes())
    except OSError:
        return None
    return f"/cover/history/{resolved.name}"


def prune_history_covers(public_root: Path, keep_urls: set[str]) -> None:
    """Delete history covers whose ``/cover/history/<name>`` is unreferenced.

    ``keep_urls`` is every history ``art_url`` still named by a play. Best-effort:
    any filesystem error is ignored so pruning never breaks the caller.
    """
    history_dir = public_root / "cover" / "history"
    try:
        entries = list(history_dir.iterdir())
    except OSError:
        return
    for entry in entries:
        if f"/cover/history/{entry.name}" in keep_urls:
            continue
        try:
            entry.unlink()
        except OSError:
            pass


def _cover_path(art_url: str, public_root: Path) -> Optional[Path]:
    """The on-disk path for a ``/cover/...`` URL, or None if it is not one."""
    path = urllib.parse.urlsplit(art_url).path
    prefix = "/cover/"
    if not path.startswith(prefix):
        return None
    relative = path[len(prefix) :]
    if not relative:
        return None
    return public_root / "cover" / relative


def _atomic_write(target: Path, data: bytes) -> None:
    """Write ``data`` to ``target`` atomically and world-readable for nginx."""
    directory = target.parent
    fd, tmp_name = tempfile.mkstemp(dir=directory, prefix=".art.", suffix=".jpg")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
            os.fchmod(handle.fileno(), 0o644)
        os.replace(tmp_name, target)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


class ArtLookup:
    """Resolve and cache covers for tracks that arrive without one."""

    def __init__(
        self,
        cover_root: Path,
        fetch: Fetch,
        *,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
        max_files: int = _DEFAULT_MAX_FILES,
        miss_ttl_s: float = _DEFAULT_MISS_TTL,
    ) -> None:
        self._cover_root = Path(cover_root)
        self._fetch = fetch
        self._clock = clock
        self._sleep = sleep
        self._max_files = max_files
        self._miss_ttl_s = miss_ttl_s
        self._last_musicbrainz: float | None = None

    def find(
        self,
        title: str,
        artist: Optional[str],
        album: Optional[str],
        kind: str,
        genre: Optional[list[str]],
    ) -> Optional[str]:
        """A same-origin ``/cover/lookup/<sha1>.jpg`` URL for the track, or None.

        Answers from the disk cache with no network call when the image, or a
        fresh miss marker, already exists for this lookup, so a replayed track is
        not searched for again. A miss writes a ``.none`` marker and returns None.
        """
        chosen = route(kind, genre)
        if chosen == "music" and not artist and not album:
            # Music needs at least an album or an artist to search on; with
            # neither there is nothing to look up.
            return None

        key = _lookup_key(chosen, title, artist, album)
        image_path = self._lookup_dir() / f"{key}.jpg"
        miss_path = self._lookup_dir() / f"{key}{_NONE_SUFFIX}"

        if image_path.is_file():
            return _lookup_url(key)
        if self._miss_fresh(miss_path):
            return None

        raw = self._resolve(chosen, kind, title, artist, album)
        if raw is None:
            self._write_miss(miss_path)
            return None

        try:
            jpeg = downscale_cover(raw)
        except Exception:
            self._write_miss(miss_path)
            return None

        try:
            self._lookup_dir().mkdir(parents=True, exist_ok=True)
            _atomic_write(image_path, jpeg)
        except OSError:
            return None
        self._prune()
        return _lookup_url(key)

    # -- lookups ----------------------------------------------------------

    def _resolve(
        self,
        chosen: str,
        kind: str,
        title: str,
        artist: Optional[str],
        album: Optional[str],
    ) -> Optional[bytes]:
        if chosen == "podcast":
            image = self._podcast(artist, album)
            if image is None and kind == "long_form":
                # A long-form track with no genre: the podcast search found
                # nothing, so try it as an audiobook.
                image = self._open_library(artist, album)
            return image
        if chosen == "audiobook":
            return self._open_library(artist, album)
        return self._music(artist, album)

    def _podcast(self, artist: Optional[str], album: Optional[str]) -> Optional[bytes]:
        show = album or artist
        if not show:
            return None
        url = _ITUNES_SEARCH + "?" + urllib.parse.urlencode(
            {"media": "podcast", "entity": "podcast", "term": show}
        )
        results = _json_field(self._fetch(url), "results")
        for result in results:
            if not isinstance(result, dict):
                continue
            if _names_match(result.get("collectionName"), show):
                artwork = result.get("artworkUrl600")
                if isinstance(artwork, str):
                    return self._fetch(artwork)
        return None

    def _open_library(
        self, artist: Optional[str], album: Optional[str]
    ) -> Optional[bytes]:
        # The book title is the album; the author is the artist.
        if not album:
            return None
        params = {"title": album}
        if artist:
            params["author"] = artist
        url = _OPEN_LIBRARY_SEARCH + "?" + urllib.parse.urlencode(params)
        docs = _json_field(self._fetch(url), "docs")
        for doc in docs:
            if not isinstance(doc, dict):
                continue
            if _names_match(doc.get("title"), album):
                cover_id = doc.get("cover_i")
                if isinstance(cover_id, int):
                    return self._fetch(_OPEN_LIBRARY_COVER.format(cover_id=cover_id))
        return None

    def _music(self, artist: Optional[str], album: Optional[str]) -> Optional[bytes]:
        if not album:
            return None
        self._await_musicbrainz_gate()
        query = f'artist:"{artist}" AND release:"{album}"' if artist else f'release:"{album}"'
        url = _MUSICBRAINZ_SEARCH + "?" + urllib.parse.urlencode(
            {"query": query, "fmt": "json", "limit": 5}
        )
        try:
            releases = _json_field(self._fetch(url), "releases")
        finally:
            self._last_musicbrainz = self._clock()
        for release in releases:
            if not isinstance(release, dict):
                continue
            if not _names_match(release.get("title"), album):
                continue
            if not _artist_credit_matches(release.get("artist-credit"), artist):
                continue
            mbid = release.get("id")
            if isinstance(mbid, str):
                return self._fetch(_COVER_ART_ARCHIVE.format(mbid=mbid))
        return None

    def _await_musicbrainz_gate(self) -> None:
        """Wait, if needed, so MusicBrainz is called at most once per second."""
        if self._last_musicbrainz is None:
            return
        remaining = _MUSICBRAINZ_MIN_INTERVAL - (self._clock() - self._last_musicbrainz)
        if remaining > 0:
            self._sleep(remaining)

    # -- cache ------------------------------------------------------------

    def _lookup_dir(self) -> Path:
        return self._cover_root / "lookup"

    def _miss_fresh(self, miss_path: Path) -> bool:
        try:
            age = self._clock() - miss_path.stat().st_mtime
        except OSError:
            return False
        return age < self._miss_ttl_s

    def _write_miss(self, miss_path: Path) -> None:
        try:
            self._lookup_dir().mkdir(parents=True, exist_ok=True)
            _atomic_write(miss_path, b"")
        except OSError:
            pass
        else:
            self._prune()

    def _prune(self) -> None:
        """Keep only the ``max_files`` most recent files in the lookup dir.

        Covers and miss markers are content-addressed, so this is a slow-growth
        guard, not a hot path. It globs only ``lookup/``, so the AirPlay bridge's
        covers in ``cover/`` are never touched. Best-effort.
        """
        lookup_dir = self._lookup_dir()
        try:
            entries = [entry for entry in lookup_dir.iterdir() if entry.is_file()]
            entries.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        except OSError:
            return
        for stale in entries[self._max_files :]:
            try:
                stale.unlink()
            except OSError:
                pass


def _lookup_key(chosen: str, title: str, artist: Optional[str], album: Optional[str]) -> str:
    """A stable cache key for a lookup, hashing only what the search uses.

    The episode title never changes a podcast's or an audiobook's cover, so it is
    left out of their keys and every episode of a show shares one cached image.
    Music is keyed by artist and album, the only fields its search and match use.
    """
    if chosen == "podcast":
        parts = ["podcast", _normalize(album) or _normalize(artist)]
    elif chosen == "audiobook":
        parts = ["audiobook", _normalize(album), _normalize(artist)]
    else:
        parts = ["music", _normalize(artist), _normalize(album)]
    raw = "\x1f".join(parts)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _lookup_url(key: str) -> str:
    return f"/cover/lookup/{key}.jpg"


def _json_field(data: Optional[bytes], field: str) -> list:
    """The named array from a JSON body, or an empty list on any problem."""
    if not data:
        return []
    try:
        parsed = json.loads(data)
    except (ValueError, TypeError):
        return []
    value = parsed.get(field) if isinstance(parsed, dict) else None
    return value if isinstance(value, list) else []


def _artist_credit_matches(credit, artist: Optional[str]) -> bool:
    """Whether a MusicBrainz ``artist-credit`` names the searched artist.

    With no searched artist there is nothing to match against, so music with no
    artist never matches (its cover would be a guess). The credit is a list of
    parts; their joined names are compared as one name.
    """
    if not artist:
        return False
    if not isinstance(credit, list):
        return False
    names = "".join(part.get("name", "") for part in credit if isinstance(part, dict))
    return _names_match(names, artist)
