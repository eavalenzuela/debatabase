"""Automatically pick canonicals for near-duplicate clusters.

Two modes:
  --dry-run        Score every cluster, write a JSON report. No DB writes.
  --apply REPORT   Read a dry-run report; run the canonical-set UPDATEs.

The split is deliberate: the user spot-checks the JSON before any
write hits the DB.

Scoring
-------
Per-card:
  tag_score   — favors real-sentence shape, penalizes block-path leakage,
                ALL CAPS, and tags identical to the source filename.
  cite_score  — favors `Author Year` shape, penalizes missing year and
                section-heading-like fragments.
  prov_bonus  — small bonus for cards from the user's personal corpus
                (wiki_upload_id IS NULL) since hand-cut tags are usually
                cleaner than auto-extracted ones.

Per-cluster decision rules:
  apply  — winner.score >= runner_up.score * MARGIN_FACTOR
           AND min Jaccard(winner.markup, loser.markup) >= JACCARD_FLOOR
  skip   — otherwise (cluster is left for manual review)

The Jaccard guard is the important one: it preserves clusters where
alternate cuttings highlight different sentences (a real pedagogical
signal) — those go to the manual /admin/duplicates UI instead.

Usage
-----
  uv run python -m scripts.auto_dedup --dry-run --out /tmp/dedup-report.json
  # spot-check the JSON
  uv run python -m scripts.auto_dedup --apply /tmp/dedup-report.json

  # Optional LLM tiebreaker pass between dry-run and apply: takes a
  # report, calls Haiku for ambiguous skips (margin-tight + middling
  # winner score), writes a new report with those promoted to apply.
  uv run python -m scripts.auto_dedup --llm-fill /tmp/dedup-report.json \\
      --out /tmp/dedup-report-llm.json
"""

from __future__ import annotations

import argparse
import json
import random
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
import sys
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import text as sqltext

from debatabase.db import session_scope
from debatabase.dedup import (
    ClusterMember,
    DEFAULT_THRESHOLD,
    find_clusters,
)


# Tunables — keep close to the documented defaults so the dry-run JSON
# can be reasoned about reproducibly.
MARGIN_FACTOR = 1.10
JACCARD_FLOOR = 0.80
# Absolute floor on the winner's score: catches "all candidates are
# parser garbage" clusters (e.g. every tag is block-path leakage) that
# would otherwise pass the relative-margin check just because all
# candidates are equally bad. Sends the cluster to manual review.
WINNER_SCORE_FLOOR = 0.50
# Tag-token Jaccard between cards. Used both:
#   (a) to split each embedding-similarity "coarse" cluster into
#       tag-coherent sub-clusters (connected components at >=floor),
#   (b) inside a sub-cluster, as a sanity check between the winner
#       and each loser.
# Same floor for both — using one threshold for "considered the same
# argument" is simpler than two and gives identical false-positive rate.
TAG_TOKEN_JACCARD_FLOOR = 0.30
# When tags within a sub-cluster are essentially identical
# (max pairwise tag-token Jaccard >= this), drop the markup-span
# Jaccard requirement to TIGHT_MARKUP_JACCARD_FLOOR. Rationale:
# identical tags + different markup is "alt-cuts of the same argument
# with different highlighting choices" — pedagogically captured by
# the alt-cuts sidebar, so safe to canonicalize.
TAG_IDENTICAL_THRESHOLD = 0.85
TIGHT_MARKUP_JACCARD_FLOOR = 0.50
# Bypass the relative-margin check when the winner's absolute score is
# this high. Rationale: a tight margin between two candidates each
# scoring 0.55+ means both candidates are at least passable (sentence-
# shape tag OR clean cite) — any consistent pick (we use lowest
# card_id) is fine. The margin check exists to prevent crowning a
# slightly-less-bad bad candidate, not to refuse to choose between two
# acceptable ones. Lowered from 0.65 -> 0.55 in v3 after observing
# that the dominant skip pile is byte-identical-tag clusters scoring
# in 0.55-0.65 territory (Khrushcheva, Rochowicz, Carliner ...).
GOOD_ENOUGH_FLOOR = 0.55


# ----- scoring -------------------------------------------------------------

