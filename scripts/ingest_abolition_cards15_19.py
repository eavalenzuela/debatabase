"""Batch insert Abolition Aff/Neg cards 15-19 (training session 2, approved by user).

Cards 15-17 sit under Aff > 2AC > Framing – Probability.
Cards 18-19 sit under Aff > 2AC > Framing – DA Risk Low.

Cards 15-17 are the first cards in the corpus with substantial highlighting —
this is the in-round-spoken text, distinct from the broader underlined argumentative
context. Verified the character-style extractor handles the {h}-inside-[u] pattern
correctly.

New tags introduced this batch (all top-level, no parenting):
  probability-framing, existential-risk-bad, pascals-wager, decision-theory,
  structural-violence.
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
        # CARD 15 — Munthe on x-risk arguments as Pascal's Wager
        dict(
            tag_idx=121, cite_idx=122, body_range=range(123, 133),
            block_path=["Aff", "2AC", "Framing – Probability"],
            source=SourceData(
                cite_short="Munthe 15",
                author_last="Munthe",
                author_full="Christian Munthe",
                qualifications=(
                    "PhD; Practical Philosophy Professor; Associate Head of "
                    "Department for Research at the University of Gothenburg."
                ),
                publication="Philosophical Comment (blog)",
                title=(
                    "Why Aren't Existential Risk / Ultimate Harm Argument "
                    "Advocates All Attending Mass?"
                ),
                published_date="2015-02-01",
                year=2015,
                url="http://philosophicalcomment.blogspot.com/2015/02/why-arent-existential-risk-ultimate.html",
                raw_cite=paragraphs[122]["text"],
            ),
            tags=[
                ("abolition", "Abolition"),
                ("probability-framing", "Probability Framing"),
                ("existential-risk-bad", "Existential Risk Bad"),
                ("pascals-wager", "Pascal's Wager"),
            ],
        ),
        # CARD 16 — Tarsney on stochastic dominance as alternative decision theory
        dict(
            tag_idx=133, cite_idx=134, body_range=range(135, 145),
            block_path=["Aff", "2AC", "Framing – Probability"],
            source=SourceData(
                cite_short="Tarsney 19",
                author_last="Tarsney",
                author_full="Christian J. Tarsney",
                qualifications=(
                    "Faculty of Philosophy at the Global Priorities Institute, "
                    "University of Oxford; Philosophy PhD at the University of "
                    "Maryland."
                ),
                publication="philarchive.org (working paper, version 7)",
                title=(
                    "Exceeding Expectations: Stochastic Dominance as a General "
                    "Decision Theory"
                ),
                published_date="2019-09",
                year=2019,
                url="https://philarchive.org/archive/TAREES",
                raw_cite=paragraphs[134]["text"],
            ),
            tags=[
                ("abolition", "Abolition"),
                ("probability-framing", "Probability Framing"),
                ("existential-risk-bad", "Existential Risk Bad"),
                ("pascals-wager", "Pascal's Wager"),
                ("decision-theory", "Decision Theory"),
            ],
        ),
        # CARD 17 — Kaczmarek on indirect approaches to x-risk via ripple effects
        dict(
            tag_idx=145, cite_idx=146, body_range=range(147, 157),
            block_path=["Aff", "2AC", "Framing – Probability"],
            source=SourceData(
                cite_short="Kaczmarek 17",
                author_last="Kaczmarek",
                author_full="Patrick Kaczmarek",
                qualifications=(
                    "PhD at the University of Glasgow; Senior Researcher at "
                    "Effective Giving; Visiting Researcher at the Future of "
                    "Humanity Institute, University of Oxford; Visiting Scholar "
                    "at the Department of Philosophy, University of Pittsburgh."
                ),
                publication="Utilitas, 29(2)",
                title=(
                    "How Much is Rule-Consequentialism Really Willing to Give Up "
                    "to Save the Future of Humanity?"
                ),
                published_date="2017",
                year=2017,
                url="https://www.cambridge.org/core/journals/utilitas/article/how-much-is-ruleconsequentialism-really-willing-to-give-up-to-save-the-future-of-humanity/F867301151A79F7DA566A14DF71749B3",
                raw_cite=paragraphs[146]["text"],
            ),
            tags=[
                ("abolition", "Abolition"),
                ("probability-framing", "Probability Framing"),
                ("root-causes", "Root Causes"),
            ],
        ),
        # CARD 18 — Yudkowsky on conjunction fallacy in extinction predictions
        dict(
            tag_idx=158, cite_idx=159, body_range=[160],
            block_path=["Aff", "2AC", "Framing – DA Risk Low"],
            source=SourceData(
                cite_short="Yudkowsky 06",
                author_last="Yudkowsky",
                author_full="Eliezer Yudkowsky",
                qualifications=(
                    "Singularity Institute for Artificial Intelligence, Palo Alto, "
                    "CA. (Card also cites Bruce Schneier, security expert, as a "
                    "secondary source within the body.)"
                ),
                publication=(
                    "Global Catastrophic Risks, eds. Nick Bostrom and Milan "
                    "Cirkovic (forthcoming at time of cite)"
                ),
                title="Cognitive biases potentially affecting judgment of global risks",
                published_date="2006-08-31",
                year=2006,
                url="https://intelligence.org/files/CognitiveBiases.pdf",
                raw_cite=paragraphs[159]["text"],
            ),
            tags=[
                ("abolition", "Abolition"),
                ("probability-framing", "Probability Framing"),
            ],
        ),
        # CARD 19 — Masur on cognitive biases inflating low-probability harms
        dict(
            tag_idx=161, cite_idx=162, body_range=[163],
            block_path=["Aff", "2AC", "Framing – DA Risk Low"],
            source=SourceData(
                cite_short="Masur 7",
                author_last="Masur",
                author_full="Jonathan Masur",
                qualifications=(
                    "Bigelow Fellow and Lecturer in Law, University of Chicago "
                    "Law School."
                ),
                publication="92 Iowa L. Rev. 1293",
                title="Probability Thresholds",
                published_date="2007",
                year=2007,
                url=None,
                raw_cite=paragraphs[162]["text"],
            ),
            tags=[
                ("abolition", "Abolition"),
                ("probability-framing", "Probability Framing"),
                ("structural-violence", "Structural Violence"),
            ],
        ),
    ]

    with session_scope() as s:
        for n, c in enumerate(cards, start=15):
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
                block_path=c["block_path"],
            )
            inserted = insert_card(s, src, card, c["tags"])
            nu = sum(1 for x in body_markup if x["kind"] == "underline")
            nh = sum(1 for x in body_markup if x["kind"] == "highlight")
            print(
                f"  card #{n:2d}  src={src.id:2d}  card.id={inserted.id:2d}  "
                f"body={len(body_text):6d}c  u={nu:2d}/h={nh:2d}  "
                f"tags={len(c['tags'])}"
            )


if __name__ == "__main__":
    main()
