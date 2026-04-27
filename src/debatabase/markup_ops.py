"""Markup span ops for the in-browser re-highlighting flow.

A markup list is a JSON-serializable list of ``{"start", "end", "kind"}``
dicts where kind is ``"underline"`` or ``"highlight"``. Spans of the same
kind may be coalesced when adjacent or overlapping. Highlight is treated
as a strict subset of underline per the project domain rule (see
``schema.sql`` and ``web/render.py``): adding a highlight implicitly
also adds an underline over the same range.

All functions here are pure — they never read or write the DB.
"""

from __future__ import annotations

from typing import Literal

Kind = Literal["underline", "highlight"]
Span = dict
Action = Literal["add", "clear"]


def apply_op(
    spans: list[Span],
    *,
    action: Action,
    start: int,
    end: int,
    kind: Kind | None = None,
) -> list[Span]:
    """Apply a single markup op and return the new (normalized) span list.

    ``add`` with ``kind="highlight"`` adds spans for both ``highlight``
    and ``underline`` over [start, end). ``add`` with ``kind="underline"``
    adds only an underline span. ``clear`` removes any markup of any kind
    that overlaps [start, end), splitting spans that straddle the range.
    """
    if start >= end:
        return list(spans)

    if action == "clear":
        return _normalize(_subtract(spans, start, end))

    if action == "add":
        if kind == "highlight":
            new = list(spans) + [
                {"start": start, "end": end, "kind": "underline"},
                {"start": start, "end": end, "kind": "highlight"},
            ]
        elif kind == "underline":
            new = list(spans) + [{"start": start, "end": end, "kind": "underline"}]
        else:
            raise ValueError(f"unknown kind for add: {kind!r}")
        return _normalize(new)

    raise ValueError(f"unknown action: {action!r}")


def _subtract(spans: list[Span], lo: int, hi: int) -> list[Span]:
    """Remove [lo, hi) from each span, splitting straddlers in two."""
    out: list[Span] = []
    for s in spans:
        a, b, k = s["start"], s["end"], s["kind"]
        if b <= lo or a >= hi:
            out.append(dict(s))
        elif a >= lo and b <= hi:
            continue
        elif a < lo and b > hi:
            out.append({"start": a, "end": lo, "kind": k})
            out.append({"start": hi, "end": b, "kind": k})
        elif a < lo:
            out.append({"start": a, "end": lo, "kind": k})
        else:
            out.append({"start": hi, "end": b, "kind": k})
    return out


def _normalize(spans: list[Span]) -> list[Span]:
    """Coalesce overlapping/adjacent spans of the same kind, drop empties."""
    by_kind: dict[str, list[tuple[int, int]]] = {}
    for s in spans:
        a, b = s["start"], s["end"]
        if a >= b:
            continue
        by_kind.setdefault(s["kind"], []).append((a, b))

    out: list[Span] = []
    for kind, ranges in by_kind.items():
        ranges.sort()
        merged: list[list[int]] = []
        for a, b in ranges:
            if merged and a <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], b)
            else:
                merged.append([a, b])
        out.extend({"start": a, "end": b, "kind": kind} for a, b in merged)

    out.sort(key=lambda s: (s["start"], s["kind"]))
    return out
