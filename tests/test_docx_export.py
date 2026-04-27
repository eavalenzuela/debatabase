"""Round-trip tests for the .docx export.

Strategy: build a workspace with known markup spans, export to bytes, write
to a temp file, re-extract with parser/extract.py, then reconstruct spans
from runs and assert they match the originals at character-offset
granularity.

Markup colors / underline styles intentionally don't round-trip — the DB
stores ``kind`` only, so all underlines export as ``single`` and all
highlights as ``yellow``. The test asserts on offset ranges, not styles.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from debatabase.docx_export import (
    ExportAnalytical,
    ExportCard,
    ExportEntry,
    render_workspace_to_docx,
)
from debatabase.parser.extract import Paragraph, extract_docx


def _write_temp(blob: bytes) -> Path:
    f = tempfile.NamedTemporaryFile(suffix=".docx", delete=False)
    f.write(blob)
    f.close()
    return Path(f.name)


def _find_paragraph_by_text(paragraphs: list[Paragraph], text: str) -> Paragraph:
    for p in paragraphs:
        if p.text == text:
            return p
    raise AssertionError(f"no paragraph with exact text {text!r}")


def _merge_adjacent(segments: list[tuple[int, int]]) -> list[tuple[int, int]]:
    if not segments:
        return []
    segments = sorted(segments)
    merged = [segments[0]]
    for s, e in segments[1:]:
        if s == merged[-1][1]:
            merged[-1] = (merged[-1][0], e)
        else:
            merged.append((s, e))
    return merged


def _reconstruct_spans(paragraph: Paragraph) -> tuple[list[tuple[int, int]], list[tuple[int, int]]]:
    """Walk runs and return (underline_ranges, highlight_ranges) merged."""
    offset = 0
    underline: list[tuple[int, int]] = []
    highlight: list[tuple[int, int]] = []
    for run in paragraph.runs:
        run_len = len(run.text)
        if run_len == 0:
            continue
        if run.underline and run.underline != "none":
            underline.append((offset, offset + run_len))
        if run.highlight:
            highlight.append((offset, offset + run_len))
        offset += run_len
    return _merge_adjacent(underline), _merge_adjacent(highlight)


def test_round_trip_card_with_nested_highlight():
    text = "The quick brown fox jumps over the lazy dog."
    # Underlined: "quick brown fox jumps over" [4,30)
    # Highlighted (subset of above): "brown fox" [10,19)
    # Underlined-only (no highlight): "the lazy" [31,39)
    markup = [
        {"start": 4, "end": 30, "kind": "underline"},
        {"start": 10, "end": 19, "kind": "highlight"},
        {"start": 31, "end": 39, "kind": "underline"},
    ]
    card = ExportCard(
        tag="Resilience answers China rise scenario",
        tag_markup=[],
        card_text=text,
        markup=markup,
        cite_short="Roberts 19",
        raw_cite="Dorothy E. Roberts, professor, Penn Law, 2019, Title, Journal, p.42",
    )
    entry = ExportEntry(header_path=["Aff", "1AC"], card=card)

    blob = render_workspace_to_docx("test-doc", [entry])
    path = _write_temp(blob)

    paragraphs = extract_docx(path)
    body = _find_paragraph_by_text(paragraphs, text)
    underline, highlight = _reconstruct_spans(body)

    # Highlight implies underline on export (so cards read in plain Word
    # without a Verbatim template), so the underline coverage is the
    # union of the original underline + highlight spans. Both originals
    # had highlight ⊆ underline, so the union equals the underline.
    assert underline == [(4, 30), (31, 39)]
    assert highlight == [(10, 19)]


def test_round_trip_card_no_markup():
    text = "Plain text with no markup at all."
    card = ExportCard(
        tag="Plain card",
        tag_markup=[],
        card_text=text,
        markup=[],
        cite_short="X 25",
        raw_cite="raw cite",
    )
    entry = ExportEntry(header_path=[], card=card)
    blob = render_workspace_to_docx("plain", [entry])
    path = _write_temp(blob)

    paragraphs = extract_docx(path)
    body = _find_paragraph_by_text(paragraphs, text)
    underline, highlight = _reconstruct_spans(body)
    assert underline == []
    assert highlight == []


def test_round_trip_analytical():
    argument = "Their CP is severance because it does not solve the case."
    # Highlight "severance" [13,22)
    markup = [
        {"start": 13, "end": 22, "kind": "underline"},
        {"start": 13, "end": 22, "kind": "highlight"},
    ]
    analytical = ExportAnalytical(
        argument=argument,
        argument_markup=markup,
        answer_to="Poverty Adv. CP",
    )
    entry = ExportEntry(header_path=["Aff", "2AC"], analytical=analytical)
    blob = render_workspace_to_docx("ana-doc", [entry])
    path = _write_temp(blob)

    paragraphs = extract_docx(path)
    body = _find_paragraph_by_text(paragraphs, argument)
    underline, highlight = _reconstruct_spans(body)
    assert underline == [(13, 22)]
    assert highlight == [(13, 22)]

    # AT header should be present somewhere in the document
    assert any("AT: Poverty Adv. CP" in p.text for p in paragraphs)


def test_header_path_diffing():
    """Consecutive entries sharing a prefix should not re-emit headers."""
    card_a = ExportCard(
        tag="A", tag_markup=[], card_text="text-a",
        markup=[], cite_short="A 1", raw_cite="cite-a",
    )
    card_b = ExportCard(
        tag="B", tag_markup=[], card_text="text-b",
        markup=[], cite_short="B 1", raw_cite="cite-b",
    )
    card_c = ExportCard(
        tag="C", tag_markup=[], card_text="text-c",
        markup=[], cite_short="C 1", raw_cite="cite-c",
    )
    entries = [
        ExportEntry(header_path=["Aff", "1AC", "Adv. 1"], card=card_a),
        ExportEntry(header_path=["Aff", "1AC", "Adv. 1"], card=card_b),  # same path
        ExportEntry(header_path=["Aff", "1AC", "Adv. 2"], card=card_c),  # diverges at 3
    ]
    blob = render_workspace_to_docx("hdr", entries)
    path = _write_temp(blob)

    paragraphs = extract_docx(path)
    headings = [p for p in paragraphs if p.style and p.style.startswith("Heading")]
    heading_texts = [p.text for p in headings]

    # Expect: Aff (H1), 1AC (H2), Adv. 1 (H3), Adv. 2 (H3) — Adv. 2 only,
    # because Aff/1AC carried over from the previous entry's path.
    assert heading_texts == ["Aff", "1AC", "Adv. 1", "Adv. 2"]
