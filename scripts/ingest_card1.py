"""One-shot insert for Cap K card 1 (training session, approved by user)."""

from __future__ import annotations

import json

from debatabase.db import session_scope
from debatabase.ingest import CardData, SourceData, get_or_create_source, insert_card

EXTRACT_PATH = "extracted/cap_k.jsonl"
SOURCE_FILE = "Cap K Commodification Cards.docx"


def spans_from_runs(runs):
    text_parts: list[str] = []
    cursor = 0
    raw: list[dict] = []
    for r in runs:
        t = r.get("text", "")
        start, end = cursor, cursor + len(t)
        u = r.get("underline")
        if u and u != "none":
            raw.append({"start": start, "end": end, "kind": "underline"})
        if r.get("highlight") or r.get("shading"):
            raw.append({"start": start, "end": end, "kind": "highlight"})
        text_parts.append(t)
        cursor = end
    text = "".join(text_parts)
    merged: dict[str, list[dict]] = {"underline": [], "highlight": []}
    for s in raw:
        bucket = merged[s["kind"]]
        if bucket and bucket[-1]["end"] == s["start"]:
            bucket[-1]["end"] = s["end"]
        else:
            bucket.append({"start": s["start"], "end": s["end"]})
    spans = (
        [{"kind": "underline", **s} for s in merged["underline"]]
        + [{"kind": "highlight", **s} for s in merged["highlight"]]
    )
    return text, spans


def main() -> None:
    paragraphs = [json.loads(line) for line in open(EXTRACT_PATH)]
    tag_p, _, cite_full_p, body_p = paragraphs[2], paragraphs[3], paragraphs[4], paragraphs[5]

    tag_text, tag_markup = spans_from_runs(tag_p["runs"])
    body_text, body_markup = spans_from_runs(body_p["runs"])

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
        ("commodification-of-resistance", "Commodification of Resistance"),
        ("neoliberalism-bad", "Neoliberalism Bad"),
        ("cap-k", "Capitalism K"),
        ("reformism-fails", "Reformism Fails"),
        ("baudrillard", "Baudrillard"),
    ]

    with session_scope() as s:
        src = get_or_create_source(s, source)
        c = insert_card(s, src, card, approved_tags)
        print(f"Inserted source.id={src.id}  card.id={c.id}")
        print(f"  tag: {c.tag[:80]}...")
        print(f"  card_text: {len(body_text)} chars")
        print(f"  underline spans: {sum(1 for x in body_markup if x['kind']=='underline')}")
        print(f"  highlight spans: {sum(1 for x in body_markup if x['kind']=='highlight')}")
        print(f"  content tags: {[t[0] for t in approved_tags]}")


if __name__ == "__main__":
    main()
