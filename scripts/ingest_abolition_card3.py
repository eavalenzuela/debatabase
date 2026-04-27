"""Insert Abolition Aff/Neg card 3 (training session 2, approved by user).

Body spans P37–P43. Cite has no parenthetical delimiters; URL is the
author's bio page (cutter error preserved verbatim per project policy).
"""

from __future__ import annotations

import json

from debatabase.db import session_scope
from debatabase.ingest import (
    CardData,
    SourceData,
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
    tag_p = paragraphs[34]
    cite_p = paragraphs[35]
    body_paras = [paragraphs[i] for i in range(37, 44)]  # P37-P43

    tag_text, tag_raw = spans_from_runs(tag_p["runs"])
    tag_markup = merge(tag_raw)

    text = ""
    raw: list[dict] = []
    for bp in body_paras:
        if text and text[-1].isalnum() and bp["text"][:1].isalnum():
            text += " "
        seg, sr = spans_from_runs(bp["runs"], base=len(text))
        text += seg
        raw.extend(sr)
    body_text = text
    body_markup = merge(raw)

    source = SourceData(
        cite_short="Sapien et al 20",
        author_last="Sapien",
        author_full="Joaquin Sapien et al.",
        qualifications=(
            "Has explored a broad range of topics, including criminal justice, "
            "social services, and the environment. In 2019, co-producer and "
            "correspondent for \"Right to Fail,\" a film for the PBS documentary "
            "series Frontline. The film was based on his 2018 examination of a "
            "flawed housing program for New Yorkers with mental illness."
        ),
        publication="ProPublica",
        title=(
            "Domestic Violence and Child Abuse Will Rise During Quarantines. "
            "So Will Neglect of At-Risk People, Social Workers Say"
        ),
        published_date="2020",
        year=2020,
        # Cutter pasted the author's bio page URL instead of the article URL;
        # preserved as-is per project policy.
        url="https://www.propublica.org/people/joaquin-sapien",
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
        ("social-workers-solve", "Social Workers Solve"),
        ("domestic-violence", "Domestic Violence"),
        ("child-welfare", "Child Welfare / CPS"),
        ("vulnerable-populations", "Vulnerable Populations"),
    ]

    with session_scope() as s:
        src = get_or_create_source(s, source)
        c = insert_card(s, src, card, approved_tags)
        print(f"Inserted source.id={src.id}  card.id={c.id}")
        print(f"  card_text: {len(body_text)} chars")
        print(f"  underline: {sum(1 for x in body_markup if x['kind']=='underline')}  "
              f"highlight: {sum(1 for x in body_markup if x['kind']=='highlight')}")
        print(f"  block_path: {c.block_path}")
        print(f"  tags: {[t[0] for t in approved_tags]}")


if __name__ == "__main__":
    main()
