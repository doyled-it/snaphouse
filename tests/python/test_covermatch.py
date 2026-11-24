"""Tests for the shared cover matcher (pure, no I/O)."""

import pytest

from snaphouse.covermatch import (
    artist_names,
    artists_match,
    core_text,
    core_title,
    normalize,
    title_score,
)


def test_normalize_reads_ampersand_as_and():
    assert normalize("THE MOUSE & THE MASK") == normalize("The Mouse and the Mask")
    assert normalize("Beyoncé!") == "beyonce"


@pytest.mark.parametrize(
    "track, candidate, score",
    [
        ("THE MOUSE & THE MASK", "THE MOUSE AND THE MASK (METALFACE EDITION)", 1),
        ("THE MOUSE AND THE MASK (METALFACE EDITION)", "THE MOUSE & THE MASK", 1),
        ("NEHRUVIANDOOM (REDUX)", "NEHRUVIANDOOM (REDUX)", 2),
        ("NEHRUVIANDOOM (REDUX)", "NEHRUVIANDOOM", 1),
        ("Kaya (Remastered 2013)", "Kaya", 1),
        ("Catfish - Single", "Catfish", 1),
        ("Catfish", "Catfish - EP", 1),
        ("Special Herbs, Vol. 1 & 2", "Special Herbs, Vol. 7 And 8", 0),
        ("100% Electronica", "200% Electronica", 0),
        ("Night Harbor", "Night Harbor Live", 0),
    ],
)
def test_title_score(track, candidate, score):
    assert title_score(track, candidate) == score


def test_core_title_keeps_a_title_that_is_only_a_group():
    assert core_title("(Untitled)") == "untitled"


def test_artist_names_split_and_drop_the():
    assert artist_names("DANGERDOOM, MF DOOM & Danger Mouse") == {
        "dangerdoom",
        "mf doom",
        "danger mouse",
    }
    assert artist_names("The Weeknd feat. Drake") == {"weeknd", "drake"}


@pytest.mark.parametrize(
    "track, candidate, expected",
    [
        ("DANGERDOOM, MF DOOM & Danger Mouse", "MF DOOM", True),
        ("Bob Marley & The Wailers", "Bob Marley and the Wailers", True),
        ("Simon & Garfunkel", "Simon & Garfunkel", True),
        ("The Weeknd", "Goomp", False),
        ("Four Tops", "Various Artists", False),
        ("", "Four Tops", False),
        ("Four Tops", None, False),
    ],
)
def test_artists_match(track, candidate, expected):
    assert artists_match(track, candidate) is expected


def test_core_text_keeps_the_original_spelling_for_a_search_term():
    assert core_text("THE MOUSE AND THE MASK (METALFACE EDITION)") == "THE MOUSE AND THE MASK"
    assert core_text("Catfish - Single") == "Catfish"
    assert core_text("Kaya") == "Kaya"
