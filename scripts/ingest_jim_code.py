"""Batch ingest the New Jim Code - UTNIF 2020.docx — 48 cards, 0 analyticals.

Same auto-parser approach as ingest_abolition_remaining.py. raw_cite is always
verbatim; parsed fields are best-effort. Tags are inferred from keywords against
the existing controlled vocabulary plus a handful of New Jim Code-specific tags
this doc requires.
"""

from __future__ import annotations

import json
import re

from sqlalchemy import text as sqltext

from debatabase.db import engine, session_scope
from debatabase.ingest import (
    AnalyticalData,
    CardData,
    SourceData,
    get_or_create_source,
    insert_analytical,
    insert_card,
)
from debatabase.models import Analytical, Card, ContentTag, Source

EXTRACT_PATH = "extracted/new_jim_code.jsonl"
SOURCE_FILE = "New Jim Code - UTNIF 2020.docx"


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


URL_RE = re.compile(r"https?://[^\s)\]\\\"<>]+")
TITLE_RE = re.compile(r'[“"]([^“”"]{8,300})[”"]')
SHORTHAND_RE = re.compile(
    r"^\s*(?P<short>"
    r"[A-Z][\w'’\-]*(?:\s+(?:et\s+al\.?|et\.\s+al\.?|&|and)\s+[A-Z][\w'’\-]*)*"
    r"(?:\s+[A-Z][\w'’\-]*)*"
    r"(?:[,\s]+|—|–)"
    r"(?:['’]?\d{1,4}|\d{1,2}[/\-]\d{1,2}(?:[/\-]\d{2,4})?|ND|No\s+Date)"
    r")"
)
YEAR_RE = re.compile(r"(?:^|[^0-9])(\d{4})(?:[^0-9]|$)")


def auto_parse_cite(cite_text: str) -> SourceData:
    raw = cite_text.strip()
    raw_norm = raw.replace("\xa0", " ")
    m = SHORTHAND_RE.match(raw_norm)
    if m:
        cite_short = m.group("short").strip().rstrip(",")
    else:
        cite_short = " ".join(raw_norm.split()[:4])
    cite_short = re.sub(r"\s+", " ", cite_short).strip(" ,;")

    author_match = re.match(
        r"^([A-Z][\w'’\-]*(?:\s+(?:et\s+al\.?|et\.\s+al\.?|&|and)\s+[A-Z][\w'’\-]*)*"
        r"(?:\s+[A-Z][\w'’\-]*)*)",
        raw_norm,
    )
    author_last = author_match.group(1).strip(" ,") if author_match else cite_short.split()[0]
    author_last = re.sub(r"\s+and\s+", " & ", author_last)

    year = None
    for ym in YEAR_RE.finditer(raw_norm):
        y = int(ym.group(1))
        if 1900 <= y <= 2030:
            year = y
            break
    if year is None:
        m2 = re.search(r"['’](\d{2})", raw_norm)
        if m2:
            year = 2000 + int(m2.group(1)) if int(m2.group(1)) < 50 else 1900 + int(m2.group(1))
        else:
            m3 = re.search(r"\b(\d{1,2})[/\-](\d{1,2})[/\-]?(\d{2,4})?", raw_norm)
            if m3 and m3.group(3):
                yy = m3.group(3)
                year = int(yy) if len(yy) == 4 else 2000 + int(yy)

    url_match = URL_RE.search(raw)
    url = url_match.group(0).rstrip(".,;)\\]") if url_match else None

    titles = TITLE_RE.findall(raw)
    title = max(titles, key=len) if titles else None

    quals = None
    paren_match = re.search(r"[(\[]([^()\[\]]{20,1500})[)\]]", raw)
    if paren_match:
        q = paren_match.group(1)
        if url:
            q = q.replace(url, "")
        if title:
            q = q.replace(f'"{title}"', "").replace(f"“{title}”", "")
        q = re.sub(r"\s+", " ", q).strip(" ,.;")
        if q:
            quals = q

    return SourceData(
        cite_short=cite_short,
        author_last=author_last,
        author_full=None,
        qualifications=quals,
        publication=None,
        title=title,
        published_date=str(year) if year else None,
        year=year,
        url=url,
        raw_cite=raw,
    )


# Existing vocabulary patterns (reuse from prior batches)
TAG_RULES = [
    ("abolition",                "Abolition",                          ["abolition", "abolish"]),
    ("police-abolition",         "Police Abolition",                   ["police abolition", "abolish the police", "police-free"]),
    ("anti-blackness",           "Anti-Blackness",                     ["black", "racial", "racism", "anti-black", "white supremac"]),
    ("neoliberalism-bad",        "Neoliberalism Bad",                  ["neoliberal"]),
    ("cap-k",                    "Capitalism K",                       ["capitalism", "capitalist"]),
    ("co-optation",              "Co-optation",                        ["co-opt", "coopt", "commodif", "reified", "reification"]),
    ("structural-violence",      "Structural Violence",                ["structural violence"]),
    ("slavery-origins",          "Slavery Origins of Policing",        ["slave", "slavery"]),
    ("perm-debate",              "Perm / CP Theory",                   ["perm", "permutation"]),
    ("da-defense",               "DA Defense",                         []),  # context-driven
]

