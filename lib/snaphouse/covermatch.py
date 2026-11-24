"""The shared matcher for cover lookups: pure functions, no I/O.

One set of rules serves both Apple and MusicBrainz candidates. A candidate
matches only when its album title and its artist both match; there is no numeric
similarity threshold, so a close spelling stays a miss (a wrong cover is worse
than none). The design is in
``docs/superpowers/specs/2026-11-23-cover-matching-design.md``.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Optional

# A trailing "(...)" or "[...]" group, or everything from the first " - ".
_TRAILING_GROUP = re.compile(r"\s*[\(\[][^\)\]]*[\)\]]\s*$")
_DASH_SUFFIX = re.compile(r"\s+-\s+.*$")
_ARTIST_SPLIT = re.compile(
    r",\s|\s&\s|\sand\s|\sx\s|\swith\s|\sfeat\.?\s|\sft\.?\s|\sfeaturing\s",
    re.IGNORECASE,
)


def normalize(value: Optional[str]) -> str:
    """A name reduced for comparison: case-folded, accents and punctuation gone.

    "&" reads as "and", so "THE MOUSE & THE MASK" equals "The Mouse and the Mask".
    """
    if not value:
        return ""
    decomposed = unicodedata.normalize("NFKD", value.replace("&", " and "))
    without_marks = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return re.sub(r"[^0-9a-z]+", " ", without_marks.casefold()).strip()


def core_text(value: str) -> str:
    """The title without trailing edition parts, in its original spelling.

    Drops trailing ``(...)`` and ``[...]`` groups and everything after the first
    `` - `` (which covers Apple's `` - Single`` and `` - EP``). Used as a search
    term, where the original spelling matters. A title that is nothing but a
    group comes back whole.
    """
    text = value
    while True:
        cut = _TRAILING_GROUP.sub("", text)
        if cut == text:
            break
        text = cut
    text = _DASH_SUFFIX.sub("", text).strip()
    return text if normalize(text) else value.strip()


def core_title(value: str) -> str:
    """The normalized form of :func:`core_text`, for comparison."""
    return normalize(core_text(value))


def title_score(track_title: Optional[str], candidate_title: Optional[str]) -> int:
    """2 for equal full titles, 1 for equal core titles, else 0."""
    if not track_title or not candidate_title:
        return 0
    if normalize(track_title) and normalize(track_title) == normalize(candidate_title):
        return 2
    if core_title(track_title) == core_title(candidate_title):
        return 1
    return 0


def artist_names(credit: Optional[str]) -> set[str]:
    """The normalized individual names in a credit, a leading "the" dropped."""
    names: set[str] = set()
    for part in _ARTIST_SPLIT.split(credit or ""):
        name = normalize(part)
        if name.startswith("the "):
            name = name[4:]
        if name:
            names.add(name)
    return names


def artists_match(
    track_credit: Optional[str], candidate_credit: Optional[str]
) -> bool:
    """Equal full credits, or at least one name in both. Empty never matches."""
    full = normalize(track_credit)
    if not full or not normalize(candidate_credit):
        return False
    if full == normalize(candidate_credit):
        return True
    return bool(artist_names(track_credit) & artist_names(candidate_credit))
