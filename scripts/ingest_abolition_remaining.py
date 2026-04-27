"""Batch ingest the rest of the Abolition Aff/Neg .docx — 59 cards + 3 analyticals.

This script ramps up the cadence: cite parses are auto-derived from the cite
paragraph using best-effort heuristics. raw_cite always stores the verbatim
paragraph text, so no information is lost — parsed fields can be corrected
later if needed.

Coverage:
- Aff > AT: Crime DA (AT: Sexual Assault, AT: Conflict, AT: Homicides)
- Aff > AT: Single Payer PIC
- Neg > Case (1NC blocks + ---EXT. blocks)
- Neg > A2: Nuke Terror
- Neg > Gradualism Bad
- Neg > Links (Cap K, Elections, PTX)
- Neg > Poverty Adv. CP (1NC + AT: blocks + ---EXT. blocks)
- Neg > Singlepayer PIC

The 3 analyticals all sit under H2 "Poverty Adv. CP" and answer the perm/CP
debate (Links to NB, PDCP, PDB).

Tag inference is keyword-based against the existing controlled vocabulary,
plus a handful of NEW tags that this content unambiguously requires:
  bioterror-impact, circumvention, election-da, extinction-impact,
  white-supremacy, biases, single-payer, da-defense, perm-debate,
  community-control-bad, social-workers-bad, plan-popular, plan-unpopular,
  cps-bad, immediacy-bad
The new tags will be surfaced in the post-ingest summary for user review.
"""

from __future__ import annotations

import json
import re

from debatabase.db import session_scope
from debatabase.ingest import (
    AnalyticalData,
    CardData,
    SourceData,
    get_or_create_source,
    insert_analytical,
    insert_card,
)
from debatabase.models import ContentTag

EXTRACT_PATH = "extracted/abolition_michigan.jsonl"
SOURCE_FILE = "Aff - Neg Abolition - Michigan7 2020 CCPTW.docx"

# ----- markup extraction (unchanged from earlier scripts) -------------------

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


# ----- best-effort cite auto-parser -----------------------------------------

URL_RE = re.compile(r"https?://[^\s)\]\\\"<>]+")
TITLE_RE = re.compile(r'[“"]([^“”"]{8,300})[”"]')
SHORTHAND_RE = re.compile(
    r"^\s*(?P<short>"
    r"[A-Z][\w'’\-]*(?:\s+(?:et\s+al\.?|et\.\s+al\.?|&|and)\s+[A-Z][\w'’\-]*)*"
    r"(?:\s+[A-Z][\w'’\-]*)*"
    r"(?:[,\s]+|—|–)"
    r"(?:['’]?\d{1,4}|\d{1,2}[/\-]\d{1,2}(?:[/\-]\d{2,4})?|ND)"
    r")"
)
YEAR_RE = re.compile(r"(?:^|[^0-9])(\d{4})(?:[^0-9]|$)")


def auto_parse_cite(cite_text: str) -> SourceData:
    """Best-effort parser. raw_cite is always the full verbatim text."""
    raw = cite_text.strip()

    # cite_short: leading "Name + year" chunk
    m = SHORTHAND_RE.match(raw)
    if m:
        cite_short = m.group("short").strip().rstrip(",")
    else:
        # fallback: first 4 words
        cite_short = " ".join(raw.split()[:4])

    # author_last: leading word(s) before the year/digit token
    author_match = re.match(r"^([A-Z][\w'’\-]*(?:\s+(?:et\s+al\.?|et\.\s+al\.?|&|and|of)\s+[A-Z][\w'’\-]*)*"
                            r"(?:\s+[A-Z][\w'’\-]*)*)", raw)
    author_last = author_match.group(1).strip() if author_match else cite_short.split()[0]

    # year: first 4-digit year in cite, fallback to 2-digit/'YY → 20YY
    year = None
    for ym in YEAR_RE.finditer(raw):
        y = int(ym.group(1))
        if 1900 <= y <= 2030:
            year = y
            break
    if year is None:
        m2 = re.search(r"['’](\d{2})", raw)
        if m2:
            year = 2000 + int(m2.group(1))
        else:
            m3 = re.search(r"\b(\d{1,2})[/\-](\d{1,2})[/\-]?(\d{2,4})?", raw)
            if m3 and m3.group(3):
                yy = m3.group(3)
                year = int(yy) if len(yy) == 4 else 2000 + int(yy)

    # url
    url_match = URL_RE.search(raw)
    url = url_match.group(0).rstrip(".,;)\\]") if url_match else None

    # title (longest quoted span)
    titles = TITLE_RE.findall(raw)
    title = max(titles, key=len) if titles else None

    # qualifications: best-effort — text inside the first parenthetical or bracket,
    # minus the URL and minus the title
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


