"""Batch insert Abolition Aff/Neg cards 10-14 (training session 2, approved by user).

All 5 cards live under Aff > 1AC > Gradualism Adv. Plan Text (P78) skipped per
"plan texts are not evidence" convention.

New tags introduced this batch: incrementalism, community-control, public-opinion,
reformism-good, ocr-artifacts.
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
BLOCK_PATH = ["Aff", "1AC", "Gradualism Adv."]


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


def build_body(paragraphs, idx_range):
    text = ""
    raw: list[dict] = []
    for i in idx_range:
        bp = paragraphs[i]
        if not bp["text"].strip() and not bp["runs"]:
            continue
        if text and text[-1].isalnum() and bp["text"][:1].isalnum():
            text += " "
        seg, sr = spans_from_runs(bp["runs"], base=len(text))
        text += seg
        raw.extend(sr)
    return text, merge(raw)


def main() -> None:
    paragraphs = [json.loads(line) for line in open(EXTRACT_PATH)]

    cards = [
        # CARD 10 — Garza et al., "Who Do You Serve, Who Do You Protect?"
        # (heavy OCR artifacts in body — preserved verbatim, flagged via ocr-artifacts tag)
        dict(
            tag_idx=81, cite_idx=82, body_range=[83],
            source=SourceData(
                cite_short="Garza et al. 16",
                author_last="Garza et al.",
                author_full=(
                    "Alicia Garza; Maya Schenwar; Joe Macaré; Alana Yu-lan Price"
                ),
                qualifications=(
                    "Garza — civil rights activist and writer; co-founder of the "
                    "international Black Lives Matter movement. Schenwar — "
                    "editor-in-chief of Truthout; author of Locked Down, Locked Out: "
                    "Why Prison Doesn't Work and How We Can Do Better; co-editor of "
                    "Who Do You Serve, Who Do You Protect? Macaré — writer, editor, "
                    "and development/communications professional. Yu-lan Price — "
                    "Truthout's content relations editor; co-editor of Who Do You "
                    "Serve, Who Do You Protect; previously managing editor of Tikkun "
                    "magazine."
                ),
                publication="Haymarket Books",
                title=(
                    "Who Do You Serve, Who Do You Protect?: Police Violence and "
                    "Resistance in the United States (pp. 111-117)"
                ),
                published_date="2016-05-30",
                year=2016,
                # UMich library proxy URL preserved verbatim per project policy
                url="https://ebookcentral-proquest-com.proxy.lib.umich.edu/lib/umichigan/detail.action?docID=4548988",
                raw_cite=paragraphs[82]["text"],
            ),
            tags=[
                ("abolition", "Abolition"),
                ("police-abolition", "Police Abolition"),
                ("incrementalism", "Incrementalism"),
                ("divestment-reinvestment", "Divestment / Reinvestment"),
                ("ocr-artifacts", "OCR Artifacts"),
            ],
        ),
        # CARD 11 — Moody, MA thesis on police abolition ethics
        dict(
            tag_idx=85, cite_idx=86, body_range=[88],
            source=SourceData(
                cite_short="Moody 17",
                author_last="Moody",
                author_full="Amber Moody",
                qualifications=(
                    "M.A. in Applied Ethics; thesis presented November 22, 2017."
                ),
                publication=None,
                title=(
                    "Protection or Control? An Ethical Argument for Police "
                    "Abolition in the United States (pp. 85-88)"
                ),
                published_date="2017-11-22",
                year=2017,
                url=None,
                raw_cite=paragraphs[86]["text"],
            ),
            tags=[
                ("abolition", "Abolition"),
                ("police-abolition", "Police Abolition"),
                ("incrementalism", "Incrementalism"),
                ("community-control", "Community Control"),
            ],
        ),
        # CARD 12 — Shugars on the fear of radical change (Roberto Unger framing)
        dict(
            tag_idx=91, cite_idx=92, body_range=[93],
            source=SourceData(
                cite_short="Shugars 14",
                author_last="Shugars",
                author_full="Sarah Shugars",
                qualifications=(
                    "Computational political scientist studying American political "
                    "behavior; develops new methods in natural language processing, "
                    "network analysis, and machine learning; PhD from Northeastern's "
                    "Network Science program in Spring 2020."
                ),
                publication="sarahshugars.com",
                title="The Fear of Radical Change",
                published_date="2014-03-17",
                year=2014,
                url="https://sarahshugars.com/2014/04/the-fear-of-radical-change/",
                raw_cite=paragraphs[92]["text"],
            ),
            tags=[
                ("abolition", "Abolition"),
                ("incrementalism", "Incrementalism"),
            ],
        ),
        # CARD 13 — Schroedel on incremental change (CitizenLab blog)
        dict(
            tag_idx=95, cite_idx=96, body_range=[97],
            source=SourceData(
                cite_short="Schroedel 19",
                author_last="Schroedel",
                author_full="Jenna Schroedel",
                qualifications="Marketing Intern at CitizenLab.",
                publication="CitizenLab",
                title="What's the difference? Radical vs. Incremental Change",
                published_date="2019-11-10",
                year=2019,
                url="https://www.citizenlab.co/blog/e-government/whats-the-difference-radical-vs-incremental-change/",
                raw_cite=paragraphs[96]["text"],
            ),
            tags=[
                ("abolition", "Abolition"),
                ("incrementalism", "Incrementalism"),
            ],
        ),
        # CARD 14 — Ekins/Cato polling on abolition vs reform support
        dict(
            tag_idx=99, cite_idx=100, body_range=[101],
            source=SourceData(
                cite_short="Ekins 6/4/20",
                author_last="Ekins",
                author_full="Emily Ekins",
                qualifications=(
                    "Research fellow and director of polling at the Cato Institute. "
                    "Research focuses on public opinion, American politics, "
                    "political psychology, and social movements. Leads the Cato "
                    "Institute project on public opinion. PhD and MA in political "
                    "science from UCLA."
                ),
                publication="Cato Institute",
                title=(
                    "Americans Don't Want to #Defund Police, Instead They Agree on "
                    "Reform"
                ),
                published_date="2020-06-04",
                year=2020,
                url="https://www.cato.org/blog/americans-agree-policing-reform",
                raw_cite=paragraphs[100]["text"],
            ),
            tags=[
                ("abolition", "Abolition"),
                ("incrementalism", "Incrementalism"),
                ("public-opinion", "Public Opinion"),
                ("reformism-good", "Reformism Good"),
            ],
        ),
    ]

    with session_scope() as s:
        for n, c in enumerate(cards, start=10):
            tag_text, tag_raw = spans_from_runs(paragraphs[c["tag_idx"]]["runs"])
            tag_markup = merge(tag_raw)
            body_text, body_markup = build_body(paragraphs, c["body_range"])

            src = get_or_create_source(s, c["source"])
            card = CardData(
                tag=tag_text,
                tag_markup=tag_markup,
                card_text=body_text,
                markup=body_markup,
                source_file=SOURCE_FILE,
                block_path=BLOCK_PATH,
            )
            inserted = insert_card(s, src, card, c["tags"])
            nu = sum(1 for x in body_markup if x["kind"] == "underline")
            nh = sum(1 for x in body_markup if x["kind"] == "highlight")
            print(
                f"  card #{n:2d}  src={src.id:2d}  card.id={inserted.id:2d}  "
                f"body={len(body_text):5d}c  u={nu:2d}/h={nh:2d}  "
                f"tags={len(c['tags'])}"
            )


if __name__ == "__main__":
    main()
