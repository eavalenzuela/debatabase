"""Faithful, dumb extractor for .docx evidence files.

Emits a flat stream of paragraphs with run-level formatting. Does NOT decide
where cards begin/end, what's a tag vs a cite vs a body. That semantic work
is done by Claude reviewing this output. The contract here is: lose no
information that the segmenter might need.
"""

from __future__ import annotations

import json
import zipfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from xml.etree import ElementTree as ET

from docx import Document
from docx.oxml.ns import qn

# White / no-fill values we treat as "not actually highlighted" so the
# segmenter doesn't get noise. Word writes "FFFFFF" or "auto" for runs
# inside a block that has paragraph-level shading set to white.
_NON_HIGHLIGHT_FILLS = {"ffffff", "auto", "none", ""}

_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
ET.register_namespace("w", _W_NS)


def _w(tag: str) -> str:
    return f"{{{_W_NS}}}{tag}"


def _build_style_markup_map(docx_path: str | Path) -> dict[str, dict]:
    """Parse styles.xml and return {styleId: {underline, highlight, shading}}.

    Only character-style inheritance is captured; paragraph-style inheritance
    is intentionally ignored because Word's default heading styles sometimes
    declare underlines that aren't semantically meaningful as "this is cut card
    text."
    """
    out: dict[str, dict] = {}
    try:
        with zipfile.ZipFile(docx_path) as z:
            with z.open("word/styles.xml") as f:
                tree = ET.parse(f)
    except (KeyError, zipfile.BadZipFile):
        return out

    for style in tree.getroot().findall(_w("style")):
        if style.get(_w("type")) != "character":
            continue
        style_id = style.get(_w("styleId"))
        if not style_id:
            continue
        rpr = style.find(_w("rPr"))
        if rpr is None:
            continue
        info: dict[str, str | None] = {}
        u = rpr.find(_w("u"))
        if u is not None:
            info["underline"] = u.get(_w("val"))
        hl = rpr.find(_w("highlight"))
        if hl is not None:
            val = hl.get(_w("val"))
            if val and val.lower() not in _NON_HIGHLIGHT_FILLS:
                info["highlight"] = val
        shd = rpr.find(_w("shd"))
        if shd is not None:
            fill = shd.get(_w("fill"))
            if fill and fill.lower() not in _NON_HIGHLIGHT_FILLS:
                info["shading"] = fill
        if info:
            out[style_id] = info
    return out


@dataclass
class Run:
    text: str
    bold: bool = False
    italic: bool = False
    underline: str | None = None        # e.g. "single", "thick", "double", "none"
    highlight: str | None = None        # w:highlight value (e.g. "cyan", "green", "yellow")
    shading: str | None = None          # w:shd fill hex (e.g. "00FFFF") if non-white
    font_size_pt: float | None = None   # half-points/2; reported in pt
    style: str | None = None            # rStyle (e.g. "Style13ptBold")


@dataclass
class Paragraph:
    index: int
    style: str | None
    text: str
    runs: list[Run] = field(default_factory=list)


def _run_underline(r) -> str | None:
    rpr = r._element.rPr
    if rpr is None:
        return None
    u = rpr.find(qn("w:u"))
    if u is None:
        return None
    return u.get(qn("w:val"))


def _run_highlight(r) -> str | None:
    rpr = r._element.rPr
    if rpr is None:
        return None
    hl = rpr.find(qn("w:highlight"))
    if hl is None:
        return None
    val = hl.get(qn("w:val"))
    if val and val.lower() not in _NON_HIGHLIGHT_FILLS:
        return val
    return None


def _run_shading(r) -> str | None:
    rpr = r._element.rPr
    if rpr is None:
        return None
    shd = rpr.find(qn("w:shd"))
    if shd is None:
        return None
    fill = shd.get(qn("w:fill"))
    if fill and fill.lower() not in _NON_HIGHLIGHT_FILLS:
        return fill
    return None