# Block-path leakage in the tag, e.g. "2NC---T---Non-Military". Triple-dash
# is a strong signal the parser fell through to a heading rather than a
# real H4 tag.
_BLOCKPATH_TAG = re.compile(r"---")

# Bracket prefixes like "[c] ...", "[a] ...", "[\t] ..." are parser-leaked
# structural markers (subpoint labels, tab characters) — never the
# debater's intended tag. Numeric prefixes like "1. ...", "2)..." are
# also subpoint markers we should demote, but only when leading.
_BRACKET_PREFIX = re.compile(r"^\s*\[[^\]]{0,4}\]\s")
_NUMERIC_PREFIX = re.compile(r"^\s*\d{1,2}[.)\-]\s")

# Author + year, allowing optional comma + "et al"
_AUTHOR_YEAR = re.compile(
    r"^[A-Z][A-Za-z'\-]+(\s+(et al\.?|and\s+[A-Z][A-Za-z'\-]+))?,?\s*['']?\d{2,4}"
)

# Tags that look like ALL CAPS section labels (debaters do write actual
# screaming tags occasionally, but pure all-caps is usually parser noise).
_ALL_CAPS_RUN = re.compile(r"\b[A-Z]{6,}\b")


def score_tag(tag: str, source_file: str | None) -> tuple[float, list[str]]:
    """Return (score in [0,1], list of reason fragments)."""
    reasons: list[str] = []
    score = 0.5  # neutral baseline
    if not tag:
        return 0.0, ["empty tag"]

    if _BLOCKPATH_TAG.search(tag):
        score -= 0.4
        reasons.append("block-path leakage (---) in tag")

    if _BRACKET_PREFIX.match(tag):
        score -= 0.25
        reasons.append("bracketed parser prefix")
    if _NUMERIC_PREFIX.match(tag):
        score -= 0.15
        reasons.append("numeric subpoint prefix")

    if source_file and tag.strip() == Path(source_file).stem:
        score -= 0.3
        reasons.append("tag matches source filename")

    words = tag.split()
    n = len(words)
    if 6 <= n <= 30:
        score += 0.2
        reasons.append(f"sentence-shape ({n} words)")
    elif n < 4:
        score -= 0.15
        reasons.append(f"too-short tag ({n} words)")
    elif n > 50:
        score -= 0.1
        reasons.append(f"too-long tag ({n} words)")

    if tag and tag[0].isupper():
        score += 0.05
    if tag.endswith((".", "?", "!", "”", "\"")):
        score += 0.05
        reasons.append("ends in terminal punctuation")

    caps_runs = _ALL_CAPS_RUN.findall(tag)
    if len(caps_runs) >= 2:
        score -= 0.15
        reasons.append(f"{len(caps_runs)} ALL-CAPS runs")

    return max(0.0, min(1.0, score)), reasons


def score_cite(cite_short: str | None, year: int | None) -> tuple[float, list[str]]:
    reasons: list[str] = []
    score = 0.5
    if not cite_short:
        return 0.0, ["no cite_short"]

    cite = cite_short.strip()

    if _AUTHOR_YEAR.match(cite):
        score += 0.3
        reasons.append("matches Author+Year shape")
    else:
        score -= 0.1
        reasons.append("not Author+Year shape")

    if not re.search(r"\d", cite):
        score -= 0.2
        reasons.append("no digit (likely missing year)")

    if year is not None and re.search(rf"\b{year % 100:02d}\b|\b{year}\b", cite):
        score += 0.1
        reasons.append("year matches sources.year")

    # Reward fuller author info: "Richard Carrier 22" > "Carrier 22"
    # because the longer form is unambiguous when authors share surnames.
    cite_word_count = len(re.findall(r"[A-Za-z][A-Za-z'\-]+", cite))
    if cite_word_count >= 3:
        score += 0.07
        reasons.append("multi-token author info")

    if len(cite) > 60:
        score -= 0.15
        reasons.append("cite is long (likely a heading, not a stub)")
    if len(cite) < 4:
        score -= 0.2
        reasons.append("cite trivially short")

    return max(0.0, min(1.0, score)), reasons


# ----- markup Jaccard -------------------------------------------------------

