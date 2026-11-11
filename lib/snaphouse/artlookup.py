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
import logging
import os
import re
import tempfile
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable, NamedTuple, Optional

from .airplay_meta import downscale_cover


class LookupResult(NamedTuple):
    """What a lookup resolved: a cover URL, and a podcast id when it has one.

    ``art_url`` is the same-origin ``/cover/lookup/<sha1>.jpg`` path for the
    track, or ``None`` on a miss or failure. ``podcast_id`` is the matched show's
    iTunes ``collectionId`` (a positive integer) when the podcast route matched a
    show, else ``None``. The id rides along with a found podcast cover and is
    cached beside it, so a cached hit returns it with no network call.
    """

    art_url: Optional[str]
    podcast_id: Optional[int]


class _Resolved(NamedTuple):
    """An internal lookup result: outcome, cover bytes, and an optional id.

    ``podcast_id`` is set only on the podcast route, and only when a matched show
    carries a valid ``collectionId``; every other route leaves it ``None``.
    """

    outcome: str
    image: Optional[bytes]
    podcast_id: Optional[int] = None

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

# A feed that carries a show, book, or release under a decorated name ("<Show> -
# Patreon Feed") hides the public title before one of these separators. When the
# full name finds no exact match, a lookup retries on the part before the first
# separator that appears.
_BASE_SEPARATORS = (" - ", " | ", ": ", " (")
_MIN_BASE_LENGTH = 3

# A music cover is first searched for under the full track credit; only when that
# finds nothing is a lead artist derived and searched for as a fallback. The lead
# is the part before the first of these separators: list separators (", ", " & ")
# and unambiguous featuring markers. " and ", " with " and " x " are deliberately
# left out, because they are part of real band names ("Florence and the Machine",
# "Dance with the Dead", "Lil Nas X") far more often than they introduce a guest.
# Matched case-insensitively, on the original string so the cut index is exact.
_LEAD_SEPARATORS = (
    ", ",
    " & ",
    " feat. ",
    " feat ",
    " ft. ",
    " featuring ",
)
_LEAD_SEPARATOR_RE = re.compile(
    "|".join(re.escape(separator) for separator in _LEAD_SEPARATORS),
    re.IGNORECASE,
)

_NONE_SUFFIX = ".none"
# A podcast cover's iTunes collectionId is cached in a sidecar next to the cover,
# so a cached hit returns the id without a network call. A ``.noid`` marker beside
# a cover records that an id-only back-fill search ran and found none, so a cover
# with no real id is searched for at most once rather than on every play.
_ID_SUFFIX = ".id"
_NOID_SUFFIX = ".noid"
# Both ride alongside a cover and are keyed to it: excluded from the file-cap
# count and removed with the cover they belong to.
_SIDECAR_SUFFIXES = (_ID_SUFFIX, _NOID_SUFFIX)
_DEFAULT_MISS_TTL = 7 * 86400
_DEFAULT_MAX_FILES = 300

# A lookup resolves to one of three outcomes. FOUND carries the cover bytes. MISS
# means the search ran, parsed, and matched nothing: a real miss, cached so the
# track is not searched for on every play. ERROR means a fetch failed (a bad
# status, a timeout, a transport error, or an image fetch that came back empty):
# transient, never cached, so a Wi-Fi blip does not cost a week of missing art.
_FOUND = "found"
_MISS = "miss"
_ERROR = "error"

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