def _run_font_size_pt(r) -> float | None:
    rpr = r._element.rPr
    if rpr is None:
        return None
    sz = rpr.find(qn("w:sz"))
    if sz is None:
        return None
    val = sz.get(qn("w:val"))
    try:
        return int(val) / 2.0  # w:sz is in half-points
    except (TypeError, ValueError):
        return None


def _run_style(r) -> str | None:
    rpr = r._element.rPr
    if rpr is None:
        return None
    rstyle = rpr.find(qn("w:rStyle"))
    if rstyle is None:
        return None
    return rstyle.get(qn("w:val"))


def _extract_run(r, style_map: dict[str, dict]) -> Run:
    """Resolve direct run formatting; fall back to character-style inheritance.

    Word lets cutters underline either by setting <w:u> directly on the run OR
    by applying a character style (<w:rStyle>) that declares <w:u>. We need
    both. Direct formatting wins over inherited; an explicit "none" override
    clears inherited formatting.
    """
    direct_u = _run_underline(r)
    direct_hl = _run_highlight(r)
    direct_shd = _run_shading(r)
    style_id = _run_style(r)

    inherited = style_map.get(style_id, {}) if style_id else {}

    if direct_u is not None:
        underline = None if direct_u == "none" else direct_u
    else:
        inherit_u = inherited.get("underline")
        underline = None if inherit_u in (None, "none") else inherit_u

    highlight = direct_hl if direct_hl is not None else inherited.get("highlight")
    shading = direct_shd if direct_shd is not None else inherited.get("shading")

    return Run(
        text=r.text or "",
        bold=bool(r.bold),
        italic=bool(r.italic),
        underline=underline,
        highlight=highlight,
        shading=shading,
        font_size_pt=_run_font_size_pt(r),
        style=style_id,
    )


def extract_docx(path: str | Path) -> list[Paragraph]:
    """Extract every paragraph from a .docx as a list of Paragraph dataclasses.

    Empty paragraphs are preserved (with empty runs list) so paragraph
    indices line up with what the segmenter sees.
    """
    style_map = _build_style_markup_map(path)
    doc = Document(str(path))
    out: list[Paragraph] = []
    for i, p in enumerate(doc.paragraphs):
        style_name = p.style.name if p.style is not None else None
        runs = [_extract_run(r, style_map) for r in p.runs]
        runs = [
            r for r in runs
            if r.text or r.bold or r.italic or r.underline or r.highlight or r.shading
        ]
        out.append(Paragraph(index=i, style=style_name, text=p.text, runs=runs))
    return out


def to_jsonl(paragraphs: list[Paragraph]) -> str:
    return "\n".join(json.dumps(asdict(p), ensure_ascii=False) for p in paragraphs)


def main() -> None:
    import argparse

    ap = argparse.ArgumentParser(description="Extract a .docx to paragraph/run JSONL.")
    ap.add_argument("path", help="Path to .docx file")
    ap.add_argument(
        "-o", "--output",
        help="Output JSONL file (default: stdout)",
    )
    ap.add_argument(
        "--summary", action="store_true",
        help="Print a one-line summary instead of the full extraction",
    )
    args = ap.parse_args()

    paragraphs = extract_docx(args.path)

    if args.summary:
        styles: dict[str, int] = {}
        for p in paragraphs:
            styles[p.style or ""] = styles.get(p.style or "", 0) + 1
        underline_runs = sum(1 for p in paragraphs for r in p.runs if r.underline and r.underline != "none")
        highlight_runs = sum(1 for p in paragraphs for r in p.runs if r.highlight or r.shading)
        print(f"paragraphs={len(paragraphs)}  underline_runs={underline_runs}  highlight_runs={highlight_runs}")
        print("styles:", dict(sorted(styles.items(), key=lambda x: -x[1])))
        return

    payload = to_jsonl(paragraphs)
    if args.output:
        Path(args.output).write_text(payload)
    else:
        print(payload)


if __name__ == "__main__":
    main()