def _span_set(markup: list[dict] | None) -> set[tuple[int, int, str]]:
    if not markup:
        return set()
    return {(s["start"], s["end"], s["kind"]) for s in markup}


def jaccard(a: list[dict] | None, b: list[dict] | None) -> float:
    sa, sb = _span_set(a), _span_set(b)
    if not sa and not sb:
        return 1.0
    if not sa or not sb:
        return 0.0
    inter = len(sa & sb)
    union = len(sa | sb)
    return inter / union if union else 1.0


def markup_covers(winner: list[dict] | None, loser: list[dict] | None) -> bool:
    """True iff every loser span is fully contained inside some winner span
    of the same kind. Used as a markup-Jaccard bypass: if winner is a
    strict-or-equal superset of loser's markup, the loser is a less-cut
    version of the winner — safe to canonicalize, since the winner shows
    everything the loser does (and the loser remains accessible via the
    alt-cuts sidebar).
    """
    if not loser:
        return True  # nothing to cover
    if not winner:
        return False  # winner has no markup but loser does
    by_kind: dict[str, list[tuple[int, int]]] = {}
    for s in winner:
        by_kind.setdefault(s["kind"], []).append((s["start"], s["end"]))
    for ls in loser:
        spans = by_kind.get(ls["kind"], [])
        ls_start, ls_end = ls["start"], ls["end"]
        if not any(ws_start <= ls_start and ws_end >= ls_end for ws_start, ws_end in spans):
            return False
    return True


_STOPWORDS = frozenset(
    "a an the and or of for to in on at is are was were be been being "
    "this that these those it its their they them as but by with not no".split()
)


def _tag_tokens(tag: str) -> set[str]:
    """Lowercase content-word tokens for argument-overlap Jaccard."""
    if not tag:
        return set()
    return {
        t.lower()
        for t in re.findall(r"[A-Za-z][A-Za-z'\-]+", tag)
        if t.lower() not in _STOPWORDS and len(t) > 2
    }


def tag_token_jaccard(a: str, b: str) -> float:
    sa, sb = _tag_tokens(a), _tag_tokens(b)
    if not sa and not sb:
        return 1.0
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


# ----- main passes ----------------------------------------------------------

@dataclass
class CardScore:
    card_id: int
    tag: str
    cite_short: str
    source_id: int
    score: float
    tag_score: float
    cite_score: float
    prov_bonus: float
    reasons: list[str]


def find_source_clusters(session) -> list[list[ClusterMember]]:
    """Coarse clusters by source_id, not embedding distance.

    For each source with >= 2 still-canonical (non-deduped) cards,
    emit one cluster. The two-stage tag-token sub-clustering done
    downstream then partitions each source's cards into argument-
    coherent groups. Catches dedup pairs the embedding scan missed
    because their bodies didn't quite cross the cosine threshold
    (typos, paragraph breaks, slightly different boilerplate).
    """
    rows = session.execute(sqltext("""
        SELECT c.id, c.tag, c.source_id, s.cite_short
        FROM cards c
        JOIN sources s ON s.id = c.source_id
        WHERE c.canonical_card_id IS NULL
          AND NOT c.dedup_excluded
        ORDER BY c.source_id, c.id
    """)).all()

    by_source: dict[int, list[ClusterMember]] = {}
    for r in rows:
        by_source.setdefault(r.source_id, []).append(
            ClusterMember(
                card_id=r.id, tag=r.tag, cite_short=r.cite_short,
                source_id=r.source_id,
            )
        )
    clusters = [g for g in by_source.values() if len(g) >= 2]
    clusters.sort(key=lambda c: -len(c))
    return clusters


def hydrate_cluster(session, member_ids: list[int]) -> list[dict[str, Any]]:
    """Pull tag, source, markup, wiki provenance, year for scoring."""
    rows = session.execute(
        sqltext(
            """
            SELECT c.id, c.tag, c.markup, c.source_file, c.wiki_upload_id,
                   s.cite_short, s.year, s.id AS source_id
            FROM cards c
            JOIN sources s ON s.id = c.source_id
            WHERE c.id = ANY(:ids)
            """
        ),
        {"ids": member_ids},
    ).all()
    return [
        dict(r._mapping)  # SQLAlchemy 2.x row → mapping dict
        for r in rows
    ]