# ----- tag inference --------------------------------------------------------

# Existing vocab patterns (slug, label, list of substring matchers against
# tag_text + h2 + h3 lowercased)
TAG_RULES = [
    ("abolition",                "Abolition",                          [""]),  # always applied
    ("police-abolition",         "Police Abolition",                   ["police abolition", "abolish the police", "abolition of police", "police-free", "abolish police"]),
    ("defund-police",            "Defund Police",                      ["defund", "divert police", "police budget"]),
    ("divestment-reinvestment",  "Divestment / Reinvestment",          ["divest", "reinvest", "redirect fund", "shift fund", "redirect", "redirected"]),
    ("social-workers-solve",     "Social Workers Solve",               ["social worker"]),
    ("mental-health-response",   "Mental Health Response",             ["mental health", "psych"]),
    ("incrementalism",           "Incrementalism",                     ["incremental", "gradual", "step", "first step"]),
    ("reformism-fails",          "Reformism Fails",                    ["reform fail", "reformism fail", "co-opt", "incremental fail", "gradualism fail", "reform isn't enough", "reform doesn't"]),
    ("reformism-good",           "Reformism Good",                     ["reform better", "reform key", "reform good", "reform is necessary", "reform is needed"]),
    ("poverty-impact",           "Poverty Impact",                     ["poverty"]),
    ("education-impact",         "Education Impact",                   ["education", "school", "graduation", "graduate"]),
    ("housing-impact",           "Housing Impact",                     ["housing", "homeless"]),
    ("anti-blackness",           "Anti-Blackness",                     ["black", "racial", "racism", "anti-black", "white supremac"]),
    ("neoliberalism-bad",        "Neoliberalism Bad",                  ["neoliberal"]),
    ("cap-k",                    "Capitalism K",                       ["capitalism", "capitalist"]),
    ("co-optation",              "Co-optation",                        ["co-opt", "coopt"]),
    ("root-causes",              "Root Causes",                        ["root cause"]),
    ("structural-violence",      "Structural Violence",                ["structural violence"]),
    ("community-control",        "Community Control",                  ["community", "community-based"]),
    ("community-trust",          "Community Trust",                    ["community trust"]),
    ("child-welfare",            "Child Welfare / CPS",                ["cps", "child protective", "child welfare", "familial"]),
    ("domestic-violence",        "Domestic Violence",                  ["domestic", "sexual assault"]),
    ("public-opinion",           "Public Opinion",                     ["unpopular", "popular", "polling", "poll"]),
    ("police-overreach",         "Police Overreach",                   ["police aren't", "police overreach", "lack the training"]),
    ("slavery-origins",          "Slavery Origins of Policing",        ["slave", "slavery"]),
]

# New tags this batch (will be created if first-used)
NEW_TAGS = {
    "bioterror-impact":   "Bioterror Impact",
    "circumvention":      "Circumvention",
    "election-da":        "Election DA",
    "extinction-impact":  "Extinction Impact",
    "white-supremacy":    "White Supremacy",
    "biases":             "Biases (Police/Decision-making)",
    "single-payer":       "Single-Payer Healthcare",
    "da-defense":         "DA Defense",
    "perm-debate":        "Perm / CP Theory",
    "social-workers-bad": "Social Workers Bad",
    "plan-popular":       "Plan Popular",
    "plan-unpopular":     "Plan Unpopular",
    "cps-bad":            "CPS Bad",
    "immediacy-bad":      "Immediacy Bad",
    "uniqueness":         "Uniqueness",
}


