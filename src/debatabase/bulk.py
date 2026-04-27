"""Shared auto-ingest pipeline used by bulk processing.

The functions here mirror what `ingest_jim_code.py` and
`ingest_abolition_remaining.py` do per-doc, factored out so a bulk runner can
process arbitrary .docx files uniformly.

Invariants:
- raw_cite is always preserved verbatim.
- Sub-block H4 headers (consecutive H4s with no cite between) are dropped.
- Plan-text style headers are dropped.
- Analyticals are routed to their own table.
- Tag inference is best-effort against the shared controlled vocabulary.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.orm import Session

from debatabase.ingest import (
    AnalyticalData,
    CardData,
    SourceData,
    get_or_create_source,
    insert_analytical,
    insert_card,
)
from debatabase.models import ContentTag
from debatabase.parser.extract import extract_docx, to_jsonl
from debatabase.tagger import VocabEntry, has_tagging_capability, propose_tags

# ----- markup span computation ---------------------------------------------

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


def merge_spans(raw: list[dict]) -> list[dict]:
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
    return text, merge_spans(raw)


# ----- document mapping ----------------------------------------------------

PLAN_HEADER_PATTERNS = (
    "plan text", "plan", "advocacy text", "cp text", "perm", "alt text",
    "advocacy statement",
)


@dataclass
class Item:
    kind: str  # "card" | "analytical"
    tag_idx: int
    cite_idx: int | None
    body_start: int | None
    body_end: int | None
    h1: str | None
    h2: str | None
    h3: str | None


def map_doc(paragraphs) -> list[Item]:
    """Walk a doc's paragraphs and emit a list of cards + analyticals."""
    items: list[Item] = []
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
            # Skip placeholder sentinels
            if tag_stripped.startswith("(") and tag_stripped.endswith(")") and "INSERT" in tag_stripped.upper():
                i += 1; continue
            # Trailing H4 with no following content — drop entirely
            if j >= len(paragraphs):
                i += 1; continue
            # Skip H4s that are immediately followed by another H4 (sub-block label)
            if (paragraphs[j]["style"] or "") == "Heading 4":
                i += 1; continue
            # Skip plan-text style sections
            if current_h3 and current_h3.lower().strip() in PLAN_HEADER_PATTERNS:
                i += 1; continue
            k = j + 1
            while k < len(paragraphs):
                sty = paragraphs[k]["style"] or ""
                if sty in ("Heading 1","Heading 2","Heading 3","Heading 4"):
                    break
                k += 1
            next_is_heading = (paragraphs[j]["style"] or "") in (
                "Heading 1","Heading 2","Heading 3","Heading 4")
            kind = "analytical" if next_is_heading else "card"
            items.append(Item(
                kind=kind, tag_idx=i,
                cite_idx=j if not next_is_heading else None,
                body_start=j+1 if not next_is_heading else None,
                body_end=k-1 if not next_is_heading else None,
                h1=current_h1, h2=current_h2, h3=current_h3,
            ))
            i = k; continue
        i += 1
    return items


# ----- cite auto-parser ----------------------------------------------------

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
    cite_short = (m.group("short").strip().rstrip(",") if m
                  else " ".join(raw_norm.split()[:4]))
    cite_short = re.sub(r"\s+", " ", cite_short).strip(" ,;")

    am = re.match(
        r"^([A-Z][\w'’\-]*(?:\s+(?:et\s+al\.?|et\.\s+al\.?|&|and)\s+[A-Z][\w'’\-]*)*"
        r"(?:\s+[A-Z][\w'’\-]*)*)",
        raw_norm,
    )
    author_last = am.group(1).strip(" ,") if am else (cite_short.split()[0] if cite_short else "(unknown)")
    author_last = re.sub(r"\s+and\s+", " & ", author_last)

    year = None
    for ym in YEAR_RE.finditer(raw_norm):
        y = int(ym.group(1))
        if 1900 <= y <= 2030:
            year = y; break
    if year is None:
        m2 = re.search(r"['’](\d{2})", raw_norm)
        if m2:
            yy = int(m2.group(1))
            year = 2000 + yy if yy < 50 else 1900 + yy
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
        if url: q = q.replace(url, "")
        if title: q = q.replace(f'"{title}"', "").replace(f"“{title}”", "")
        q = re.sub(r"\s+", " ", q).strip(" ,.;")
        if q: quals = q

    return SourceData(
        cite_short=cite_short or "(unknown)",
        author_last=author_last or "(unknown)",
        author_full=None,
        qualifications=quals,
        publication=None,
        title=title,
        published_date=str(year) if year else None,
        year=year,
        url=url,
        raw_cite=raw,
    )