def score_card(row: dict[str, Any]) -> CardScore:
    tag_s, tag_r = score_tag(row["tag"], row["source_file"])
    cite_s, cite_r = score_cite(row["cite_short"], row["year"])
    # Provenance bonus: hand-cut > disclosed wiki upload
    prov_bonus = 0.05 if row["wiki_upload_id"] is None else 0.0
    score = tag_s * 0.55 + cite_s * 0.35 + prov_bonus + 0.05  # base float
    reasons = [f"tag: {r}" for r in tag_r] + [f"cite: {r}" for r in cite_r]
    if prov_bonus > 0:
        reasons.append("personal-corpus provenance (+0.05)")
    return CardScore(
        card_id=row["id"],
        tag=row["tag"] or "",
        cite_short=row["cite_short"] or "",
        source_id=row["source_id"],
        score=round(score, 4),
        tag_score=round(tag_s, 4),
        cite_score=round(cite_s, 4),
        prov_bonus=prov_bonus,
        reasons=reasons,
    )


def split_by_tag(rows: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Partition a coarse (embedding-similarity) cluster into tag-coherent
    sub-clusters via union-find on pairwise tag-token Jaccard.

    Two cards land in the same sub-cluster iff their tags share enough
    content-word tokens (>= TAG_TOKEN_JACCARD_FLOOR). Singleton groups
    are emitted unchanged so the caller can count "isolated" cards.
    """
    n = len(rows)
    if n <= 1:
        return [rows] if rows else []

    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    tags = [r["tag"] or "" for r in rows]
    # Pairwise — n is small (typically <20) so O(n^2) is fine.
    for i in range(n):
        for j in range(i + 1, n):
            if tag_token_jaccard(tags[i], tags[j]) >= TAG_TOKEN_JACCARD_FLOOR:
                union(i, j)

    groups: dict[int, list[dict[str, Any]]] = {}
    for i, r in enumerate(rows):
        groups.setdefault(find(i), []).append(r)
    # Stable order: by smallest card_id within each group, group order by min id
    return sorted(
        (sorted(g, key=lambda r: r["id"]) for g in groups.values()),
        key=lambda g: g[0]["id"],
    )


def decide_subcluster(
    rows: list[dict[str, Any]], coarse_cluster_id: int, sub_index: int
) -> dict[str, Any]:
    """Score + rank + apply guard rules for a single tag-coherent sub-cluster."""
    scores = [score_card(r) for r in rows]
    scores.sort(key=lambda c: (-c.score, c.card_id))

    winner = scores[0]
    runner_up = scores[1] if len(scores) > 1 else None

    by_id = {r["id"]: r for r in rows}
    losers = [c for c in scores if c.card_id != winner.card_id]
    markup_jaccards = [
        jaccard(by_id[winner.card_id]["markup"], by_id[c.card_id]["markup"])
        for c in losers
    ]
    min_markup_jaccard = min(markup_jaccards) if markup_jaccards else 1.0

    # Markup-superset shortcut: if winner's markup covers every loser's
    # markup (loser spans are subsets of winner spans), apply even when
    # Jaccard is below floor. Loser is a "less-cut" version; winner
    # already shows everything it does.
    winner_covers_all = all(
        markup_covers(by_id[winner.card_id]["markup"], by_id[c.card_id]["markup"])
        for c in losers
    )

    tag_jaccards = [tag_token_jaccard(winner.tag, c.tag) for c in losers]
    min_tag_jaccard = min(tag_jaccards) if tag_jaccards else 1.0
    max_tag_jaccard = max(tag_jaccards) if tag_jaccards else 1.0

    # Soften markup-Jaccard when tags are essentially identical (the
    # "same argument, different highlighting" pedagogical case — alt-cuts
    # sidebar preserves the markup variation, so canonicalizing is safe).
    tag_tight = max_tag_jaccard >= TAG_IDENTICAL_THRESHOLD
    markup_floor = TIGHT_MARKUP_JACCARD_FLOOR if tag_tight else JACCARD_FLOOR

    if runner_up and runner_up.score > 0:
        margin = winner.score / max(runner_up.score, 1e-6)
    else:
        margin = float("inf")

    decision = "apply"
    skip_reason: str | None = None

    if len(rows) < 2:
        decision = "skip"
        skip_reason = "singleton — no peer to canonicalize against"
    elif winner.score < WINNER_SCORE_FLOOR:
        decision = "skip"
        skip_reason = (
            f"winner score {winner.score} < absolute floor {WINNER_SCORE_FLOOR} — "
            f"all candidates are low-quality (likely parser noise)"
        )
    elif margin < MARGIN_FACTOR and winner.score < GOOD_ENOUGH_FLOOR:
        decision = "skip"
        skip_reason = (
            f"margin too tight ({margin:.2f} < {MARGIN_FACTOR}) and "
            f"winner score {winner.score} < good-enough floor {GOOD_ENOUGH_FLOOR}"
        )
    elif min_markup_jaccard < markup_floor and not winner_covers_all:
        decision = "skip"
        skip_reason = (
            f"markup Jaccard {min_markup_jaccard:.2f} < {markup_floor} "
            f"(tag_tight={tag_tight}) and winner does not cover loser markup"
        )
    elif min_tag_jaccard < TAG_TOKEN_JACCARD_FLOOR:
        # Sub-clustering should have grouped these together at the floor,
        # so a winner-vs-loser failure here is genuinely surprising —
        # keep the guard as a tripwire rather than removing it.
        decision = "skip"
        skip_reason = (
            f"tag-token Jaccard {min_tag_jaccard:.2f} < "
            f"{TAG_TOKEN_JACCARD_FLOOR} despite sub-clustering "
            f"(should not happen; investigate)"
        )

    return {
        "coarse_cluster_id": coarse_cluster_id,
        "sub_index": sub_index,
        "tag_tight": tag_tight,
        "members": [asdict(c) for c in scores],
        "winner_id": winner.card_id,
        "winner_score": winner.score,
        "runner_up_score": runner_up.score if runner_up else None,
        "margin": round(margin, 3) if margin != float("inf") else None,
        "min_markup_jaccard": round(min_markup_jaccard, 3),
        "winner_covers_all": winner_covers_all,
        "min_tag_jaccard": round(min_tag_jaccard, 3),
        "max_tag_jaccard": round(max_tag_jaccard, 3),
        "markup_floor_used": markup_floor,
        "decision": decision,
        "skip_reason": skip_reason,
    }


def decide_cluster(
    rows: list[dict[str, Any]], coarse_cluster_id: int
) -> list[dict[str, Any]]:
    """Two-stage: split a coarse cluster by tag, then decide each sub-cluster.

    Returns a flat list of per-sub-cluster decision dicts.
    """
    sub_clusters = split_by_tag(rows)
    return [
        decide_subcluster(sub, coarse_cluster_id, i)
        for i, sub in enumerate(sub_clusters)
    ]


# ----- CLI entry points -----------------------------------------------------

def cmd_dry_run(args: argparse.Namespace) -> int:
    out_path = Path(args.out)
    with session_scope() as s:
        if args.strategy == "source":
            coarse_clusters = find_source_clusters(s)
            print(f"found {len(coarse_clusters)} source-grouped clusters (>=2 visible cards)")
        else:
            coarse_clusters = find_clusters(s, threshold=args.threshold)
            print(f"found {len(coarse_clusters)} embedding clusters at threshold {args.threshold}")
        if not coarse_clusters:
            return 0

        sub_decisions: list[dict[str, Any]] = []
        for coarse_id, cluster in enumerate(coarse_clusters):
            ids = [m.card_id for m in cluster]
            rows = hydrate_cluster(s, ids)
            sub_decisions.extend(decide_cluster(rows, coarse_id))

    n_apply = sum(1 for c in sub_decisions if c["decision"] == "apply")
    n_skip = sum(1 for c in sub_decisions if c["decision"] == "skip")
    n_singleton = sum(1 for c in sub_decisions if len(c["members"]) < 2)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "strategy": args.strategy,
        "threshold": args.threshold,
        "constants": {
            "MARGIN_FACTOR": MARGIN_FACTOR,
            "JACCARD_FLOOR": JACCARD_FLOOR,
            "WINNER_SCORE_FLOOR": WINNER_SCORE_FLOOR,
            "TAG_TOKEN_JACCARD_FLOOR": TAG_TOKEN_JACCARD_FLOOR,
            "TAG_IDENTICAL_THRESHOLD": TAG_IDENTICAL_THRESHOLD,
            "TIGHT_MARKUP_JACCARD_FLOOR": TIGHT_MARKUP_JACCARD_FLOOR,
            "GOOD_ENOUGH_FLOOR": GOOD_ENOUGH_FLOOR,
        },
        "summary": {
            "coarse_clusters": len(coarse_clusters),
            "sub_clusters": len(sub_decisions),
            "singletons_dropped": n_singleton,
            "apply": n_apply,
            "skip": n_skip,
            "cards_to_hide": sum(
                len(c["members"]) - 1 for c in sub_decisions if c["decision"] == "apply"
            ),
        },
        "clusters": sub_decisions,
    }
    out_path.write_text(json.dumps(payload, indent=2))
    print(f"wrote {out_path}")
    print(
        f"  coarse={len(coarse_clusters)}  sub={len(sub_decisions)}  "
        f"apply={n_apply}  skip={n_skip}  singletons={n_singleton}  "
        f"cards-to-hide={payload['summary']['cards_to_hide']}"
    )

    if args.sample > 0 and n_apply:
        applied = [c for c in sub_decisions if c["decision"] == "apply"]
        sample = random.sample(applied, min(args.sample, len(applied)))
        print(f"\n--- random sample of {len(sample)} 'apply' decisions ---")
        for c in sample:
            w = next(m for m in c["members"] if m["card_id"] == c["winner_id"])
            print(
                f"\n  cluster #{c['coarse_cluster_id']}.{c['sub_index']} "
                f"(winner #{w['card_id']}, score {w['score']}, "
                f"margin {c['margin']}, mk_jacc {c['min_markup_jaccard']}, "
                f"tag_jacc {c['min_tag_jaccard']}, tight={c['tag_tight']}):"
            )
            print(f"    WIN  [{w['cite_short']}]  {w['tag'][:90]}")
            for m in c["members"]:
                if m["card_id"] != c["winner_id"]:
                    print(f"    hide [{m['cite_short']}]  {m['tag'][:90]}")
    return 0


def cmd_apply(args: argparse.Namespace) -> int:
    report_path = Path(args.apply)
    payload = json.loads(report_path.read_text())
    apply_clusters = [c for c in payload["clusters"] if c["decision"] == "apply"]
    print(f"applying {len(apply_clusters)} clusters from {report_path}")
    if not apply_clusters:
        return 0

    n_hidden = 0
    with session_scope() as s:
        for c in apply_clusters:
            winner_id = c["winner_id"]
            losers = [m["card_id"] for m in c["members"] if m["card_id"] != winner_id]
            if not losers:
                continue
            s.execute(
                sqltext(
                    "UPDATE cards SET canonical_card_id = :w "
                    "WHERE id = ANY(:losers) AND canonical_card_id IS DISTINCT FROM :w"
                ),
                {"w": winner_id, "losers": losers},
            )
            s.execute(
                sqltext(
                    "UPDATE cards SET canonical_card_id = NULL WHERE id = :w"
                ),
                {"w": winner_id},
            )
            n_hidden += len(losers)
    print(f"done. hid {n_hidden} cards across {len(apply_clusters)} clusters.")
    return 0


# ----- LLM tiebreaker -------------------------------------------------------
#
# For sub-clusters the heuristic skipped because of a tight margin AND a
# merely-passable winner score, ask Haiku to pick. We ONLY rewrite the
# `decision` and `winner_id` fields; the markup-Jaccard / tag-Jaccard /
# winner-floor guards still gate elsewhere. Cost: ~$1-3 per full pass at
# Haiku 4.5 rates with prompt caching on the system prompt.

LLM_MODEL = "claude-haiku-4-5-20251001"
LLM_MAX_PARALLEL = 8

LLM_SYSTEM_PROMPT = (
    "You are reviewing a cluster of debate evidence cards that the system "
    "has identified as near-duplicates of each other (same source article, "
    "same argument). Your job is to pick the SINGLE best card to be the "
    "canonical version. The rest will be hidden from search but still "
    "accessible.\n\n"
    "Quality criteria, in priority order:\n"
    "1. TAG QUALITY — prefer a clear, readable tag line that reads like a "
    "real debate tag. Penalize parser noise: triple-dash separators "
    "(`---`), bracketed prefixes (`[c]`, `[\\t]`), block-path leakage, "
    "leading subpoint markers (`1.`, `2)`, `b.`), gratuitous ALL CAPS, "
    "leading filler like 'This is offense.' or 'AT:'.\n"
    "2. CITE QUALITY — prefer cites in `Author Year` form (e.g. "
    "`Roberts 19`, `Garza et al. 16`). Demote bracket-laden or filename-"
    "looking cites.\n"
    "3. If multiple candidates are equally good, pick the LOWEST card_id "
    "(oldest ingest) — the choice is arbitrary so be deterministic.\n\n"
    "Respond ONLY with JSON: {\"winner_id\": <int>, \"reason\": \"<one "
    "short sentence>\"}. No prose outside the JSON."
)


def _llm_format_candidates(members: list[dict[str, Any]]) -> str:
    lines = []
    for m in members:
        lines.append(
            f"- card_id={m['card_id']}  "
            f"cite=\"{m['cite_short']}\"  "
            f"tag=\"{m['tag']}\""
        )
    return "\n".join(lines)


def _llm_pick_canonical(
    client, members: list[dict[str, Any]]
) -> tuple[int, str] | None:
    """Call Haiku with the cluster's candidates; return (winner_id, reason)
    or None on parse/API failure."""
    try:
        # Anthropic SDK is imported lazily so non-LLM modes don't pay the
        # import cost or require the package at install time for users
        # only running --dry-run/--apply.
        msg = client.messages.create(
            model=LLM_MODEL,
            max_tokens=200,
            system=[
                {
                    "type": "text",
                    "text": LLM_SYSTEM_PROMPT,
                    # Cache the system prompt so each subsequent call in
                    # the same run only pays the input-token cost on the
                    # candidate list.
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[
                {
                    "role": "user",
                    "content": (
                        f"Candidates ({len(members)}):\n"
                        + _llm_format_candidates(members)
                        + "\n\nPick the canonical."
                    ),
                }
            ],
        )
        text = "".join(
            b.text for b in msg.content if getattr(b, "type", None) == "text"
        ).strip()
    except Exception as e:
        print(f"  LLM call failed: {e!r}", file=sys.stderr)
        return None

    # Lenient JSON extraction (mirrors tagger.py)
    match = re.search(r"\{[^{}]*\}", text, flags=re.DOTALL)
    if not match:
        return None
    try:
        parsed = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    wid = parsed.get("winner_id")
    if not isinstance(wid, int):
        return None
    valid = {m["card_id"] for m in members}
    if wid not in valid:
        return None
    reason = parsed.get("reason") or ""
    return wid, str(reason)


def _is_llm_eligible(c: dict[str, Any]) -> bool:
    """Sub-cluster is eligible for LLM if the heuristic skipped on margin
    AND the winner score is at the lower bound (between WINNER_SCORE_FLOOR
    and GOOD_ENOUGH_FLOOR — clusters above the good-enough floor are
    already auto-applied by the heuristic)."""
    if c.get("decision") != "skip":
        return False
    reason = c.get("skip_reason") or ""
    if "margin too tight" not in reason:
        return False
    w_id = c.get("winner_id")
    w = next((m for m in c.get("members", []) if m["card_id"] == w_id), None)
    if w is None:
        return False
    return WINNER_SCORE_FLOOR <= w["score"] < GOOD_ENOUGH_FLOOR


def cmd_llm_fill(args: argparse.Namespace) -> int:
    from debatabase.config import settings  # avoids top-level cost

    if not settings.anthropic_api_key:
        print("ANTHROPIC_API_KEY not set", file=sys.stderr)
        return 2

    from anthropic import Anthropic

    in_path = Path(args.llm_fill)
    out_path = Path(args.out)
    payload = json.loads(in_path.read_text())
    eligible = [c for c in payload["clusters"] if _is_llm_eligible(c)]
    print(f"eligible for LLM: {len(eligible)}")
    if args.llm_max is not None:
        eligible = eligible[: args.llm_max]
        print(f"capped to: {len(eligible)}")

    if not eligible:
        out_path.write_text(json.dumps(payload, indent=2))
        return 0

    client = Anthropic(api_key=settings.anthropic_api_key)

    promoted = 0
    failed = 0
    started = time.monotonic()

    def _work(c: dict[str, Any]) -> tuple[dict[str, Any], tuple[int, str] | None]:
        return c, _llm_pick_canonical(client, c["members"])

    with ThreadPoolExecutor(max_workers=LLM_MAX_PARALLEL) as ex:
        futures = [ex.submit(_work, c) for c in eligible]
        for i, fut in enumerate(as_completed(futures), 1):
            c, result = fut.result()
            if result is None:
                failed += 1
                continue
            winner_id, reason = result
            c["winner_id"] = winner_id
            c["decision"] = "apply"
            c["decided_by"] = "llm"
            c["llm_reason"] = reason
            c["skip_reason"] = None
            promoted += 1
            if i % 100 == 0:
                elapsed = time.monotonic() - started
                rate = i / elapsed if elapsed else 0
                print(
                    f"  {i}/{len(eligible)}  promoted={promoted} failed={failed} "
                    f"rate={rate:.1f}/s"
                )

    # Update summary
    n_apply = sum(1 for c in payload["clusters"] if c["decision"] == "apply")
    n_skip = sum(1 for c in payload["clusters"] if c["decision"] == "skip")
    payload["summary"]["apply"] = n_apply
    payload["summary"]["skip"] = n_skip
    payload["summary"]["cards_to_hide"] = sum(
        len(c["members"]) - 1
        for c in payload["clusters"]
        if c["decision"] == "apply"
    )
    payload["summary"]["llm_promoted"] = promoted
    payload["summary"]["llm_failed"] = failed

    out_path.write_text(json.dumps(payload, indent=2))
    print(
        f"wrote {out_path}\n"
        f"  promoted={promoted}  failed={failed}  "
        f"new totals: apply={n_apply}  skip={n_skip}  "
        f"cards-to-hide={payload['summary']['cards_to_hide']}"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--apply", default=None, metavar="REPORT")
    ap.add_argument(
        "--llm-fill",
        default=None,
        metavar="REPORT",
        help="run Haiku tiebreaker on margin-skipped sub-clusters; reads REPORT, writes --out",
    )
    ap.add_argument(
        "--llm-max",
        type=int,
        default=None,
        help="cap LLM calls (useful for cost-sanity testing)",
    )
    ap.add_argument(
        "--strategy",
        choices=("embedding", "source"),
        default="embedding",
        help=(
            "embedding: cluster by body cosine similarity (default). "
            "source: cluster by source_id — finds dedup pairs the "
            "embedding scan missed because bodies don't quite cross "
            "the cosine threshold (typos, slightly different paragraph "
            "context). Same two-stage tag sub-clustering applies."
        ),
    )
    ap.add_argument(
        "--threshold",
        type=float,
        default=DEFAULT_THRESHOLD,
        help=f"cosine-distance cutoff for embedding strategy (default {DEFAULT_THRESHOLD})",
    )
    ap.add_argument(
        "--out",
        default="dedup-report.json",
        help="output path for --dry-run or --llm-fill",
    )
    ap.add_argument(
        "--sample",
        type=int,
        default=15,
        help="print N random 'apply' decisions for spot-check (0 disables)",
    )

    args = ap.parse_args(argv)

    modes = sum(1 for x in (args.dry_run, args.apply, args.llm_fill) if x)
    if modes > 1:
        print("--dry-run / --apply / --llm-fill are mutually exclusive", file=sys.stderr)
        return 2
    if args.apply:
        return cmd_apply(args)
    if args.dry_run:
        return cmd_dry_run(args)
    if args.llm_fill:
        return cmd_llm_fill(args)

    print("specify --dry-run, --apply REPORT, or --llm-fill REPORT", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
