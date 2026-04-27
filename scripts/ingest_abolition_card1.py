"""Insert Abolition Aff/Neg card 1 (training session 2, approved by user).

Also seeds a 3-level tag hierarchy: abolition -> police-abolition -> defund-police.
"""

from __future__ import annotations

import json

from debatabase.db import session_scope
from debatabase.ingest import (
    CardData,
    SourceData,
    get_or_create_content_tag,
    get_or_create_source,
    insert_card,
)
from debatabase.models import ContentTag

EXTRACT_PATH = "extracted/abolition_michigan.jsonl"
SOURCE_FILE = "Aff - Neg Abolition - Michigan7 2020 CCPTW.docx"


def spans_from_runs(runs, base: int = 0):
    parts: list[str] = []
    cur = base
    raw: list[dict] = []
    for r in runs:
        t = r.get("text", "")
        s, e = cur, cur + len(t)
        u = r.get("underline")
        if u and u != "none":
            raw.append({"start": s, "end": e, "kind": "underline"})
        if r.get("highlight") or r.get("shading"):
            raw.append({"start": s, "end": e, "kind": "highlight"})
        parts.append(t)
        cur = e
    return "".join(parts), raw


def merge(raw: list[dict]) -> list[dict]:
    m: dict[str, list[dict]] = {"underline": [], "highlight": []}
    for s in raw:
        b = m[s["kind"]]
        if b and b[-1]["end"] == s["start"]:
            b[-1]["end"] = s["end"]
        else:
            b.append({"start": s["start"], "end": s["end"]})
    return [{"kind": "underline", **s} for s in m["underline"]] + [
        {"kind": "highlight", **s} for s in m["highlight"]
    ]


def main() -> None:
    paragraphs = [json.loads(line) for line in open(EXTRACT_PATH)]
    tag_p = paragraphs[26]
    cite_p = paragraphs[27]   # combined cite_short + meta + URL
    body_p = paragraphs[28]

    tag_text, tag_raw = spans_from_runs(tag_p["runs"])
    tag_markup = merge(tag_raw)
    body_text, body_raw = spans_from_runs(body_p["runs"])
    body_markup = merge(body_raw)

    source = SourceData(
        cite_short="Lewis & Weichselbaum 6/9",
        author_last="Lewis & Weichselbaum",
        author_full="Nicole Lewis; Simone Weichselbaum",
        qualifications=(
            "Both staff writers at The Marshall Project. "
            "Weichselbaum — focuses on federal law enforcement and local policing; "
            "co-chair of diversity & inclusion at TMP; 15+ years reporting on urban "
            "criminal justice systems; previously NY Daily News and Philadelphia Daily "
            "News; graduate degree in criminology, University of Pennsylvania. "
            "Lewis — previously wrote for Washington Post's \"The Fact Checker\" blog; "
            "UMich and CUNY Graduate School of Journalism; 2016 Education Writers "
            "Association National Award."
        ),
        publication="The Marshall Project",
        title=(
            "Support For Defunding The Police Department Is Growing. "
            "Here's Why It's Not A Silver Bullet."
        ),
        published_date="2020-06-09",
        year=2020,
        url=(
            "https://www.themarshallproject.org/2020/06/09/"
            "support-for-defunding-the-police-department-is-growing-"
            "here-s-why-it-s-not-a-silver-bullet"
        ),
        raw_cite=cite_p["text"],
    )

    card = CardData(
        tag=tag_text,
        tag_markup=tag_markup,
        card_text=body_text,
        markup=body_markup,
        source_file=SOURCE_FILE,
        block_path=["Aff", "1AC", "Social Workers Adv."],
    )

    approved_tags = [
        ("abolition", "Abolition"),
        ("police-abolition", "Police Abolition"),
        ("defund-police", "Defund Police"),
        ("social-workers-solve", "Social Workers Solve"),
        ("police-overreach", "Police Overreach"),
        ("reformism-fails", "Reformism Fails"),
    ]

    with session_scope() as s:
        src = get_or_create_source(s, source)
        c = insert_card(s, src, card, approved_tags)

        # Hierarchy: abolition (top) -> police-abolition -> defund-police
        abolition = get_or_create_content_tag(s, "abolition", "Abolition")
        police_abol = get_or_create_content_tag(s, "police-abolition", "Police Abolition")
        defund = get_or_create_content_tag(s, "defund-police", "Defund Police")
        if police_abol.parent_id != abolition.id:
            police_abol.parent_id = abolition.id
            s.add(police_abol)
        if defund.parent_id != police_abol.id:
            defund.parent_id = police_abol.id
            s.add(defund)

        print(f"Inserted source.id={src.id}  card.id={c.id}")
        print(f"  card_text: {len(body_text)} chars")
        print(f"  underline: {sum(1 for x in body_markup if x['kind']=='underline')}  "
              f"highlight: {sum(1 for x in body_markup if x['kind']=='highlight')}")
        print(f"  block_path: {c.block_path}")
        print(f"  tags: {[t[0] for t in approved_tags]}")
        print(f"  hierarchy: abolition({abolition.id}) -> "
              f"police-abolition({police_abol.id}) -> defund-police({defund.id})")


if __name__ == "__main__":
    main()
