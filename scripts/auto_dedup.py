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
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import text as sqltext

from debatabase.db import session_scope
from debatabase.dedup import find_clusters, DEFAULT_THRESHOLD


# Tunables — keep close to the documented defaults so the dry-run JSON
# can be reasoned about reproducibly.
MARGIN_FACTOR = 1.10
JACCARD_FLOOR = 0.80
# Absolute floor on the winner's score: catches "all candidates are
# parser garbage" clusters (e.g. every tag is block-path leakage) that
# would otherwise pass the relative-margin check just because all
# candidates are equally bad. Sends the cluster to manual review.
WINNER_SCORE_FLOOR = 0.50
# Tag-token Jaccard between winner and each loser: catches false-
# positive embedding clusters where the bodies cluster on shared
# boilerplate but the actual *arguments* (tag lines) are different.
TAG_TOKEN_JACCARD_FLOOR = 0.30


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


def decide_cluster(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Score, rank, apply guard rules. Returns a dict for the JSON report."""
    scores = [score_card(r) for r in rows]
    scores.sort(key=lambda c: (-c.score, c.card_id))  # high score first; lowest id breaks ties

    winner = scores[0]
    runner_up = scores[1] if len(scores) > 1 else None

    # Markup Jaccard: winner vs each loser
    by_id = {r["id"]: r for r in rows}
    losers = [c for c in scores if c.card_id != winner.card_id]
    jaccards = [
        jaccard(by_id[winner.card_id]["markup"], by_id[c.card_id]["markup"])
        for c in losers
    ]
    min_jaccard = min(jaccards) if jaccards else 1.0

    # Tag-token Jaccard: winner.tag vs each loser.tag. Low values flag
    # false-positive clusters where bodies overlap on boilerplate but
    # the actual arguments are distinct.
    tag_jaccards = [
        tag_token_jaccard(winner.tag, c.tag) for c in losers
    ]
    min_tag_jaccard = min(tag_jaccards) if tag_jaccards else 1.0

    decision = "apply"
    skip_reason: str | None = None

    if runner_up and runner_up.score > 0:
        margin = winner.score / max(runner_up.score, 1e-6)
    else:
        margin = float("inf")

    if winner.score < WINNER_SCORE_FLOOR:
        decision = "skip"
        skip_reason = (
            f"winner score {winner.score} < absolute floor {WINNER_SCORE_FLOOR} — "
            f"all candidates are low-quality (likely parser noise)"
        )
    elif margin < MARGIN_FACTOR:
        decision = "skip"
        skip_reason = (
            f"margin too tight ({margin:.2f} < {MARGIN_FACTOR}) — top two are "
            f"#{winner.card_id} ({winner.score}) and #{runner_up.card_id if runner_up else '-'} "
            f"({runner_up.score if runner_up else '-'})"
        )
    elif min_jaccard < JACCARD_FLOOR:
        decision = "skip"
        skip_reason = (
            f"markup Jaccard too low ({min_jaccard:.2f} < {JACCARD_FLOOR}) — "
            f"alternate cuttings have meaningfully different highlight/underline "
            f"and would lose pedagogical signal if hidden"
        )
    elif min_tag_jaccard < TAG_TOKEN_JACCARD_FLOOR:
        decision = "skip"
        skip_reason = (
            f"tag-token Jaccard too low ({min_tag_jaccard:.2f} < "
            f"{TAG_TOKEN_JACCARD_FLOOR}) — likely a false-positive cluster "
            f"(bodies cluster on shared boilerplate but tags describe "
            f"different arguments)"
        )

    return {
        "members": [asdict(c) for c in scores],
        "winner_id": winner.card_id,
        "winner_score": winner.score,
        "runner_up_score": runner_up.score if runner_up else None,
        "margin": round(margin, 3) if margin != float("inf") else None,
        "min_jaccard": round(min_jaccard, 3),
        "min_tag_jaccard": round(min_tag_jaccard, 3),
        "decision": decision,
        "skip_reason": skip_reason,
    }


# ----- CLI entry points -----------------------------------------------------

def cmd_dry_run(args: argparse.Namespace) -> int:
    out_path = Path(args.out)
    with session_scope() as s:
        clusters = find_clusters(s, threshold=args.threshold)
        print(f"found {len(clusters)} clusters at threshold {args.threshold}")
        if not clusters:
            return 0

        report_clusters = []
        for cluster in clusters:
            ids = [m.card_id for m in cluster]
            rows = hydrate_cluster(s, ids)
            report_clusters.append(decide_cluster(rows))

    n_apply = sum(1 for c in report_clusters if c["decision"] == "apply")
    n_skip = len(report_clusters) - n_apply
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "threshold": args.threshold,
        "margin_factor": MARGIN_FACTOR,
        "jaccard_floor": JACCARD_FLOOR,
        "summary": {
            "clusters": len(report_clusters),
            "apply": n_apply,
            "skip": n_skip,
            "cards_to_hide": sum(
                len(c["members"]) - 1 for c in report_clusters if c["decision"] == "apply"
            ),
        },
        "clusters": report_clusters,
    }
    out_path.write_text(json.dumps(payload, indent=2))
    print(f"wrote {out_path}")
    print(f"  apply: {n_apply}   skip: {n_skip}   cards-to-hide: {payload['summary']['cards_to_hide']}")

    if args.sample > 0 and n_apply:
        applied = [c for c in report_clusters if c["decision"] == "apply"]
        sample = random.sample(applied, min(args.sample, len(applied)))
        print(f"\n--- random sample of {len(sample)} 'apply' decisions ---")
        for c in sample:
            w = next(m for m in c["members"] if m["card_id"] == c["winner_id"])
            print(
                f"\n  cluster (winner #{w['card_id']}, score {w['score']}, "
                f"margin {c['margin']}, jaccard {c['min_jaccard']}):"
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


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=False)

    drp = ap.add_argument_group("dry-run")
    drp = ap
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--apply", default=None, metavar="REPORT")
    ap.add_argument(
        "--threshold",
        type=float,
        default=DEFAULT_THRESHOLD,
        help=f"cosine-distance cutoff (default {DEFAULT_THRESHOLD})",
    )
    ap.add_argument(
        "--out",
        default="dedup-report.json",
        help="dry-run output path",
    )
    ap.add_argument(
        "--sample",
        type=int,
        default=15,
        help="print N random 'apply' decisions for spot-check (0 disables)",
    )

    args = ap.parse_args(argv)

    if args.dry_run and args.apply:
        print("--dry-run and --apply are mutually exclusive", file=sys.stderr)
        return 2
    if args.apply:
        return cmd_apply(args)
    if args.dry_run:
        return cmd_dry_run(args)

    print("specify --dry-run or --apply REPORT", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
