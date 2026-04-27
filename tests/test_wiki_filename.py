"""Tests for opencaselist filename parsing."""

from __future__ import annotations

import pytest

from debatabase.wiki_filename import WikiMeta, parse_wiki_filename


@pytest.mark.parametrize(
    "filename, expected",
    [
        # The dominant pattern: triple-hyphen separator after sort prefix.
        (
            "MontgomeryBell-HoLi-Aff-10---TOC-Round-1.docx",
            WikiMeta("MontgomeryBell", "HoLi", "Aff", "TOC", "Round 1", "10"),
        ),
        (
            "Damien-NgKa-Neg-13---TOC-Round-3.docx",
            WikiMeta("Damien", "NgKa", "Neg", "TOC", "Round 3", "13"),
        ),
        # Period-then-hyphen variant (12.-).
        (
            "MontgomeryBell-FuRo-Aff-12.-Tournament-of-Champions-Round-1.docx",
            WikiMeta(
                "MontgomeryBell", "FuRo", "Aff",
                "Tournament of Champions", "Round 1", "12",
            ),
        ),
        # Double-hyphen separator.
        (
            "DallasHighlandPark-CoBr-Neg-14--The-Last-Dance-Round-7.docx",
            WikiMeta(
                "DallasHighlandPark", "CoBr", "Neg",
                "The Last Dance", "Round 7", "14",
            ),
        ),
        # Single-hyphen separator after a single-digit sort prefix.
        (
            "Jenks-KoBa-Aff-3-East-Oklahoma-District-Tournament-Round-1.docx",
            WikiMeta(
                "Jenks", "KoBa", "Aff",
                "East Oklahoma District Tournament", "Round 1", "3",
            ),
        ),
        # No sort prefix at all.
        (
            "Westwood-AnGu-Neg-Tournament-of-Champions-Round-2.docx",
            WikiMeta(
                "Westwood", "AnGu", "Neg",
                "Tournament of Champions", "Round 2", None,
            ),
        ),
        # Named round (Finals, not "Round N").
        (
            "GreenhillSchool-AlMo-Neg-08---TFA-State-Finals.docx",
            WikiMeta(
                "GreenhillSchool", "AlMo", "Neg",
                "TFA State", "Finals", "08",
            ),
        ),
        # "00---" / "0---" early-season sort prefixes preserved.
        (
            "GlenbrookNorth-CaRo-Neg-0---TOC-Round-5.docx",
            WikiMeta("GlenbrookNorth", "CaRo", "Neg", "TOC", "Round 5", "0"),
        ),
        # Tournament name with non-alpha character (DSDS-#3).
        (
            "LittleRockCentral-KaGa-Neg-11---DSDS-#3-Round-5.docx",
            WikiMeta(
                "LittleRockCentral", "KaGa", "Neg",
                "DSDS #3", "Round 5", "11",
            ),
        ),
        # Short school name (3-letter ADL).
        (
            "ADL-ChWa-Neg-3---Cal-Invitational-UC-Berkeley-Round-2.docx",
            WikiMeta(
                "ADL", "ChWa", "Neg",
                "Cal Invitational UC Berkeley", "Round 2", "3",
            ),
        ),
        # Lowercase tournament variant.
        (
            "GlenbrookNorth-LePa-Neg-tournament-of-champions-Round-4.docx",
            WikiMeta(
                "GlenbrookNorth", "LePa", "Neg",
                "tournament of champions", "Round 4", None,
            ),
        ),
        # Semis after a long tournament name.
        (
            "NewTrier-YaHe-Aff-07---IDCA-Varsity-State-Tournament-Semis.docx",
            WikiMeta(
                "NewTrier", "YaHe", "Aff",
                "IDCA Varsity State Tournament", "Semis", "07",
            ),
        ),
    ],
)
def test_parse_wiki_filename_known_variants(filename, expected):
    assert parse_wiki_filename(filename) == expected


def test_parse_wiki_filename_rejects_unrecognized():
    # Side must be Aff or Neg.
    assert parse_wiki_filename("School-Team-Other-something.docx") is None
    # Need at least the four leading dash-separated fields.
    assert parse_wiki_filename("not-a-wiki-doc.docx") is None
    assert parse_wiki_filename("nope.docx") is None
