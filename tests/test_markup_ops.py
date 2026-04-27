"""Tests for markup span ops used by the in-browser re-highlighting flow."""

from __future__ import annotations

from debatabase.markup_ops import apply_op


def test_add_underline_inserts_span():
    spans = apply_op([], action="add", kind="underline", start=4, end=10)
    assert spans == [{"start": 4, "end": 10, "kind": "underline"}]


def test_add_highlight_implicitly_adds_underline():
    """Per highlight ⊆ underline rule, adding highlight also lays underline."""
    spans = apply_op([], action="add", kind="highlight", start=4, end=10)
    kinds = sorted(s["kind"] for s in spans)
    assert kinds == ["highlight", "underline"]
    for s in spans:
        assert s["start"] == 4 and s["end"] == 10


def test_overlapping_same_kind_merges():
    spans = apply_op([], action="add", kind="underline", start=0, end=5)
    spans = apply_op(spans, action="add", kind="underline", start=4, end=10)
    assert spans == [{"start": 0, "end": 10, "kind": "underline"}]


def test_adjacent_same_kind_merges():
    """A span ending at offset N is adjacent to one starting at N."""
    spans = [{"start": 0, "end": 5, "kind": "underline"}]
    spans = apply_op(spans, action="add", kind="underline", start=5, end=10)
    assert spans == [{"start": 0, "end": 10, "kind": "underline"}]


def test_clear_removes_overlap():
    spans = apply_op([], action="add", kind="underline", start=0, end=20)
    spans = apply_op(spans, action="clear", start=5, end=10)
    assert spans == [
        {"start": 0, "end": 5, "kind": "underline"},
        {"start": 10, "end": 20, "kind": "underline"},
    ]


def test_clear_removes_highlight_and_underline_in_range():
    spans = apply_op([], action="add", kind="highlight", start=0, end=20)
    # Now spans contain both an underline 0–20 and a highlight 0–20
    spans = apply_op(spans, action="clear", start=8, end=12)
    # Both kinds are split: each becomes (0,8) and (12,20).
    assert sorted((s["start"], s["end"], s["kind"]) for s in spans) == [
        (0, 8, "highlight"),
        (0, 8, "underline"),
        (12, 20, "highlight"),
        (12, 20, "underline"),
    ]


def test_clear_outside_any_span_is_noop():
    spans = [{"start": 10, "end": 20, "kind": "underline"}]
    out = apply_op(spans, action="clear", start=0, end=5)
    assert out == spans


def test_zero_length_op_is_noop():
    spans = [{"start": 0, "end": 5, "kind": "underline"}]
    out = apply_op(spans, action="add", kind="underline", start=10, end=10)
    assert out == spans


def test_normalization_is_stable():
    """Applying the same add twice must equal applying it once."""
    once = apply_op([], action="add", kind="highlight", start=4, end=12)
    twice = apply_op(once, action="add", kind="highlight", start=4, end=12)
    assert once == twice