# ----- tag inference -------------------------------------------------------

# Known controlled vocabulary slugs and their labels (for first-time creation
# during bulk; existing tags will dedupe by slug).
KNOWN_TAGS: dict[str, str] = {
    "abolition": "Abolition",
    "police-abolition": "Police Abolition",
    "anti-blackness": "Anti-Blackness",
    "neoliberalism-bad": "Neoliberalism Bad",
    "cap-k": "Capitalism K",
    "co-optation": "Co-optation",
    "structural-violence": "Structural Violence",
    "slavery-origins": "Slavery Origins of Policing",
    "perm-debate": "Perm / CP Theory",
    "da-defense": "DA Defense",
    "afropessimism": "Afropessimism",
    "fugitivity": "Fugitivity",
    "ontology": "Ontology",
    "race-as-technology": "Race as Technology",
    "algorithm-bias": "Algorithmic Bias / Predictive Policing",
    "queer-nothingness": "Queer Nothingness",
    "university-k": "University K",
    "undercommons": "Undercommons (Moten/Harney)",
    "ableism": "Ableism",
    "econ-da": "Economy DA",
    "tech-good": "Tech Good",
    "moten": "Moten",
    "topicality": "Topicality",
    "deviance-studies": "Deviance Studies",
    "afrofuturism": "Afrofuturism",
    "necropolitics": "Necropolitics",
    "biopolitics": "Biopolitics",
    "agamben": "Agamben",
    "puar": "Puar",
    "death-penalty": "Death Penalty",
    "queer-theory": "Queer Theory",
    "framework": "Framework",
    "settler-colonialism": "Settler Colonialism",
    "hollow-hope": "Hollow Hope DA",
    "fem-ir": "Fem IR",
    "ecological-linguistics": "Ecological Linguistics",
    "development-discourse": "Development Discourse",
    "indigenous": "Indigenous",
    "ice-abolition": "ICE Abolition",
    "border": "Border / Immigration",
    "reconciliation": "Reconciliation",
    "speculative-fiction": "Speculative Fiction",
    "asia-as-method": "Asia as Method",
    "joint-cp": "Joint Counterplan",
}

