"""Tests for markup-span → HTML rendering (web/render.py).

render.py decides what every card view shows — all four modes plus the
search snippet — and is pure stdlib, so it's cheap to pin down exactly.
"""

from __future__ import annotations

from debatabase.web.render import render_card, snippet

TEXT = "Hegemony is resilient and decline is slow."
#       0123456789...
UL = {"start": 0, "end": 21, "kind": "underline"}     # "Hegemony is resilient"
HL = {"start": 0, "end": 8, "kind": "highlight"}      # "Hegemony"


# ---------------------------------------------------------------------------
# full mode
# ---------------------------------------------------------------------------

def test_full_no_markup_is_escaped_plain_text():
    assert render_card("a < b & c", [], "full") == "a &lt; b &amp; c"


def test_full_wraps_highlight_and_underline():
    html = render_card(TEXT, [UL, HL], "full")
    # Highlighted prefix renders as <mark>, the underlined remainder as <u>.
    assert '<mark class="hl">Hegemony</mark>' in html
    assert '<u class="ul"> is resilient</u>' in html
    # Unmarked tail is present, unwrapped.
    assert html.endswith(" and decline is slow.")


def test_full_highlight_implies_no_nested_u():
    """A highlighted chunk renders as <mark> only — highlight implies
    underline per the domain rule, so no nested <u> is emitted."""
    html = render_card(TEXT, [UL, HL], "full")
    assert "<u" not in html.split("</mark>")[0]


# ---------------------------------------------------------------------------
# highlight-only mode
# ---------------------------------------------------------------------------

def test_highlight_only_shows_only_highlights():
    html = render_card(TEXT, [UL, HL], "highlight-only")
    assert html == '<mark class="hl">Hegemony</mark>'


def test_highlight_only_joins_spans_with_ellipsis():
    markup = [
        {"start": 0, "end": 8, "kind": "highlight"},
        {"start": 25, "end": 32, "kind": "highlight"},
    ]
    html = render_card(TEXT, markup, "highlight-only")
    assert '<span class="ellipsis">…</span>' in html
    assert html.count("<mark") == 2


def test_highlight_only_no_highlights_is_empty():
    assert render_card(TEXT, [UL], "highlight-only") == ""


# ---------------------------------------------------------------------------
# underline-only mode
# ---------------------------------------------------------------------------

def test_underline_only_shows_underlined_with_inner_highlight():
    html = render_card(TEXT, [UL, HL], "underline-only")
    # Only the underlined chunk appears...
    assert "decline" not in html
    # ...and the highlighted subsection inside it still reads as <mark>.
    assert '<mark class="hl">Hegemony</mark>' in html
    assert " is resilient" in html


# ---------------------------------------------------------------------------
# plain mode
# ---------------------------------------------------------------------------

def test_plain_strips_all_markup():
    assert render_card(TEXT, [UL, HL], "plain") == TEXT


def test_plain_escapes_html():
    assert render_card("<script>", [], "plain") == "&lt;script&gt;"


# ---------------------------------------------------------------------------
# snippet
# ---------------------------------------------------------------------------

def test_snippet_no_query_truncates():
    long_text = "word " * 100
    out = snippet(long_text, None, length=40)
    assert out.endswith("…")
    assert len(out) <= 45


def test_snippet_highlights_match_case_insensitively():
    out = snippet("The Heg debate is old.", "heg")
    assert '<mark class="match">Heg</mark>' in out


def test_snippet_query_miss_falls_back_to_prefix():
    out = snippet("alpha beta gamma", "zeta", length=10)
    assert out.startswith("alpha")
    assert "<mark" not in out


def test_snippet_escapes_html_around_match():
    out = snippet("x <b>bold</b> heg y", "heg")
    assert "<b>" not in out
    assert "&lt;b&gt;" in out
