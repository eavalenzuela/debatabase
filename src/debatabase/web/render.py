"""Render card_text + markup spans into HTML.

Each markup span is `{start, end, kind: "underline" | "highlight"}` over the
plain text. Spans of different kinds may overlap (highlight is always a subset
of underline). We turn them into properly nested HTML by walking character
events.

Render modes:
  full           — show all text; underline + highlight visually distinguished
  highlight-only — show only highlighted spans, joined by ellipses
  underline-only — show only underlined spans (which include highlights)
  plain          — strip all markup
"""

from __future__ import annotations

from html import escape
from typing import Iterable, Literal

RenderMode = Literal["full", "highlight-only", "underline-only", "plain"]


def render_card(text: str, markup: list[dict], mode: RenderMode = "full") -> str:
    if mode == "plain":
        return escape(text)

    if mode == "highlight-only":
        spans = sorted(
            (s for s in markup if s["kind"] == "highlight"),
            key=lambda s: s["start"],
        )
        parts = []
        for s in spans:
            parts.append(escape(text[s["start"]:s["end"]]))
        return ' <span class="ellipsis">…</span> '.join(p for p in parts if p)

    if mode == "underline-only":
        spans = sorted(
            (s for s in markup if s["kind"] == "underline"),
            key=lambda s: s["start"],
        )
        parts = []
        for s in spans:
            parts.append(escape(text[s["start"]:s["end"]]))
        return ' <span class="ellipsis">…</span> '.join(p for p in parts if p)

    # full mode: properly nested HTML respecting both kinds
    return _render_full(text, markup)


def _render_full(text: str, markup: list[dict]) -> str:
    if not markup:
        return escape(text)

    # Build event list at character boundaries
    boundaries = {0, len(text)}
    for s in markup:
        boundaries.add(s["start"])
        boundaries.add(s["end"])
    sorted_boundaries = sorted(boundaries)

    out: list[str] = []
    for i in range(len(sorted_boundaries) - 1):
        start = sorted_boundaries[i]
        end = sorted_boundaries[i + 1]
        if start >= end:
            continue
        # What kinds cover [start, end)?
        underlined = any(
            s["kind"] == "underline" and s["start"] <= start and s["end"] >= end
            for s in markup
        )
        highlighted = any(
            s["kind"] == "highlight" and s["start"] <= start and s["end"] >= end
            for s in markup
        )
        chunk = escape(text[start:end])
        if highlighted:
            # highlighted text is implicitly also underlined (per project policy)
            chunk = f'<mark class="hl">{chunk}</mark>'
        elif underlined:
            chunk = f'<u class="ul">{chunk}</u>'
        out.append(chunk)
    return "".join(out)


def snippet(text: str, query: str | None = None, length: int = 240) -> str:
    """Plain-text snippet around the first match of `query`, or the start."""
    if not query:
        return escape(text[:length] + ("…" if len(text) > length else ""))
    idx = text.lower().find(query.lower())
    if idx < 0:
        return escape(text[:length] + ("…" if len(text) > length else ""))
    start = max(0, idx - 60)
    end = min(len(text), idx + len(query) + 180)
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(text) else ""
    chunk = text[start:end]
    # Highlight the match
    qi = chunk.lower().find(query.lower())
    if qi >= 0:
        chunk = (
            escape(chunk[:qi])
            + f'<mark class="match">{escape(chunk[qi:qi+len(query)])}</mark>'
            + escape(chunk[qi+len(query):])
        )
    else:
        chunk = escape(chunk)
    return prefix + chunk + suffix
