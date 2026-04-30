"""Merge `sources` rows that describe the same article.

The parser produces multiple sources rows per article when debaters cite
the same work in slightly different ways: "Roberts 19" vs "Dorothy E.
Roberts 2019", "Smith 2024" with two different URLs because one
debater pasted from JSTOR and another from the publisher PDF, etc.
This fragments source-grouped dedup and clutters /sources/{id}.

Conservative merge rule (Tier 1, auto):
  same normalized author_last (>= 4 chars) AND
  same year AND
  (same normalized URL OR same normalized title)

Anything weaker stays unmerged. Two-char "author_last" values like
'dr' and 'ac' are parser failures, not shared authors — the >= 4 char
floor excludes them. Same-(author, year) without url/title corroborator
is too weak (multiple Smiths can publish in the same year).

Two modes:
  --dry-run        Build a JSON proposal of merge groups. No writes.
  --apply REPORT   Apply: re-point cards.source_id to the canonical
                   source per group, delete the losers.

Per group, canonical = the source with most cards pointing at it,
tiebreak by longest author_full (most complete metadata), then lowest id.

Usage:
    uv run python -m scripts.canonicalize_sources --dry-run --out /tmp/srcmerge.json
    uv run python -m scripts.canonicalize_sources --apply /tmp/srcmerge.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import text as sqltext

from debatabase.db import session_scope


MIN_AUTHOR_LEN = 4

# Same-key identifier rules. We bucket sources by (norm_author, year)
# first; within each bucket, we further partition by url-or-title key.
_NORMALIZE_PATTERN = re.compile(r"[^a-z0-9]+")


def normalize(s: str | None) -> str:
    if not s:
        return ""
    return _NORMALIZE_PATTERN.sub("", s.lower())


def normalize_url(u: str | None) -> str:
    """Strip trailing slashes, fragments, and trivial whitespace."""
    if not u:
        return ""
    u = u.strip().lower()
    u = u.split("#", 1)[0]
    u = u.rstrip("/")
    return u


def find_merge_groups(session) -> list[dict[str, Any]]:
    rows = session.execute(sqltext(
        """
        SELECT id, cite_short, author_last, author_full, qualifications,
               publication, title, year, url, raw_cite,
               (SELECT count(*) FROM cards WHERE source_id = sources.id) AS n_cards
        FROM sources
        WHERE author_last IS NOT NULL AND year IS NOT NULL
        """
    )).all()

    # Bucket by (norm_author, year), then split by url-or-title corroborator
    by_key: dict[tuple[str, int], list[dict[str, Any]]] = {}
    for r in rows:
        au = normalize(r.author_last)
        if len(au) < MIN_AUTHOR_LEN:
            continue
        by_key.setdefault((au, r.year), []).append(dict(r._mapping))

    groups: list[dict[str, Any]] = []
    for (au, year), items in by_key.items():
        if len(items) < 2:
            continue
        # Now subdivide by url or title corroborator.
        # Priority: URL match > title match. Items get fingerprints.
        # An item can land in multiple sub-keys if it has both url and
        # title — but we want one merge per item, so prefer URL match
        # when available, fall back to title.
        sub: dict[str, list[dict[str, Any]]] = {}
        for it in items:
            url_n = normalize_url(it["url"])
            title_n = normalize(it["title"])
            key: str | None = None
            if url_n:
                key = f"url:{url_n}"
            elif title_n:
                key = f"title:{title_n}"
            if key is None:
                continue  # no corroborator → drop from merge consideration
            sub.setdefault(key, []).append(it)

        for key, group in sub.items():
            if len(group) < 2:
                continue
            # Pick canonical: most cards, then longest author_full, then lowest id
            group.sort(
                key=lambda x: (
                    -x["n_cards"],
                    -(len(x.get("author_full") or "")),
                    x["id"],
                )
            )
            canon = group[0]
            losers = group[1:]
            groups.append({
                "norm_author": au,
                "year": year,
                "match_key": key,
                "canonical_id": canon["id"],
                "canonical_cite": canon["cite_short"],
                "canonical_n_cards": canon["n_cards"],
                "losers": [
                    {
                        "id": l["id"],
                        "cite_short": l["cite_short"],
                        "n_cards": l["n_cards"],
                    }
                    for l in losers
                ],
                "total_cards_repointed": sum(l["n_cards"] for l in losers),
            })

    # Sort: largest impact (most cards repointed) first
    groups.sort(key=lambda g: -g["total_cards_repointed"])
    return groups


def cmd_dry_run(args: argparse.Namespace) -> int:
    with session_scope() as s:
        groups = find_merge_groups(s)

    n_groups = len(groups)
    n_sources_merged = sum(len(g["losers"]) for g in groups)
    n_cards_repointed = sum(g["total_cards_repointed"] for g in groups)

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "summary": {
            "groups": n_groups,
            "sources_merged_into_canonical": n_sources_merged,
            "cards_repointed": n_cards_repointed,
        },
        "groups": groups,
    }
    Path(args.out).write_text(json.dumps(payload, indent=2))
    print(
        f"wrote {args.out}\n"
        f"  groups={n_groups}  sources-to-merge={n_sources_merged}  "
        f"cards-to-repoint={n_cards_repointed}"
    )

    if args.sample > 0 and groups:
        import random
        random.seed(0)
        sample = random.sample(groups, min(args.sample, len(groups)))
        print(f"\n--- random sample of {len(sample)} groups ---")
        for g in sample:
            print(
                f"\n  canon #{g['canonical_id']} [{g['canonical_cite']}] "
                f"({g['canonical_n_cards']} cards, key={g['match_key'][:60]})"
            )
            for l in g["losers"]:
                print(f"    merge #{l['id']} [{l['cite_short']}]  ({l['n_cards']} cards)")
    return 0


def cmd_apply(args: argparse.Namespace) -> int:
    payload = json.loads(Path(args.apply).read_text())
    groups = payload["groups"]
    print(f"applying {len(groups)} merge groups")

    n_repointed = 0
    n_deleted = 0
    with session_scope() as s:
        for g in groups:
            canon_id = g["canonical_id"]
            loser_ids = [l["id"] for l in g["losers"]]
            if not loser_ids:
                continue
            r = s.execute(
                sqltext(
                    "UPDATE cards SET source_id = :c WHERE source_id = ANY(:losers)"
                ),
                {"c": canon_id, "losers": loser_ids},
            )
            n_repointed += r.rowcount or 0
            r = s.execute(
                sqltext("DELETE FROM sources WHERE id = ANY(:losers)"),
                {"losers": loser_ids},
            )
            n_deleted += r.rowcount or 0
    print(f"done. repointed {n_repointed} cards, deleted {n_deleted} sources.")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--apply", default=None, metavar="REPORT")
    ap.add_argument("--out", default="srcmerge.json")
    ap.add_argument("--sample", type=int, default=10)
    args = ap.parse_args(argv)

    if args.dry_run and args.apply:
        print("--dry-run and --apply mutually exclusive", file=sys.stderr)
        return 2
    if args.apply:
        return cmd_apply(args)
    if args.dry_run:
        return cmd_dry_run(args)
    print("specify --dry-run or --apply REPORT", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
