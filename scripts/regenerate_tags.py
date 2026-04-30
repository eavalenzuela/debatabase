"""Regenerate parser-noise card tags via Haiku.

Targets a narrow set of cards whose `tag` is genuinely unreadable
(parser sentinels like `<<inserted for reference>>`, all-caps
block-path concatenations like `2NC---T---NON-MILITARY`, and similar).

NOT targeted:
- Tags using `---` as a debater-stylistic clause separator
  (e.g. "It causes proliferation---goes nuclear."). Those are
  readable to debaters even if visually noisy.
- Tags with leading numeric subpoint markers ("1.", "2)", etc.).
  Those are dedup-demoted but still readable.

Two modes:
  --dry-run     Build a JSON proposal of tag rewrites. No DB writes.
  --apply REPORT  Apply rewrites; clear tag_markup since the old
                spans no longer correspond to the new tag text.

Usage:
    uv run python -m scripts.regenerate_tags --dry-run --out /tmp/tagrx.json
    uv run python -m scripts.regenerate_tags --apply /tmp/tagrx.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import text as sqltext

from debatabase.db import session_scope


LLM_MODEL = "claude-haiku-4-5-20251001"
LLM_MAX_PARALLEL = 8
BODY_EXCERPT_CHARS = 1200

# Heuristic filters for "genuinely unreadable" tags.
CANDIDATE_SQL = """
    SELECT c.id, c.tag, c.card_text, s.cite_short, s.author_full
    FROM cards c
    JOIN sources s ON s.id = c.source_id
    WHERE c.canonical_card_id IS NULL
      AND (
            c.tag LIKE '%<<%'
         OR c.tag ILIKE '%inserted for reference%'
         OR c.tag ~ '^[A-Z0-9 ]{3,15}---'
      )
"""


SYSTEM_PROMPT = (
    "You are cleaning up debate evidence card tags. The given tag is "
    "parser-broken — it contains sentinel markers like `<<inserted for "
    "reference>>` or block-path leakage like `2NC---T---NON-MILITARY`. "
    "Read the card body and write a single clean tag (one short "
    "sentence) that summarizes the argument. The tag should be what a "
    "debater would write above this card.\n\n"
    "Constraints:\n"
    "- Output a single line, no leading subpoint markers (`1.`, `[c]`).\n"
    "- Aim for 4–25 words.\n"
    "- Do not invent claims not in the body. If the body is unclear or "
    "  doesn't support a confident summary, return `\"skip\": true`.\n\n"
    "Respond ONLY with JSON: {\"tag\": \"...\"} or {\"skip\": true, "
    "\"reason\": \"...\"}. No prose outside the JSON."
)


def _llm_rewrite(client, row: dict[str, Any]) -> dict[str, Any]:
    """Returns: {'new_tag': str} | {'skip': True, 'reason': str} | {'error': str}"""
    excerpt = (row["card_text"] or "")[:BODY_EXCERPT_CHARS]
    user = (
        f"Cite: {row['cite_short']}\n"
        f"Existing (broken) tag: {row['tag']}\n\n"
        f"Card body excerpt:\n{excerpt}"
    )
    try:
        msg = client.messages.create(
            model=LLM_MODEL,
            max_tokens=200,
            system=[
                {
                    "type": "text",
                    "text": SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[{"role": "user", "content": user}],
        )
        text = "".join(
            b.text for b in msg.content if getattr(b, "type", None) == "text"
        ).strip()
    except Exception as e:
        return {"error": repr(e)}

    match = re.search(r"\{[^{}]*\}", text, flags=re.DOTALL)
    if not match:
        return {"error": "no JSON in response", "raw": text[:200]}
    try:
        parsed = json.loads(match.group(0))
    except json.JSONDecodeError as e:
        return {"error": f"json parse: {e}", "raw": text[:200]}

    if parsed.get("skip"):
        return {"skip": True, "reason": parsed.get("reason", "")}
    new_tag = parsed.get("tag")
    if not isinstance(new_tag, str) or not new_tag.strip():
        return {"error": "missing tag in response"}
    return {"new_tag": new_tag.strip()}


def cmd_dry_run(args: argparse.Namespace) -> int:
    from debatabase.config import settings
    if not settings.anthropic_api_key:
        print("ANTHROPIC_API_KEY not set", file=sys.stderr)
        return 2
    from anthropic import Anthropic

    with session_scope() as s:
        rows = [dict(r._mapping) for r in s.execute(sqltext(CANDIDATE_SQL)).all()]

    print(f"candidates: {len(rows)}")
    if args.limit is not None:
        rows = rows[: args.limit]
        print(f"capped to: {len(rows)}")
    if not rows:
        return 0

    client = Anthropic(api_key=settings.anthropic_api_key)
    proposals: list[dict[str, Any]] = []
    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=LLM_MAX_PARALLEL) as ex:
        futures = {ex.submit(_llm_rewrite, client, r): r for r in rows}
        for i, fut in enumerate(as_completed(futures), 1):
            r = futures[fut]
            result = fut.result()
            proposals.append({
                "card_id": r["id"],
                "old_tag": r["tag"],
                "cite_short": r["cite_short"],
                **result,
            })
            if i % 50 == 0:
                rate = i / (time.monotonic() - started) if i else 0
                print(f"  {i}/{len(rows)} rate={rate:.1f}/s")

    n_rewrite = sum(1 for p in proposals if "new_tag" in p)
    n_skip = sum(1 for p in proposals if p.get("skip"))
    n_err = sum(1 for p in proposals if "error" in p)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "summary": {
            "candidates": len(rows),
            "rewrite": n_rewrite,
            "skip": n_skip,
            "error": n_err,
        },
        "proposals": proposals,
    }
    Path(args.out).write_text(json.dumps(payload, indent=2))
    print(
        f"wrote {args.out}\n"
        f"  rewrite={n_rewrite}  skip={n_skip}  error={n_err}"
    )

    if args.sample > 0 and n_rewrite:
        import random
        random.seed(0)
        rewrites = [p for p in proposals if "new_tag" in p]
        sample = random.sample(rewrites, min(args.sample, len(rewrites)))
        print(f"\n--- random sample of {len(sample)} rewrites ---")
        for p in sample:
            print(f"\n  #{p['card_id']} [{p['cite_short']}]")
            print(f"    OLD: {p['old_tag'][:120]}")
            print(f"    NEW: {p['new_tag'][:120]}")
    return 0


def cmd_apply(args: argparse.Namespace) -> int:
    payload = json.loads(Path(args.apply).read_text())
    rewrites = [p for p in payload["proposals"] if "new_tag" in p]
    print(f"applying {len(rewrites)} tag rewrites")
    if not rewrites:
        return 0
    n_updated = 0
    with session_scope() as s:
        for p in rewrites:
            r = s.execute(
                sqltext(
                    "UPDATE cards SET tag = :t, tag_markup = '[]'::jsonb "
                    "WHERE id = :id"
                ),
                {"t": p["new_tag"], "id": p["card_id"]},
            )
            n_updated += r.rowcount or 0
    print(f"done. updated {n_updated} cards.")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--apply", default=None, metavar="REPORT")
    ap.add_argument("--out", default="tagrx.json")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--sample", type=int, default=12)
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