# Patterns: (slug, list of substring matchers in lowercased context)
KEYWORD_RULES: list[tuple[str, list[str]]] = [
    ("abolition",            ["abolition", "abolish"]),
    ("police-abolition",     ["police abolition", "abolish the police", "police-free"]),
    ("ice-abolition",        ["abolish ice", "ice abolition"]),
    ("anti-blackness",       ["anti-black", "antiblack", "blackness", "black radical", "afropessim"]),
    ("neoliberalism-bad",    ["neoliberal"]),
    ("cap-k",                ["capitalism", "capitalist"]),
    ("co-optation",          ["co-opt", "coopt", "commodif", "reified", "reification"]),
    ("structural-violence",  ["structural violence"]),
    ("slavery-origins",      ["slave patrol", "slavery origins"]),
    ("perm-debate",          ["perm", "permutation", "pdcp", "pdb"]),
    ("afropessimism",        ["afropessim"]),
    ("fugitivity",           ["fugitiv"]),
    ("ontology",             ["ontolog"]),
    ("race-as-technology",   ["race as technology", "race as a technology"]),
    ("algorithm-bias",       ["algorithm", "predictive polic", "facial recognition"]),
    ("queer-nothingness",    ["queer nothing"]),
    ("university-k",         ["university k", "undercommons"]),
    ("undercommons",         ["undercommons"]),
    ("ableism",              ["ableism", "ableis", "disabili"]),
    ("econ-da",              ["economy", "econ da", "economic decline", "recession"]),
    ("tech-good",            ["tech good", "technology is good"]),
    ("moten",                ["moten"]),
    ("topicality",           ["topicality", "t-version"]),
    ("deviance-studies",     ["deviance"]),
    ("afrofuturism",         ["afrofuturism", "afrofuturist"]),
    ("necropolitics",        ["necropolitic", "necropower", "mbembe"]),
    ("biopolitics",          ["biopolitic", "biopower"]),
    ("agamben",              ["agamben", "homo sacer", "bare life", "state of exception"]),
    ("puar",                 ["puar", "homonational"]),
    ("death-penalty",        ["death penalty", "capital punishment"]),
    ("queer-theory",         ["queer theory"]),
    ("framework",            ["framework", "fairness", "limits", "predictability"]),
    ("settler-colonialism",  ["settler colonial", "settlerism", "settler-coloni"]),
    ("hollow-hope",          ["hollow hope", "rosenberg"]),
    ("fem-ir",               ["fem ir", "feminist ir", "feminist international relations"]),
    ("ecological-linguistics", ["ecological linguistic"]),
    ("development-discourse", ["development discourse", "post-development"]),
    ("indigenous",           ["indigenous", "indigeneity"]),
    ("border",               ["immigration", "border", "deportation", "ice "]),
    ("reconciliation",       ["reconciliation"]),
    ("speculative-fiction",  ["speculative fiction", "specfic", "afrofutur"]),
    ("asia-as-method",       ["asia as method"]),
    ("joint-cp",             ["joint cp", "joint counterplan"]),
]


def infer_tags(tag_text: str, h1: str | None, h2: str | None, h3: str | None,
               filename: str | None = None) -> list[tuple[str, str]]:
    blob = " ".join(filter(None, [tag_text, h1, h2, h3, filename])).lower()
    out: list[tuple[str, str]] = []
    for slug, patterns in KEYWORD_RULES:
        if any(p in blob for p in patterns):
            out.append((slug, KNOWN_TAGS.get(slug, slug.replace("-", " ").title())))
    # AT:/A2: blocks → da-defense
    h2l = (h2 or "").lower(); h3l = (h3 or "").lower()
    if h2l.startswith("at:") or h2l.startswith("a2:") or h3l.startswith("at:") or h3l.startswith("a2:"):
        out.append(("da-defense", "DA Defense"))
    seen = set(); deduped = []
    for s, l in out:
        if s not in seen:
            seen.add(s); deduped.append((s, l))
    return deduped


# ----- block_path normalization --------------------------------------------

def normalize_h1(h1: str | None) -> str:
    if not h1:
        return "Other"
    cleaned = h1.replace("---", "").strip()
    low = cleaned.lower()
    if "negative" in low or low == "neg":
        return "Neg"
    if "affirmative" in low or low == "aff":
        return "Aff"
    return cleaned or "Other"


# ----- the bulk ingest workhorse -------------------------------------------

