"""Insert Abolition Aff/Neg card 2 (training session 2, approved by user).

Adds: anti-blackness (top), slavery-origins (top), divestment-reinvestment
(child of defund-police).
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
    tag_p = paragraphs[30]
    cite_p = paragraphs[31]
    body_p = paragraphs[32]

    tag_text, tag_raw = spans_from_runs(tag_p["runs"])
    tag_markup = merge(tag_raw)
    body_text, body_raw = spans_from_runs(body_p["runs"])
    body_markup = merge(body_raw)

    source = SourceData(
        cite_short="Fernandez 6/4/20",
        author_last="Fernandez",
        author_full="Paige Fernandez",
        qualifications=(
            "Policing Policy Advisor in the ACLU's National Political Advocacy "
            "Department; develops nationwide advocacy around police practices; "
            "previously co-founded and directed multiple chapters of Together We "
            "Stand (nonprofit aimed at dismantling racism, discrimination, and "
            "police brutality); MPP from Oxford University; B.A. from Sarah "
            "Lawrence College."
        ),
        publication="Cosmopolitan",
        title="Why People Say Defund the Police at Black Lives Matter Protests",
        published_date="2020-06-04",
        year=2020,
        url="https://www.cosmopolitan.com/politics/a32757152/defund-police-black-lives-matter/",
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
        ("reformism-fails", "Reformism Fails"),
        ("slavery-origins", "Slavery Origins of Policing"),
        ("anti-blackness", "Anti-Blackness"),
        ("divestment-reinvestment", "Divestment / Reinvestment"),
    ]

    with session_scope() as s:
        src = get_or_create_source(s, source)
        c = insert_card(s, src, card, approved_tags)

        # Parent divestment-reinvestment under defund-police.
        defund = get_or_create_content_tag(s, "defund-police", "Defund Police")
        divest = get_or_create_content_tag(
            s, "divestment-reinvestment", "Divestment / Reinvestment"
        )
        if divest.parent_id != defund.id:
            divest.parent_id = defund.id
            s.add(divest)

        print(f"Inserted source.id={src.id}  card.id={c.id}")
        print(f"  card_text: {len(body_text)} chars")
        print(f"  underline: {sum(1 for x in body_markup if x['kind']=='underline')}  "
              f"highlight: {sum(1 for x in body_markup if x['kind']=='highlight')}")
        print(f"  block_path: {c.block_path}")
        print(f"  tags: {[t[0] for t in approved_tags]}")


if __name__ == "__main__":
    main()