def infer_tags(tag_text: str, h2: str | None, h3: str | None) -> list[tuple[str, str]]:
    """Best-effort tag inference based on keywords + section context."""
    blob = " ".join(filter(None, [tag_text, h2, h3])).lower()
    out: list[tuple[str, str]] = []
    for slug, label, patterns in TAG_RULES:
        if any((not pat) or (pat in blob) for pat in patterns):
            # The "always" rule (empty pattern) only fires for "abolition"
            if slug == "abolition" or any(pat in blob for pat in patterns if pat):
                out.append((slug, label))

    # Section-context-driven inference
    h2l = (h2 or "").lower(); h3l = (h3 or "").lower()
    if "nuke terror" in h2l or "bioterror" in h3l or "nuclear" in (tag_text or "").lower():
        out.append(("bioterror-impact" if "bioterror" in h3l else "extinction-impact",
                    NEW_TAGS["bioterror-impact"] if "bioterror" in h3l else NEW_TAGS["extinction-impact"]))
    if "circumvention" in h3l or "circumvent" in (tag_text or "").lower():
        out.append(("circumvention", NEW_TAGS["circumvention"]))
    if "election" in h3l or "trump" in (tag_text or "").lower() or "biden" in (tag_text or "").lower():
        out.append(("election-da", NEW_TAGS["election-da"]))
        if "unpopular" in (tag_text or "").lower():
            out.append(("plan-unpopular", NEW_TAGS["plan-unpopular"]))
        elif "popular" in (tag_text or "").lower():
            out.append(("plan-popular", NEW_TAGS["plan-popular"]))
    if "uq" in h3l or "uniqueness" in h3l:
        out.append(("uniqueness", NEW_TAGS["uniqueness"]))
    if "white supremac" in (tag_text or "").lower() or "white nationalist" in (tag_text or "").lower():
        out.append(("white-supremacy", NEW_TAGS["white-supremacy"]))
    if "biases" in h3l or "bias" in (tag_text or "").lower():
        out.append(("biases", NEW_TAGS["biases"]))
    if "single payer" in h2l or "single-payer" in h2l or "single payer" in h3l.lower():
        out.append(("single-payer", NEW_TAGS["single-payer"]))
    # 1NC/2NC/2NR responses to aff impacts: DA defense
    if h2l.startswith("a2:") or h2l.startswith("at:") or h3l.startswith("a2:") or h3l.startswith("at:"):
        out.append(("da-defense", NEW_TAGS["da-defense"]))
    if "social workers part of state" in h3l or ("social workers" in (tag_text or "").lower() and "fail" in (tag_text or "").lower()):
        out.append(("social-workers-bad", NEW_TAGS["social-workers-bad"]))
    if "cps bad" in h3l or "cps fail" in (tag_text or "").lower():
        out.append(("cps-bad", NEW_TAGS["cps-bad"]))
    if "immediacy" in h3l and "key" in h3l:
        out.append(("immediacy-bad", NEW_TAGS["immediacy-bad"]))
    if "links to nb" in h3l or "pdcp" in h3l or "pdb" in h3l or "perm" in (tag_text or "").lower():
        out.append(("perm-debate", NEW_TAGS["perm-debate"]))

    # Dedupe
    seen = set()
    deduped = []
    for s, l in out:
        if s not in seen:
            seen.add(s); deduped.append((s, l))
    return deduped


# ----- mapping (rebuild items) ----------------------------------------------

def map_remaining(paragraphs):
    items = []
    current_h1 = current_h2 = current_h3 = None
    START = 462
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

        if i < START:
            i += 1
            continue

        if style == "Heading 4":
            j = i + 1
            while j < len(paragraphs) and not paragraphs[j]["text"].strip() and not paragraphs[j]["runs"]:
                j += 1
            tag_stripped = p["text"].strip()
            if tag_stripped.startswith("(") and tag_stripped.endswith(")") and "INSERT" in tag_stripped.upper():
                i += 1
                continue
            if j < len(paragraphs) and (paragraphs[j]["style"] or "") == "Heading 4":
                i += 1
                continue
            k = j + 1
            while k < len(paragraphs):
                sty = paragraphs[k]["style"] or ""
                if sty in ("Heading 1", "Heading 2", "Heading 3", "Heading 4"):
                    break
                k += 1
            next_is_heading = j < len(paragraphs) and (paragraphs[j]["style"] or "") in (
                "Heading 1","Heading 2","Heading 3","Heading 4"
            )
            kind = "analytical" if next_is_heading else "card"
            items.append({
                "kind": kind, "tag_idx": i,
                "cite_idx": j if not next_is_heading else None,
                "body_start": j + 1 if not next_is_heading else None,
                "body_end": k - 1 if not next_is_heading else None,
                "h1": current_h1, "h2": current_h2, "h3": current_h3,
            })
            i = k
            continue
        i += 1
    return items


