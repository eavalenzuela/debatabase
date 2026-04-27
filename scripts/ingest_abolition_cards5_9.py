"""Batch insert Abolition Aff/Neg cards 5-9 (training session 2, approved by user).

All 5 cards live under Aff > 1AC > Social Workers Adv. Each has a distinct
source. Sub-block H4 "Scenario Two is Mental Health" (P63) is dropped per
project convention.

New tags introduced this batch: poverty-impact, root-causes.
Dropped from proposals: crime-impact (too generic).
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
BLOCK_PATH = ["Aff", "1AC", "Social Workers Adv."]


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
        # CARD 5 — APA on poverty's effects on children
        dict(
            tag_idx=51, cite_idx=52, body_range=range(53, 56),
            source=SourceData(
                cite_short="APA '09",
                author_last="APA",
                author_full=None,
                qualifications=(
                    "American Psychological Association — the leading scientific and "
                    "professional organization representing psychology in the United "
                    "States, with more than 121,000 researchers, educators, "
                    "clinicians, consultants and students as its members."
                ),
                publication="apa.org",
                title="Effects of Poverty, Hunger and Homelessness on Children and Youth",
                published_date="2009",
                year=2009,
                url="https://www.apa.org/pi/families/poverty",
                raw_cite=paragraphs[52]["text"],
            ),
            tags=[
                ("abolition", "Abolition"),
                ("poverty-impact", "Poverty Impact"),
                ("child-welfare", "Child Welfare / CPS"),
            ],
        ),
        # CARD 6 — Samenow on the crime/poverty cycle
        dict(
            tag_idx=56, cite_idx=57, body_range=range(58, 63),
            source=SourceData(
                cite_short="Samenow 14",
                author_last="Samenow",
                author_full="Stanton Samenow",
                qualifications=(
                    "Joined the Program for the Investigation of Criminal Behavior "
                    "at St. Elizabeth's Hospital in Washington, D.C. From 1970 until "
                    "June 1978, clinical research psychologist for that program."
                ),
                publication="Psychology Today",
                title="Crime causes Poverty",
                published_date="2014",
                year=2014,
                url="https://www.psychologytoday.com/us/blog/inside-the-criminal-mind/201412/crime-causes-poverty",
                raw_cite=paragraphs[57]["text"],
            ),
            tags=[
                ("abolition", "Abolition"),
                ("poverty-impact", "Poverty Impact"),
                ("root-causes", "Root Causes"),
            ],
        ),
        # CARD 7 — Samuel/Vox on mental health response
        dict(
            tag_idx=64, cite_idx=65, body_range=range(66, 69),
            source=SourceData(
                cite_short="Samuel 20",
                author_last="Samuel",
                author_full="Sigal Samuel",
                qualifications=(
                    "Staff Writer for Vox's Future Perfect; previously Religion "
                    "Editor at The Atlantic."
                ),
                publication="Vox",
                title=(
                    "Calling the cops on someone with mental illness can go terribly "
                    "wrong. Here's a better idea."
                ),
                published_date="2020-06-15 (accessed 2020-06-23)",
                year=2020,
                url="https://www.vox.com/future-perfect/2019/7/1/20677523/mental-health-police-cahoots-oregon-oakland-sweden",
                raw_cite=paragraphs[65]["text"],
            ),
            tags=[
                ("abolition", "Abolition"),
                ("police-abolition", "Police Abolition"),
                ("defund-police", "Defund Police"),
                ("divestment-reinvestment", "Divestment / Reinvestment"),
                ("social-workers-solve", "Social Workers Solve"),
                ("mental-health-response", "Mental Health Response"),
            ],
        ),
        # CARD 8 — Lyons/CTMirror on social workers as first responders
        dict(
            tag_idx=69, cite_idx=70, body_range=range(71, 73),
            source=SourceData(
                cite_short="Lyons 6/12",
                author_last="Lyons",
                author_full="Kelan Lyons",
                qualifications=(
                    "Covering mental health + criminal justice for CT Mirror via "
                    "Report4America. Paired with a data editor and supported by the "
                    "Connecticut Mirror's health care and justice reporters to "
                    "explore the intersection of mental health and criminal justice. "
                    "Was a fellow at City Bureau in Chicago, where he produced an "
                    "audio documentary about police misconduct settlements. Majored "
                    "in psychology at University of Pittsburgh; M.S. from "
                    "Northwestern University, where he was an Alfred Balk "
                    "scholarship recipient."
                ),
                publication="CT Mirror",
                title="Should police be social workers",
                published_date="2020-06-12",
                year=2020,
                url="https://ctmirror.org/2020/06/12/should-police-be-social-workers-reimagining-their-role-in-responding-to-mental-health-crises/",
                raw_cite=paragraphs[70]["text"],
            ),
            tags=[
                ("abolition", "Abolition"),
                ("police-abolition", "Police Abolition"),
                ("defund-police", "Defund Police"),
                ("divestment-reinvestment", "Divestment / Reinvestment"),
                ("social-workers-solve", "Social Workers Solve"),
                ("mental-health-response", "Mental Health Response"),
            ],
        ),
        # CARD 9 — Cohen/GQ on CAHOOTS empirics
        dict(
            tag_idx=73, cite_idx=74, body_range=range(75, 77),
            source=SourceData(
                cite_short="Cohen 6-12",
                author_last="Cohen",
                author_full="Danielle Cohen",
                qualifications="GQ's Editorial Business Assistant.",
                publication="GQ",
                title="Here's How a 911 Call Without Police Could Work",
                published_date="2020-06-12 (accessed 2020-06-23)",
                year=2020,
                url="https://www.gq.com/story/how-a-911-call-without-police-could-work",
                raw_cite=paragraphs[74]["text"],
            ),
            tags=[
                ("abolition", "Abolition"),
                ("police-abolition", "Police Abolition"),
                ("social-workers-solve", "Social Workers Solve"),
                ("mental-health-response", "Mental Health Response"),
            ],
        ),
    ]

    with session_scope() as s:
        for n, c in enumerate(cards, start=5):
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