# New tags this doc requires
NEW_TAGS = {
    "afropessimism":         "Afropessimism",
    "fugitivity":            "Fugitivity",
    "ontology":              "Ontology",
    "race-as-technology":    "Race as Technology",
    "algorithm-bias":        "Algorithmic Bias / Predictive Policing",
    "queer-nothingness":     "Queer Nothingness",
    "university-k":          "University K",
    "undercommons":          "Undercommons (Moten/Harney)",
    "ableism":               "Ableism",
    "econ-da":               "Economy DA",
    "tech-good":             "Tech Good",
    "moten":                 "Moten",
    "wilderson":             "Wilderson",
    "sexton":                "Sexton",
    "warren":                "Warren (Calvin)",
    "marriott":              "Marriott",
    "topicality":            "Topicality",
    "deviance-studies":      "Deviance Studies",
}


def infer_tags(tag_text: str, h1: str | None, h2: str | None, h3: str | None) -> list[tuple[str, str]]:
    blob = " ".join(filter(None, [tag_text, h2, h3])).lower()
    h1l = (h1 or "").lower()
    h2l = (h2 or "").lower()
    h3l = (h3 or "").lower()
    out: list[tuple[str, str]] = []

    for slug, label, patterns in TAG_RULES:
        if any(p in blob for p in patterns):
            out.append((slug, label))

    # Section-driven inference
    if "afropessimism" in h2l or "afropessimism" in h3l or "afropessim" in (tag_text or "").lower():
        out.append(("afropessimism", NEW_TAGS["afropessimism"]))
    if "fugitiv" in h3l or "fugitiv" in (tag_text or "").lower():
        out.append(("fugitivity", NEW_TAGS["fugitivity"]))
    if "ontolog" in (tag_text or "").lower() or "ontolog" in h3l:
        out.append(("ontology", NEW_TAGS["ontology"]))
    if "race as technology" in (tag_text or "").lower() or "race as a technology" in (tag_text or "").lower():
        out.append(("race-as-technology", NEW_TAGS["race-as-technology"]))
    if "algorith" in (tag_text or "").lower() or "predictive polic" in (tag_text or "").lower():
        out.append(("algorithm-bias", NEW_TAGS["algorithm-bias"]))
    if "queer nothing" in h2l or "queer nothing" in (tag_text or "").lower():
        out.append(("queer-nothingness", NEW_TAGS["queer-nothingness"]))
    if "university k" in h2l or "undercommons" in h3l or "undercommons" in (tag_text or "").lower():
        out.append(("university-k", NEW_TAGS["university-k"]))
    if "undercommons" in (tag_text or "").lower() or "undercommons" in h3l:
        out.append(("undercommons", NEW_TAGS["undercommons"]))
    if "ableism" in h3l or "ableis" in (tag_text or "").lower() or "disabl" in (tag_text or "").lower():
        out.append(("ableism", NEW_TAGS["ableism"]))
    if "econ" in h3l or "econom" in (tag_text or "").lower():
        out.append(("econ-da", NEW_TAGS["econ-da"]))
    if "tech good" in h3l or h3l == "tech good" or "technology is good" in (tag_text or "").lower():
        out.append(("tech-good", NEW_TAGS["tech-good"]))
    if "moten" in (tag_text or "").lower():
        out.append(("moten", NEW_TAGS["moten"]))
    if "counterinterpretation" in h2l or "topical" in (tag_text or "").lower():
        out.append(("topicality", NEW_TAGS["topicality"]))
    if "deviance" in h3l or "deviance" in (tag_text or "").lower():
        out.append(("deviance-studies", NEW_TAGS["deviance-studies"]))

    # Theory-author tags from cite shorthand (low-precision, but useful)
    if "wilderson" in (tag_text or "").lower() or "wilderson" in (h2 or "").lower():
        out.append(("wilderson", NEW_TAGS["wilderson"]))
    # Note: marriott/sexton/warren — applied if mentioned in tag, not just cited
    if "sexton" in (tag_text or "").lower():
        out.append(("sexton", NEW_TAGS["sexton"]))

    if h2l.startswith("at:") or h3l.startswith("at:") or h2l.startswith("a2:") or h3l.startswith("a2:"):
        out.append(("da-defense", "DA Defense"))

    seen = set()
    deduped = []
    for s, l in out:
        if s not in seen:
            seen.add(s); deduped.append((s, l))
    return deduped