def ingest_docx(
    docx_path: Path,
    session: Session,
    save_jsonl_to: Path | None = None,
    *,
    wiki_upload_id: int | None = None,
    use_claude_tagger: bool | None = None,
) -> dict:
    """Process one .docx end-to-end. Returns {cards_added, analyticals_added, errors, new_tag_slugs}.

    ``wiki_upload_id`` propagates to inserted cards/analyticals so they
    link back to the disclosed file they came from. ``use_claude_tagger``
    lets the caller force-disable the Claude tagger (useful for the
    wiki ingest path, where the corpus is huge and per-card Claude
    calls would be expensive). When ``None``, falls back to the existing
    auto-detect behavior (Claude when ANTHROPIC_API_KEY is set).
    """
    paragraphs_data = extract_docx(docx_path)
    paragraphs = [
        {
            "index": p.index,
            "style": p.style,
            "text": p.text,
            "runs": [
                {
                    "text": r.text,
                    "bold": r.bold,
                    "italic": r.italic,
                    "underline": r.underline,
                    "highlight": r.highlight,
                    "shading": r.shading,
                    "font_size_pt": r.font_size_pt,
                    "style": r.style,
                }
                for r in p.runs
            ],
        }
        for p in paragraphs_data
    ]
    if save_jsonl_to:
        save_jsonl_to.parent.mkdir(parents=True, exist_ok=True)
        save_jsonl_to.write_text(to_jsonl(paragraphs_data))

    items = map_doc(paragraphs)
    source_file = docx_path.name

    cards_added = 0
    analyticals_added = 0
    new_tag_slugs: set[str] = set()
    errors: list[str] = []

    # Claude tagging is preferred when ANTHROPIC_API_KEY is set; falls
    # back to the legacy KEYWORD_RULES otherwise. Vocabulary is loaded
    # once per doc — the controlled vocabulary changes rarely enough
    # that a per-card refresh isn't worth the round-trips.
    if use_claude_tagger is None:
        use_claude = has_tagging_capability()
    else:
        use_claude = use_claude_tagger
    vocab: list[VocabEntry] = []
    if use_claude:
        rows = session.execute(
            ContentTag.__table__.select()
        ).all()
        vocab = [
            VocabEntry(slug=r.slug, label=r.label, description=r.description)
            for r in rows
        ]

    for it in items:
        try:
            tag_text, tag_raw = spans_from_runs(paragraphs[it.tag_idx]["runs"])
            tag_markup = merge_spans(tag_raw)
            block_path = [normalize_h1(it.h1)]
            for h in (it.h2, it.h3):
                if h: block_path.append(h)

            if use_claude:
                # Claude needs the body too; for analyticals we only have
                # the tag itself, which is the argument.
                if it.kind == "analytical":
                    body_for_tagger = ""
                else:
                    # Pre-extract body text just for the tagger; the
                    # canonical insert path below re-extracts with markup.
                    body_for_tagger, _ = build_body(
                        paragraphs, range(it.body_start, it.body_end + 1)
                    )
                slugs = propose_tags(tag_text, body_for_tagger, vocab)
                # Hydrate to the (slug, label) tuples the insert helpers want.
                slug_to_label = {v.slug: v.label for v in vocab}
                inferred = [(s, slug_to_label[s]) for s in slugs]
                tag_status = "proposed"
            else:
                inferred = infer_tags(
                    tag_text, it.h1, it.h2, it.h3, filename=source_file
                )
                tag_status = "approved"

            if it.kind == "analytical":
                a = insert_analytical(
                    session,
                    AnalyticalData(
                        argument=tag_text,
                        argument_markup=tag_markup,
                        answer_to=it.h2 or it.h3,
                        source_file=source_file,
                        block_path=block_path,
                    ),
                    approved_tag_slugs=inferred,
                    status=tag_status,
                    wiki_upload_id=wiki_upload_id,
                )
                analyticals_added += 1
            else:
                cite_text = paragraphs[it.cite_idx]["text"]
                source = auto_parse_cite(cite_text)
                body_text, body_markup = build_body(
                    paragraphs, range(it.body_start, it.body_end + 1)
                )
                # Skip empty-body cards (no real content)
                if not body_text.strip():
                    continue
                src = get_or_create_source(session, source)
                card = CardData(
                    tag=tag_text, tag_markup=tag_markup,
                    card_text=body_text, markup=body_markup,
                    source_file=source_file, block_path=block_path,
                )
                insert_card(
                    session, src, card, inferred,
                    status=tag_status,
                    wiki_upload_id=wiki_upload_id,
                )
                cards_added += 1
            for slug, _ in inferred:
                new_tag_slugs.add(slug)
        except Exception as e:
            errors.append(f"item @ p{it.tag_idx}: {e!r}")

    return {
        "cards_added": cards_added,
        "analyticals_added": analyticals_added,
        "new_tag_slugs": new_tag_slugs,
        "errors": errors,
    }