def _base_name(name: str) -> Optional[str]:
    """The title before the first suffix separator, or None when there is none.

    Cuts at the earliest occurrence of any separator in ``_BASE_SEPARATORS`` and
    trims the part before it. Returns None when no separator appears, the base is
    shorter than ``_MIN_BASE_LENGTH``, or the base equals the full name, so a
    plain or barely-there name yields nothing extra to search for.
    """
    cut = None
    for separator in _BASE_SEPARATORS:
        index = name.find(separator)
        if index != -1 and (cut is None or index < cut):
            cut = index
    if cut is None:
        return None
    base = name[:cut].strip()
    if len(base) < _MIN_BASE_LENGTH or base == name:
        return None
    return base


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
        log: Optional[logging.Logger] = None,
    ) -> None:
        self._cover_root = Path(cover_root)
        self._fetch = fetch
        self._clock = clock
        self._sleep = sleep
        self._max_files = max_files
        self._miss_ttl_s = miss_ttl_s
        self._log = log if log is not None else logging.getLogger("snaphouse.artlookup")
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

        The cover-only convenience over :meth:`find_with_id`; callers that also
        want the matched podcast id call that instead. Answers from the disk
        cache with no network call when the image, or a fresh miss marker,
        already exists for this lookup.
        """
        return self.find_with_id(title, artist, album, kind, genre).art_url

    def find_with_id(
        self,
        title: str,
        artist: Optional[str],
        album: Optional[str],
        kind: str,
        genre: Optional[list[str]],
    ) -> LookupResult:
        """The cover URL and, for a matched podcast, its iTunes collectionId.

        Answers from the disk cache with no network call when the image, or a
        fresh miss marker, already exists for this lookup, so a replayed track is
        not searched for again; a cached podcast cover returns its id from the
        sidecar beside it. A podcast cover cached before ids existed (no sidecar)
        is back-filled once with a single id-only search, no image download. A
        miss writes a ``.none`` marker and returns no cover. One INFO line names
        the lookup and its outcome (cover found, miss, or failure), carrying only
        the show or album name.
        """
        chosen = route(kind, genre)
        url, outcome, podcast_id = self._find(chosen, kind, title, artist, album)
        name = album or artist or title
        self._log.info("art lookup for %r (%s): %s", name, chosen, outcome)
        return LookupResult(url, podcast_id)

    def _find(
        self,
        chosen: str,
        kind: str,
        title: str,
        artist: Optional[str],
        album: Optional[str],
    ) -> tuple[Optional[str], str, Optional[int]]:
        """Resolve the cover URL, a one-word outcome label, and a podcast id."""
        if chosen == "music":
            return self._find_music(title, artist, album)

        key = _lookup_key(chosen, title, artist, album)
        image_path = self._lookup_dir() / f"{key}.jpg"
        miss_path = self._lookup_dir() / f"{key}{_NONE_SUFFIX}"
        id_path = self._lookup_dir() / f"{key}{_ID_SUFFIX}"
        noid_path = self._lookup_dir() / f"{key}{_NOID_SUFFIX}"

        if image_path.is_file():
            podcast_id = self._read_podcast_id(id_path)
            if (
                podcast_id is None
                and chosen == "podcast"
                and not id_path.exists()
                and not noid_path.exists()
            ):
                # A cover cached before podcast ids existed: back-fill it once.
                podcast_id = self._backfill_podcast_id(
                    id_path, noid_path, artist, album
                )
            return (_lookup_url(key), "cover found", podcast_id)
        if self._miss_fresh(miss_path):
            return (None, "miss", None)

        resolved = self._resolve(chosen, kind, title, artist, album)
        if resolved.outcome == _ERROR:
            # A fetch failed; transient, so leave no marker and retry next play.
            return (None, "failure", None)
        if resolved.image is None:
            # A real miss: the search ran and matched nothing.
            self._write_miss(miss_path)
            return (None, "miss", None)

        try:
            jpeg = downscale_cover(resolved.image)
        except Exception:
            self._write_miss(miss_path)
            return (None, "miss", None)

        try:
            self._lookup_dir().mkdir(parents=True, exist_ok=True)
            _atomic_write(image_path, jpeg)
        except OSError:
            return (None, "failure", None)
        podcast_id = _valid_podcast_id(resolved.podcast_id)
        if podcast_id is not None:
            # Best-effort: a sidecar we fail to write just means the next cached
            # hit returns the cover without an id, so swallow a write error.
            try:
                _atomic_write(id_path, str(podcast_id).encode("ascii"))
            except OSError:
                pass
        self._prune()
        return (_lookup_url(key), "cover found", podcast_id)

    def _find_music(
        self, title: str, artist: Optional[str], album: Optional[str]
    ) -> tuple[Optional[str], str, Optional[int]]:
        """Resolve a music cover in two steps, full credit first, then lead.

        The full track credit is searched for and cached under its own key, so a
        band whose name carries ", " or " & " ("Simon & Garfunkel", "Earth, Wind
        & Fire") is found and cached exactly as it was before featured artists
        were handled. Only when the full credit finds nothing is a lead artist
        derived and searched for, accepting a release whose artist credit equals
        the lead exactly; that cover is cached under the lead key and consulted
        before any network call for a later track with the same album and lead, so
        a whole album of featured tracks shares one cover after one fallback
        lookup. Each step keeps its own miss marker, so neither suppresses the
        other.
        """
        if not album:
            # Music needs an album to search a release on; a track with neither an
            # album nor the means to find one has nothing to look up.
            return (None, "miss", None)

        full_key = _lookup_key("music", title, artist, album)
        full_image = self._lookup_dir() / f"{full_key}.jpg"
        full_miss = self._lookup_dir() / f"{full_key}{_NONE_SUFFIX}"

        lead = _lead_artist(artist)
        lead_key = _lookup_key("music", title, lead, album) if lead else None
        lead_image = (
            self._lookup_dir() / f"{lead_key}.jpg" if lead_key is not None else None
        )
        lead_miss = (
            self._lookup_dir() / f"{lead_key}{_NONE_SUFFIX}"
            if lead_key is not None
            else None
        )

        # Cached cover: the track's own full-credit cover, or the album's shared
        # fallback cover under the lead. Both are checked before any network call.
        if full_image.is_file():
            return (_lookup_url(full_key), "cover found", None)
        if lead_image is not None and lead_image.is_file():
            return (_lookup_url(lead_key), "cover found", None)

        # Step 1: the full track credit. Skip the search when a fresh miss marker
        # already records that this exact credit found nothing.
        if not self._miss_fresh(full_miss):
            resolved = self._music(artist, album)
            if resolved.outcome == _ERROR:
                return (None, "failure", None)
            if resolved.image is not None:
                return self._store_cover(full_key, full_image, full_miss, resolved.image)
            self._write_miss(full_miss)

        # Step 2: fall back to the lead, only when the credit named extra artists.
        if lead_key is None:
            return (None, "miss", None)
        if self._miss_fresh(lead_miss):
            return (None, "miss", None)
        resolved = self._music(lead, album)
        if resolved.outcome == _ERROR:
            return (None, "failure", None)
        if resolved.image is not None:
            return self._store_cover(lead_key, lead_image, lead_miss, resolved.image)
        self._write_miss(lead_miss)
        return (None, "miss", None)

    def _store_cover(
        self, key: str, image_path: Path, miss_path: Path, raw: bytes
    ) -> tuple[Optional[str], str, Optional[int]]:
        """Downscale and cache a found cover, returning its ``_find`` triple.

        A cover that will not decode is treated as a real miss (the marker is
        written), a disk error is a transient failure (no marker), and a stored
        cover prunes the lookup directory and returns its same-origin URL.
        """
        try:
            jpeg = downscale_cover(raw)
        except Exception:
            self._write_miss(miss_path)
            return (None, "miss", None)
        try:
            self._lookup_dir().mkdir(parents=True, exist_ok=True)
            _atomic_write(image_path, jpeg)
        except OSError:
            return (None, "failure", None)
        self._prune()
        return (_lookup_url(key), "cover found", None)

    def _read_podcast_id(self, id_path: Path) -> Optional[int]:
        """The cached podcast id beside a cover, or None when there is none.

        A missing sidecar, an unreadable one, or a body that is not a positive
        integer all resolve to None, so a cover with no (or a corrupt) id simply
        returns without one.
        """
        try:
            text = id_path.read_text(encoding="ascii")
        except OSError:
            return None
        try:
            value = int(text.strip())
        except ValueError:
            return None
        return _valid_podcast_id(value)

    def _backfill_podcast_id(
        self,
        id_path: Path,
        noid_path: Path,
        artist: Optional[str],
        album: Optional[str],
    ) -> Optional[int]:
        """Resolve a legacy cached podcast cover's id with one id-only search.

        A cover cached before this change has no ``.id`` sidecar, and the cached
        path returns before any network code, so every episode of a show the
        owner already follows would resolve without a podcast id until the cover
        is evicted. The first time such a cover is served, run a single iTunes
        search for the id alone (no image download, since the cover is already on
        disk), writing the ``.id`` sidecar on an exact match. On a parsed miss (no
        match, or a match with no usable id) write a ``.noid`` marker so the
        search never repeats for this cover. A transient fetch failure writes no
        marker and returns None, so a later play retries. iTunes podcast search is
        not rate-gated, and the marker caps it at one search per cover.
        """
        resolved = self._podcast_id(artist, album)
        if resolved.outcome == _ERROR:
            return None
        podcast_id = _valid_podcast_id(resolved.podcast_id)
        if resolved.outcome == _FOUND and podcast_id is not None:
            try:
                _atomic_write(id_path, str(podcast_id).encode("ascii"))
            except OSError:
                # A sidecar we fail to write just means the next play retries the
                # search; do not write the negative marker in that case.
                return podcast_id
            return podcast_id
        self._write_marker(noid_path)
        return None

    # -- lookups ----------------------------------------------------------

    def _resolve(
        self,
        chosen: str,
        kind: str,
        title: str,
        artist: Optional[str],
        album: Optional[str],
    ) -> _Resolved:
        """Resolve a cover, returning an ``_Resolved``.

        ``outcome`` is ``_FOUND`` (``image`` holds the bytes), ``_MISS`` (a real
        miss the caller should cache), or ``_ERROR`` (a transient fetch failure
        the caller must not cache). ``podcast_id`` is set only when the podcast
        route matched a show that carried one.
        """
        if chosen == "podcast":
            found = self._podcast(artist, album)
            if found.outcome == _FOUND:
                return found
            if kind == "long_form":
                # A long-form track with no genre: the podcast search found
                # nothing, so try it as an audiobook. A blip on either leg stays
                # a transient error so the miss is not cached over an outage. An
                # audiobook cover carries no podcast id.
                fallback = self._open_library(artist, album)
                if fallback.outcome == _FOUND:
                    return fallback
                if _ERROR in (found.outcome, fallback.outcome):
                    return _Resolved(_ERROR, None)
                return _Resolved(_MISS, None)
            return _Resolved(found.outcome, None)
        # Music takes its own two-step path in _find_music and never reaches here.
        return self._open_library(artist, album)

    def _with_base(
        self,
        name: str,
        search: Callable[[str], _Resolved],
    ) -> _Resolved:
        """Search the full name, then the base name before its first suffix.

        ``search`` runs one lookup for a given name and returns its outcome. The
        full name is tried first; only on a parsed miss, and only when a base
        name exists, is a second search run on that base. A fetch error on either
        attempt returns ``_ERROR`` at once, so the miss marker is written only
        when both attempts parse and miss.
        """
        result = search(name)
        if result.outcome != _MISS:
            return result
        base = _base_name(name)
        if base is None:
            return _Resolved(_MISS, None)
        return search(base)

    def _podcast(
        self, artist: Optional[str], album: Optional[str]
    ) -> _Resolved:
        show = album or artist
        if not show:
            return _Resolved(_MISS, None)
        return self._with_base(show, self._podcast_search)

    def _podcast_search(self, show: str) -> _Resolved:
        url = _ITUNES_SEARCH + "?" + urllib.parse.urlencode(
            {"media": "podcast", "entity": "podcast", "term": show}
        )
        results = _search_list(self._fetch(url), "results")
        if results is None:
            return _Resolved(_ERROR, None)
        for result in results:
            if not isinstance(result, dict):
                continue
            if _names_match(result.get("collectionName"), show):
                artwork = result.get("artworkUrl600")
                if isinstance(artwork, str):
                    image = self._fetch(artwork)
                    if image is None:
                        return _Resolved(_ERROR, None)
                    # The id is validated here and again on the way out of
                    # _find, so a non-integer or non-positive value never
                    # reaches the cache or the published shape.
                    podcast_id = _valid_podcast_id(result.get("collectionId"))
                    return _Resolved(_FOUND, image, podcast_id)
        return _Resolved(_MISS, None)

    def _podcast_id(
        self, artist: Optional[str], album: Optional[str]
    ) -> _Resolved:
        """An id-only podcast search (the show name, then its base), no image."""
        show = album or artist
        if not show:
            return _Resolved(_MISS, None)
        return self._with_base(show, self._podcast_id_search)

    def _podcast_id_search(self, show: str) -> _Resolved:
        """Search the iTunes podcast directory for a show's collectionId alone.

        ``_FOUND`` carries the id in ``podcast_id`` (``image`` stays None, since
        the cover is already cached); ``_MISS`` is a parsed search that matched no
        show, or matched one with no usable id; ``_ERROR`` is a failed or
        unparseable fetch. It downloads no artwork.
        """
        url = _ITUNES_SEARCH + "?" + urllib.parse.urlencode(
            {"media": "podcast", "entity": "podcast", "term": show}
        )
        results = _search_list(self._fetch(url), "results")
        if results is None:
            return _Resolved(_ERROR, None)
        for result in results:
            if not isinstance(result, dict):
                continue
            if _names_match(result.get("collectionName"), show):
                podcast_id = _valid_podcast_id(result.get("collectionId"))
                if podcast_id is not None:
                    return _Resolved(_FOUND, None, podcast_id)
                # The show matched but carries no usable id: a real miss.
                return _Resolved(_MISS, None)
        return _Resolved(_MISS, None)

    def _open_library(
        self, artist: Optional[str], album: Optional[str]
    ) -> _Resolved:
        # The book title is the album; the author is the artist.
        if not album:
            return _Resolved(_MISS, None)
        return self._with_base(
            album, lambda title: self._open_library_search(artist, title)
        )

    def _open_library_search(
        self, artist: Optional[str], album: str
    ) -> _Resolved:
        params = {"title": album}
        if artist:
            params["author"] = artist
        url = _OPEN_LIBRARY_SEARCH + "?" + urllib.parse.urlencode(params)
        docs = _search_list(self._fetch(url), "docs")
        if docs is None:
            return _Resolved(_ERROR, None)
        for doc in docs:
            if not isinstance(doc, dict):
                continue
            if _names_match(doc.get("title"), album):
                cover_id = doc.get("cover_i")
                if isinstance(cover_id, int):
                    image = self._fetch(_OPEN_LIBRARY_COVER.format(cover_id=cover_id))
                    if image is None:
                        return _Resolved(_ERROR, None)
                    return _Resolved(_FOUND, image)
        return _Resolved(_MISS, None)

    def _music(
        self, name: Optional[str], album: Optional[str]
    ) -> _Resolved:
        """Search MusicBrainz for a release ``album`` credited exactly to ``name``.

        ``name`` is the full track credit on the primary lookup and the derived
        lead artist on the fallback. The full album title is searched first, then
        its base before a decorated suffix, so a release named "<Album> (Deluxe)"
        is still found.
        """
        if not album:
            return _Resolved(_MISS, None)
        return self._with_base(
            album, lambda release: self._music_search(name, release)
        )

    def _music_search(
        self, name: Optional[str], album: str
    ) -> _Resolved:
        self._await_musicbrainz_gate()
        if name:
            query = (
                f'artist:"{_lucene_phrase(name)}"'
                f' AND release:"{_lucene_phrase(album)}"'
            )
        else:
            query = f'release:"{_lucene_phrase(album)}"'
        url = _MUSICBRAINZ_SEARCH + "?" + urllib.parse.urlencode(
            {"query": query, "fmt": "json", "limit": 5}
        )
        try:
            releases = _search_list(self._fetch(url), "releases")
        finally:
            self._last_musicbrainz = self._clock()
        if releases is None:
            return _Resolved(_ERROR, None)
        for release in releases:
            if not isinstance(release, dict):
                continue
            if not _names_match(release.get("title"), album):
                continue
            if not _credit_equals(release.get("artist-credit"), name):
                continue
            mbid = release.get("id")
            if isinstance(mbid, str):
                image = self._fetch(_COVER_ART_ARCHIVE.format(mbid=mbid))
                if image is None:
                    return _Resolved(_ERROR, None)
                return _Resolved(_FOUND, image)
        return _Resolved(_MISS, None)

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

    def _write_marker(self, marker_path: Path) -> None:
        """Write an empty marker (a ``.noid``) beside a cover, best-effort.

        Unlike ``_write_miss`` this does not prune: the marker rides an existing
        cover and is excluded from the file cap, so it adds no primary entry.
        """
        try:
            self._lookup_dir().mkdir(parents=True, exist_ok=True)
            _atomic_write(marker_path, b"")
        except OSError:
            pass

    def _prune(self) -> None:
        """Keep only the ``max_files`` most recent entries in the lookup dir.

        Covers and miss markers are content-addressed, so this is a slow-growth
        guard, not a hot path. It globs only ``lookup/``, so the AirPlay bridge's
        covers in ``cover/`` are never touched. A podcast cover's ``.id`` and
        ``.noid`` sidecars do not count toward the limit and are removed with the
        cover, so neither outlives, nor evicts, the cover it belongs to.
        Best-effort.
        """
        lookup_dir = self._lookup_dir()
        try:
            entries = [
                entry
                for entry in lookup_dir.iterdir()
                if entry.is_file() and entry.suffix not in _SIDECAR_SUFFIXES
            ]
            entries.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        except OSError:
            return
        for stale in entries[self._max_files :]:
            try:
                stale.unlink()
            except OSError:
                pass
            for suffix in _SIDECAR_SUFFIXES:
                try:
                    stale.with_suffix(suffix).unlink()
                except OSError:
                    pass


def _lookup_key(chosen: str, title: str, artist: Optional[str], album: Optional[str]) -> str:
    """A stable cache key for a lookup, hashing only what the search uses.

    The episode title never changes a podcast's or an audiobook's cover, so it is
    left out of their keys and every episode of a show shares one cached image.
    Music is keyed by the artist and album, where the artist is the full track
    credit on the primary lookup and the derived lead artist on the fallback, so a
    featured track's fallback cover is shared across the album under the lead key.
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


def _valid_podcast_id(value: object) -> Optional[int]:
    """A positive integer podcast id, or None for anything else.

    ``bool`` is rejected even though it is an ``int`` subclass, so a stray
    ``True`` never becomes the id ``1``. Everything that is not a positive,
    non-boolean integer resolves to None, so no unvalidated id ever reaches the
    cache or the published now-playing and play-history shapes.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value > 0:
        return value
    return None


def _lucene_phrase(value: str) -> str:
    """Escape a value for use inside a quoted Lucene phrase.

    Inside double quotes Lucene treats only the backslash and the double quote
    specially, so both are backslash-escaped (the backslash first, or it would
    double-escape the quote's escape). This keeps a name with a quote from
    breaking out of the phrase or corrupting the MusicBrainz query.
    """
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _search_list(data: Optional[bytes], field: str) -> Optional[list]:
    """The named array from a search body, or ``None`` when the body is unusable.

    ``None`` means the fetch failed (no body) or the body did not parse as a JSON
    object: a transient condition the caller treats as an error, never a cached
    miss. A present-but-empty list, or a body that parsed without the field, is a
    search that ran and matched nothing, returned as an empty list (a real miss).
    """
    if data is None:
        return None
    try:
        parsed = json.loads(data)
    except (ValueError, TypeError):
        return None
    if not isinstance(parsed, dict):
        return None
    value = parsed.get(field)
    return value if isinstance(value, list) else []


def _lead_artist(credit: Optional[str]) -> Optional[str]:
    """The lead artist cut from a track credit, or None when the credit is one name.

    Cuts at the earliest whole-token separator in ``_LEAD_SEPARATORS`` (matched
    case-insensitively on the original string, so the cut index is exact even when
    case-folding would change length) and trims the part before it. Returns None
    when no separator appears or the lead equals the whole credit, so a single
    artist, or a band whose own name is tried first and matched in full, needs no
    fallback.
    """
    if not credit:
        return None
    match = _LEAD_SEPARATOR_RE.search(credit)
    if match is None:
        return None
    lead = credit[: match.start()].strip()
    if not lead or lead == credit.strip():
        return None
    return lead


def _credit_equals(credit, name: Optional[str]) -> bool:
    """Whether a MusicBrainz ``artist-credit`` equals ``name`` exactly.

    With no name there is nothing to match against, so a release never matches
    (its cover would be a guess). The credit is a list of parts; their joined
    names are compared as one name, so a release stands only when its whole artist
    credit is the searched name, never when it merely contains it.
    """
    if not name:
        return False
    if not isinstance(credit, list):
        return False
    names = "".join(part.get("name", "") for part in credit if isinstance(part, dict))
    return _names_match(names, name)