def map_doc(paragraphs):
    items = []
    current_h1 = current_h2 = current_h3 = None
    i = 0
    while i < len(paragraphs):
        p = paragraphs[i]
        style = p["style"] or ""
        if style == "Heading 1":
            current_h1 = p["text"].strip(); current_h2 = current_h3 = None
        elif style == "Heading 2":
            current_h2 = p["text"].strip(); current_h3 = None
        elif style == "Heading 3":
            current_h3 = p["text"].strip()
        elif style == "Heading 4":
            j = i + 1
            while j < len(paragraphs) and not paragraphs[j]["text"].strip() and not paragraphs[j]["runs"]:
                j += 1
            tag_stripped = p["text"].strip()
            if tag_stripped.startswith("(") and tag_stripped.endswith(")") and "INSERT" in tag_stripped.upper():
                i += 1; continue
            if j < len(paragraphs) and (paragraphs[j]["style"] or "") == "Heading 4":
                i += 1; continue
            k = j + 1
            while k < len(paragraphs):
                sty = paragraphs[k]["style"] or ""
                if sty in ("Heading 1","Heading 2","Heading 3","Heading 4"): break
                k += 1
            next_is_heading = j < len(paragraphs) and (paragraphs[j]["style"] or "") in (
                "Heading 1","Heading 2","Heading 3","Heading 4")
            kind = "analytical" if next_is_heading else "card"
            items.append({
                "kind": kind, "tag_idx": i,
                "cite_idx": j if not next_is_heading else None,
                "body_start": j+1 if not next_is_heading else None,
                "body_end": k-1 if not next_is_heading else None,
                "h1": current_h1, "h2": current_h2, "h3": current_h3,
            })
            i = k; continue
        i += 1
    return items


def main() -> None:
    paragraphs = [json.loads(line) for line in open(EXTRACT_PATH)]
    items = map_doc(paragraphs)
    print(f"Mapped {len(items)} items "
          f"({sum(1 for x in items if x['kind']=='card')} cards, "
          f"{sum(1 for x in items if x['kind']=='analytical')} analyticals)\n")

    new_tag_slugs_seen = set()

    with session_scope() as s:
        for n, it in enumerate(items, start=1):
            tag_text, tag_raw = spans_from_runs(paragraphs[it["tag_idx"]]["runs"])
            tag_markup = merge(tag_raw)
            # block_path: H1 normalized to "Aff" or "Neg" if it matches; else verbatim
            h1 = it["h1"] or ""
            if "negative" in h1.lower():
                bp_h1 = "Neg"
            elif "affirmative" in h1.lower() or "1ac" in (it["h2"] or "").lower():
                bp_h1 = "Aff"
            else:
                bp_h1 = h1.replace("---", "").strip() or "Other"
            block_path = [bp_h1]
            for h in (it["h2"], it["h3"]):
                if h:
                    block_path.append(h)

            if it["kind"] == "analytical":
                a = insert_analytical(
                    s,
                    AnalyticalData(
                        argument=tag_text,
                        argument_markup=tag_markup,
                        answer_to=it["h2"] or it["h3"],
                        source_file=SOURCE_FILE,
                        block_path=block_path,
                    ),
                    approved_tag_slugs=infer_tags(tag_text, it["h1"], it["h2"], it["h3"]),
                )
                print(f"  ANALYTICAL  id={a.id}  block_path={block_path}")
            else:
                cite_text = paragraphs[it["cite_idx"]]["text"]
                source = auto_parse_cite(cite_text)
                body_text, body_markup = build_body(
                    paragraphs, range(it["body_start"], it["body_end"] + 1)
                )
                src = get_or_create_source(s, source)
                inferred_tags = infer_tags(tag_text, it["h1"], it["h2"], it["h3"])
                card = CardData(
                    tag=tag_text, tag_markup=tag_markup,
                    card_text=body_text, markup=body_markup,
                    source_file=SOURCE_FILE, block_path=block_path,
                )
                inserted = insert_card(s, src, card, inferred_tags)
                for slug, _ in inferred_tags:
                    if slug in NEW_TAGS:
                        new_tag_slugs_seen.add(slug)
                nu = sum(1 for x in body_markup if x["kind"] == "underline")
                nh = sum(1 for x in body_markup if x["kind"] == "highlight")
                print(
                    f"  card #{n:3d}  src={src.id:3d}  card.id={inserted.id:3d}  "
                    f"body={len(body_text):6d}c  u={nu:2d}/h={nh:2d}  "
                    f"tags={len(inferred_tags)}  "
                    f"cite_short={source.cite_short!r}  bp={block_path}"
                )

        print(f"\n--- New tags introduced this batch ({len(new_tag_slugs_seen)}) ---")
        for slug in sorted(new_tag_slugs_seen):
            print(f"  {slug}  →  {NEW_TAGS[slug]}")

        print(f"\n--- DB after this run ---")
        n_cards = s.query(Card).count()
        n_anal = s.query(Analytical).count()
        n_src = s.query(Source).count()
        n_tags = s.query(ContentTag).count()
        print(f"  cards={n_cards}  analyticals={n_anal}  sources={n_src}  content_tags={n_tags}")


if __name__ == "__main__":
    main()
