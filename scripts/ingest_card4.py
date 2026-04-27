"""One-shot insert for Cap K card 4 (training session, approved by user).

Also parents `demystification-alt` under `alt-solvency`.
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

EXTRACT_PATH = "extracted/cap_k.jsonl"
SOURCE_FILE = "Cap K Commodification Cards.docx"


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
    tag_p = paragraphs[33]
    cite_full_p = paragraphs[35]
    body_paras = [paragraphs[36]]

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

    tag_text, tag_raw = spans_from_runs(tag_p["runs"])
    tag_markup = merge(tag_raw)

    source = SourceData(
        cite_short="Rothe & Collins 17",
        author_last="Rothe & Collins",
        author_full="Dawn L. Rothe; Victoria Ellen Collins",
        qualifications=(
            "Rothe — Professor of Criminology and Chair of the School of Justice Studies at "
            "Eastern Kentucky University; Director of the Crimes of the Powerful Center, PhD. "
            "Collins — Associate Professor in the School of Justice Studies at Eastern "
            "Kentucky University; PhD in criminology and criminal justice."
        ),
        publication="Critical Criminology (Crit Crim) 25, 609–618",
        title=(
            "The Illusion of Resistance: Commodification and Reification of Neoliberalism "
            "and the State"
        ),
        published_date="2017-10-06 (issue Dec 2017)",
        year=2017,
        url="https://doi.org/10.1007/s10612-017-9374-7",
        raw_cite=cite_full_p["text"],
    )

    card = CardData(
        tag=tag_text,
        tag_markup=tag_markup,
        card_text=body_text,
        markup=body_markup,
        source_file=SOURCE_FILE,
        block_path=["Cap K Commodification Cards"],
    )

    approved_tags = [
        ("cap-k", "Capitalism K"),
        ("commodification-of-resistance", "Commodification of Resistance"),
        ("neoliberalism-bad", "Neoliberalism Bad"),
        ("co-optation", "Co-optation"),
        ("reformism-fails", "Reformism Fails"),
        ("alt-solvency", "Alt Solvency"),
        ("demystification-alt", "Demystification Alt"),
    ]

    with session_scope() as s:
        src = get_or_create_source(s, source)
        c = insert_card(s, src, card, approved_tags)

        # Parent demystification-alt under alt-solvency.
        alt_solv = get_or_create_content_tag(s, "alt-solvency", "Alt Solvency")
        demyst = get_or_create_content_tag(s, "demystification-alt", "Demystification Alt")
        if demyst.parent_id != alt_solv.id:
            demyst.parent_id = alt_solv.id
            s.add(demyst)

        print(f"Inserted source.id={src.id}  card.id={c.id}")
        print(f"  card_text: {len(body_text)} chars")
        print(f"  underline: {sum(1 for x in body_markup if x['kind']=='underline')}  "
              f"highlight: {sum(1 for x in body_markup if x['kind']=='highlight')}")
        print(f"  tags: {[t[0] for t in approved_tags]}")
        print(f"  parented demystification-alt under alt-solvency (parent_id={alt_solv.id})")

        all_tags = s.query(ContentTag).order_by(ContentTag.id).all()
        print("\n--- content_tags after insert ---")
        for t in all_tags:
            parent = f" (parent={t.parent_id})" if t.parent_id else ""
            print(f"  [{t.id}] {t.slug}{parent}")


if __name__ == "__main__":
    main()