# ----- main -----------------------------------------------------------------

# Hard-coded answer_to overrides for the 3 analyticals (P777/780/783) since
# their actual H3 contexts are AT: Links to NB / AT: PDCP / AT: PDB
ANALYTICAL_OVERRIDES = {
    777: ("Links to NB", ["abolition", "perm-debate"]),
    780: ("PDCP", ["abolition", "perm-debate"]),
    783: ("PDB", ["abolition", "perm-debate"]),
}


def main() -> None:
    paragraphs = [json.loads(line) for line in open(EXTRACT_PATH)]
    items = map_remaining(paragraphs)
    print(f"Mapped {len(items)} items "
          f"({sum(1 for x in items if x['kind']=='card')} cards, "
          f"{sum(1 for x in items if x['kind']=='analytical')} analyticals)\n")

    new_tag_slugs_seen = set()

    with session_scope() as s:
        for n, it in enumerate(items, start=1):
            tag_text, tag_raw = spans_from_runs(paragraphs[it["tag_idx"]]["runs"])
            tag_markup = merge(tag_raw)
            block_path = ["Neg" if it["h1"] == "Neg" else "Aff"]
            for h in (it["h2"], it["h3"]):
                if h:
                    block_path.append(h)

            if it["kind"] == "analytical":
                override = ANALYTICAL_OVERRIDES.get(it["tag_idx"])
                if override:
                    answer_to, force_tags = override
                else:
                    answer_to = it["h2"] or it["h3"]
                    force_tags = []
                inferred = infer_tags(tag_text, it["h2"], it["h3"])
                # dedupe with override-forced tags (using existing labels where known)
                final_tags = []
                seen = set()
                for slug in force_tags:
                    if slug not in seen:
                        seen.add(slug)
                        # Look up label
                        label = next(
                            (l for s_, l, _ in TAG_RULES if s_ == slug),
                            NEW_TAGS.get(slug, slug.replace("-", " ").title()),
                        )
                        final_tags.append((slug, label))
                for slug, label in inferred:
                    if slug not in seen:
                        seen.add(slug); final_tags.append((slug, label))

                a = insert_analytical(
                    s,
                    AnalyticalData(
                        argument=tag_text,
                        argument_markup=tag_markup,
                        answer_to=answer_to,
                        source_file=SOURCE_FILE,
                        block_path=block_path,
                    ),
                    approved_tag_slugs=final_tags,
                )
                for slug, _ in final_tags:
                    if slug in NEW_TAGS:
                        new_tag_slugs_seen.add(slug)
                print(f"  ANALYTICAL  id={a.id}  answer_to={answer_to!r}  "
                      f"tags={[t[0] for t in final_tags]}")
            else:
                # Card
                cite_text = paragraphs[it["cite_idx"]]["text"]
                source = auto_parse_cite(cite_text)
                body_text, body_markup = build_body(
                    paragraphs, range(it["body_start"], it["body_end"] + 1)
                )

                src = get_or_create_source(s, source)
                inferred_tags = infer_tags(tag_text, it["h2"], it["h3"])
                card = CardData(
                    tag=tag_text,
                    tag_markup=tag_markup,
                    card_text=body_text,
                    markup=body_markup,
                    source_file=SOURCE_FILE,
                    block_path=block_path,
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
                    f"cite_short={source.cite_short!r}"
                )

        print(f"\n--- New tags introduced this batch ({len(new_tag_slugs_seen)}) ---")
        for slug in sorted(new_tag_slugs_seen):
            print(f"  {slug}  →  {NEW_TAGS[slug]}")

        # Final DB summary
        n_cards = s.query(__import__("debatabase.models", fromlist=["Card"]).Card).count()
        n_anal = s.query(__import__("debatabase.models", fromlist=["Analytical"]).Analytical).count()
        n_src = s.query(__import__("debatabase.models", fromlist=["Source"]).Source).count()
        n_tags = s.query(ContentTag).count()
        print(f"\n--- DB after this run ---")
        print(f"  cards={n_cards}  analyticals={n_anal}  sources={n_src}  content_tags={n_tags}")


if __name__ == "__main__":
    main()
