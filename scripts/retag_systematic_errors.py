"""Targeted Haiku re-tag pass for two systematically-mistagged slugs.

Empirical observation across 250+ manually-reviewed batches: the
existing tagger reflexively applies `russia-regime-fragility` to any
peer-power-instability card (China, Kazakhstan, Russia revisionism)
and `icebreaker-capacity` to any charting / hydrography / generic
Arctic-logistics card. Each affects ~1k+ cards.

Workflow
--------
For each candidate (card, slug) pair:
  1. Send Haiku the card tag-line + body excerpt + suspect pairing
     + a small vocabulary slice of plausible retag targets.
  2. Haiku returns one of:
       {"action": "keep"}                         — tag really fits, leave alone
       {"action": "retag", "slug": "<vocab>"}     — replace with a different tag
       {"action": "remove"}                       — tag doesn't fit; no clean alt
  3. Two-mode CLI: --dry-run writes JSON proposals, --apply executes.

Usage
-----
  uv run python -m scripts.retag_systematic_errors --slug russia-regime-fragility \\
      --dry-run --out /tmp/retag-russia.json
  uv run python -m scripts.retag_systematic_errors --apply /tmp/retag-russia.json
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
BODY_EXCERPT_CHARS = 1100


# Plausible retag targets for each known-mistagged slug. The LLM picks
# from this list, "keep" the original, or "remove". Keeping the menu
# tight keeps the prompt small and cuts hallucinated slugs.
RETAG_MENUS = {
    "russia-regime-fragility": [
        "russia-regime-fragility",  # keep option
        "china-regime-fragility",
        "regime-fragility",  # umbrella for other countries (Kazakhstan, Iran, NK)
        "great-power-competition",
        "hegemony-k-link",
        "arctic-militarization",
        "arctic-resource-competition",
        "russian-conventional-weakness",
        "nuclear-escalation",
        "military-escalation",
    ],
    "icebreaker-capacity": [
        "icebreaker-capacity",  # keep option
        "arctic-navigation",
        "maritime-governance",
        "arctic-militarization",
        "military-balance",
        "military-escalation",
        "resource-extraction",
        "arctic-resource-competition",
    ],
}


SYSTEM_PROMPT_TEMPLATE = (
    "You are auditing tag pairings on debate evidence cards. The current "
    "tagger has a systematic blind spot: it reflexively applies "
    "`{slug}` to many cards where it doesn't actually fit.\n\n"
    "{guidance}\n\n"
    "For each card you receive, decide one of:\n"
    "  keep   — the suspect tag really does fit this card's argument\n"
    "  retag  — pick a better-fitting tag from the menu\n"
    "  remove — no menu option fits well; just delete the bad pairing\n\n"
    "Menu of valid retag targets (you may also choose to keep or remove):\n"
    "{menu}\n\n"
    "Respond ONLY with JSON. No prose outside the JSON.\n"
    "  {{\"action\": \"keep\"}}\n"
    "  {{\"action\": \"retag\", \"slug\": \"<one menu slug>\", \"reason\": \"<short>\"}}\n"
    "  {{\"action\": \"remove\", \"reason\": \"<short>\"}}"
)

GUIDANCE = {
    "russia-regime-fragility": (
        "When the card is actually about: CHINA's CCP/Xi legitimacy → "
        "use china-regime-fragility. KAZAKHSTAN, IRAN, NORTH KOREA "
        "stability → use regime-fragility (umbrella). RUSSIA-CHINA "
        "revisionism / great-power axis → great-power-competition or "
        "hegemony-k-link. RUSSIAN MILITARY POSTURE in the Arctic without "
        "regime-collapse argument → arctic-militarization. RUSSIAN "
        "CONVENTIONAL CAPABILITY GAPS → russian-conventional-weakness. "
        "Only KEEP `russia-regime-fragility` when the card argues "
        "specifically that Putin's regime is fragile/collapsing/about-"
        "to-fall."
    ),
    "icebreaker-capacity": (
        "When the card is actually about: HYDROGRAPHIC CHARTING, "
        "navigation infrastructure, NSR/NWP routes, vessel safety → "
        "use arctic-navigation. UNCLOS / law-of-the-sea / sovereignty "
        "disputes → maritime-governance. RUSSIAN OR US MILITARY POSTURE "
        "in the Arctic generally → arctic-militarization. CAPABILITY "
        "GAPS / posture asymmetries between great powers → "
        "military-balance. Only KEEP `icebreaker-capacity` when the "
        "card is specifically about icebreaker fleet size, USCG polar "
        "icebreaker procurement, or icebreaker-shipbuilding gaps."
    ),
}


def _system_prompt(slug: str) -> str:
    menu_lines = "\n".join(f"  - {s}" for s in RETAG_MENUS[slug])
    return SYSTEM_PROMPT_TEMPLATE.format(
        slug=slug, guidance=GUIDANCE[slug], menu=menu_lines
    )


def _llm_decide(client, system_prompt: str, row: dict[str, Any]) -> dict[str, Any]:
    excerpt = (row["card_text"] or "")[:BODY_EXCERPT_CHARS]
    user = (
        f"Suspect pairing: card #{row['id']} tagged with `{row['slug']}`\n\n"
        f"Cite: {row['cite_short']}\n"
        f"Tag-line: {row['tag']}\n\n"
        f"Card body excerpt:\n{excerpt}"
    )
    try:
        msg = client.messages.create(
            model=LLM_MODEL,
            max_tokens=200,
            system=[{
                "type": "text",
                "text": system_prompt,
                "cache_control": {"type": "ephemeral"},
            }],
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
    return parsed


def _load_candidates(s, slug: str) -> list[dict[str, Any]]:
    rows = s.execute(sqltext("""
        SELECT c.id, c.tag, c.card_text, src.cite_short, :slug AS slug
        FROM card_content_tags cct
        JOIN content_tags ct ON ct.id = cct.content_tag_id
        JOIN cards c ON c.id = cct.card_id
        JOIN sources src ON src.id = c.source_id
        WHERE ct.slug = :slug
          AND c.canonical_card_id IS NULL
    """), {"slug": slug}).all()
    return [dict(r._mapping) for r in rows]


def cmd_dry_run(args: argparse.Namespace) -> int:
    from debatabase.config import settings
    if not settings.anthropic_api_key:
        print("ANTHROPIC_API_KEY not set", file=sys.stderr)
        return 2
    from anthropic import Anthropic

    if args.slug not in RETAG_MENUS:
        print(f"unknown slug; valid: {list(RETAG_MENUS)}", file=sys.stderr)
        return 2

    with session_scope() as s:
        rows = _load_candidates(s, args.slug)
    print(f"candidates for {args.slug}: {len(rows)}")
    if args.limit is not None:
        rows = rows[: args.limit]
        print(f"capped to: {len(rows)}")
    if not rows:
        return 0

    sys_prompt = _system_prompt(args.slug)
    client = Anthropic(api_key=settings.anthropic_api_key)
    proposals: list[dict[str, Any]] = []
    started = time.monotonic()

    def _work(r):
        return r, _llm_decide(client, sys_prompt, r)

    with ThreadPoolExecutor(max_workers=LLM_MAX_PARALLEL) as ex:
        futures = [ex.submit(_work, r) for r in rows]
        for i, fut in enumerate(as_completed(futures), 1):
            r, result = fut.result()
            proposals.append({
                "card_id": r["id"],
                "cite_short": r["cite_short"],
                "tag": r["tag"][:200] if r["tag"] else "",
                "current_slug": args.slug,
                **result,
            })
            if i % 100 == 0:
                rate = i / (time.monotonic() - started) if i else 0
                print(f"  {i}/{len(rows)}  rate={rate:.1f}/s")

    n_keep = sum(1 for p in proposals if p.get("action") == "keep")
    n_retag = sum(1 for p in proposals if p.get("action") == "retag")
    n_remove = sum(1 for p in proposals if p.get("action") == "remove")
    n_err = sum(1 for p in proposals if "error" in p)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "current_slug": args.slug,
        "summary": {
            "candidates": len(rows),
            "keep": n_keep, "retag": n_retag, "remove": n_remove, "error": n_err,
        },
        "proposals": proposals,
    }
    Path(args.out).write_text(json.dumps(payload, indent=2))
    print(f"wrote {args.out}")
    print(f"  keep={n_keep}  retag={n_retag}  remove={n_remove}  err={n_err}")

    if args.sample > 0 and n_retag:
        import random
        random.seed(0)
        retag_props = [p for p in proposals if p.get("action") == "retag"]
        sample = random.sample(retag_props, min(args.sample, len(retag_props)))
        # Bucket by target slug
        from collections import Counter
        buckets = Counter(p["slug"] for p in retag_props)
        print(f"\nretag target distribution:")
        for tgt, n in buckets.most_common():
            print(f"  {tgt}: {n}")
        print(f"\nrandom sample of {len(sample)} retag decisions:")
        for p in sample:
            print(f"  #{p['card_id']} [{p['cite_short'][:25]}] → {p['slug']}")
            print(f"    tag: {p['tag'][:90]}")
            print(f"    why: {p.get('reason','')[:120]}")
    return 0


def cmd_apply(args: argparse.Namespace) -> int:
    payload = json.loads(Path(args.apply).read_text())
    proposals = payload["proposals"]
    current_slug = payload["current_slug"]
    print(f"applying retag for {current_slug}: {len(proposals)} proposals")

    n_keep = n_retag = n_remove = n_err = 0
    with session_scope() as s:
        cur_tag_id = s.scalar(sqltext("SELECT id FROM content_tags WHERE slug = :s"),
                              {"s": current_slug})
        if cur_tag_id is None:
            print(f"current slug not in vocab: {current_slug}", file=sys.stderr)
            return 2
        for p in proposals:
            action = p.get("action")
            cid = p["card_id"]
            if action == "keep":
                n_keep += 1
                continue
            if action == "remove":
                s.execute(sqltext("""
                    DELETE FROM card_content_tags
                    WHERE card_id = :c AND content_tag_id = :t
                """), {"c": cid, "t": cur_tag_id})
                n_remove += 1
                continue
            if action == "retag":
                target = p.get("slug")
                if target == current_slug:
                    n_keep += 1
                    continue
                target_id = s.scalar(sqltext("SELECT id FROM content_tags WHERE slug = :s"),
                                     {"s": target})
                if target_id is None:
                    n_err += 1
                    continue
                # Add the new tag (approved), then drop the old
                s.execute(sqltext("""
                    INSERT INTO card_content_tags (card_id, content_tag_id, status, created_at)
                    VALUES (:c, :t, 'approved', now())
                    ON CONFLICT (card_id, content_tag_id) DO UPDATE SET status = 'approved'
                """), {"c": cid, "t": target_id})
                s.execute(sqltext("""
                    DELETE FROM card_content_tags
                    WHERE card_id = :c AND content_tag_id = :ct
                """), {"c": cid, "ct": cur_tag_id})
                n_retag += 1
                continue
            n_err += 1
    print(f"done. keep={n_keep}  retag={n_retag}  remove={n_remove}  errors={n_err}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--slug", choices=list(RETAG_MENUS),
                    help="which mistagged slug to audit")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--apply", default=None, metavar="REPORT")
    ap.add_argument("--out", default="retag.json")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--sample", type=int, default=12)
    args = ap.parse_args(argv)

    if args.dry_run and args.apply:
        print("--dry-run and --apply mutually exclusive", file=sys.stderr); return 2
    if args.apply:
        return cmd_apply(args)
    if args.dry_run:
        if not args.slug:
            print("--dry-run requires --slug", file=sys.stderr); return 2
        return cmd_dry_run(args)
    print("specify --dry-run --slug X / --apply REPORT", file=sys.stderr); return 2


if __name__ == "__main__":
    sys.exit(main())
