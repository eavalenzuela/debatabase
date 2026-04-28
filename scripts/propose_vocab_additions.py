"""Sample wiki cards, ask Claude to propose new content_tag slugs.

The current controlled vocabulary was built around the user's personal
corpus (policing / abolition / critical theory). The wiki ingest pulled
in ~88k cards from the 2025-26 hspolicy season — a different topic
(Arctic policy) that the existing slugs don't cover. This script
samples wiki cards, shows Claude the existing vocab, and asks for
proposed additions.

Output goes to a JSON file for the user to review before any
content_tags rows get inserted.

Usage:
    uv run python scripts/propose_vocab_additions.py
    uv run python scripts/propose_vocab_additions.py --sample-size 80 --batches 4
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from anthropic import Anthropic
from sqlalchemy import select, text as sqltext

from debatabase.config import settings
from debatabase.db import session_scope
from debatabase.models import ContentTag

_HAIKU_MODEL = "claude-haiku-4-5-20251001"
_BODY_CHARS = 800

_SYSTEM_PROMPT = (
    "You are helping curate the controlled tag vocabulary for a policy "
    "debate evidence database. The user already has a vocabulary built "
    "around their existing corpus (policing, abolition, critical theory, "
    "K debates). They have just imported a large new corpus of cards from "
    "the 2025-26 hspolicy season — primarily about Arctic exploration / "
    "development. You will see a sample of those new cards and the "
    "existing vocabulary.\n\n"
    "PROPOSE new tag slugs (8 to 15 per response) that would help tag "
    "these new cards but aren't redundant with anything in the existing "
    "vocab. Slugs should be:\n"
    "- Lowercase, hyphenated, short (2-4 words). Examples in the existing "
    "vocab: 'cap-k', 'police-abolition', 'race-as-technology'.\n"
    "- ARGUMENTATIVE / TOPICAL, not just topical labels. A good slug "
    "labels what kind of argument the card makes: 'arctic-militarization', "
    "'icebreaker-shortage-da', 'indigenous-sovereignty-link', not just "
    "'arctic' or 'indigenous'.\n"
    "- Useful across multiple cards, not one-offs.\n\n"
    "Output ONLY a JSON array of objects with fields slug, label, "
    "description, rationale. No preamble, no code fences."
)


def _load_existing_vocab() -> list[ContentTag]:
    with session_scope() as s:
        return list(s.execute(select(ContentTag).order_by(ContentTag.slug)).scalars().all())


def _sample_cards(n: int) -> list[dict]:
    """Stratified random sample across (side, tournament) combinations."""
    with session_scope() as s:
        rows = s.execute(
            sqltext("""
                SELECT c.id, c.tag, substring(c.card_text, 1, :nb) AS body,
                       w.side, w.tournament
                FROM cards c
                JOIN wiki_uploads w ON w.id = c.wiki_upload_id
                WHERE c.canonical_card_id IS NULL
                ORDER BY random()
                LIMIT :n
            """),
            {"nb": _BODY_CHARS, "n": n},
        ).all()
    return [
        {
            "id": r.id,
            "tag": r.tag,
            "body": r.body,
            "side": r.side,
            "tournament": r.tournament,
        }
        for r in rows
    ]


def _build_user_msg(cards: list[dict], vocab: list[ContentTag]) -> str:
    vocab_lines = []
    for v in vocab:
        if v.description:
            vocab_lines.append(f"- {v.slug}: {v.label} — {v.description}")
        else:
            vocab_lines.append(f"- {v.slug}: {v.label}")
    vocab_block = "\n".join(vocab_lines)

    card_lines = []
    for c in cards:
        card_lines.append(
            f"--- card #{c['id']} ({c['side']}{', ' + c['tournament'] if c['tournament'] else ''}) ---\n"
            f"tag: {c['tag']}\n"
            f"body: {c['body']}"
        )
    cards_block = "\n\n".join(card_lines)

    return (
        f"Existing vocabulary ({len(vocab)} tags):\n{vocab_block}\n\n"
        f"Sample of new (Arctic / 2025-26 hspolicy) cards:\n\n{cards_block}\n\n"
        f"Now propose new slugs."
    )


def _parse_proposals(text: str) -> list[dict]:
    match = re.search(r"\[.*\]", text, flags=re.DOTALL)
    if match is None:
        return []
    try:
        parsed = json.loads(match.group(0))
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    out = []
    for p in parsed:
        if not isinstance(p, dict):
            continue
        slug = (p.get("slug") or "").strip().lower()
        label = (p.get("label") or "").strip()
        if not slug or not label:
            continue
        out.append({
            "slug": slug,
            "label": label,
            "description": (p.get("description") or "").strip() or None,
            "rationale": (p.get("rationale") or "").strip() or None,
        })
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sample-size", type=int, default=80,
                    help="total wiki cards to sample (default 80)")
    ap.add_argument("--batches", type=int, default=4,
                    help="number of independent Claude calls; results merged "
                    "and de-duped by slug (default 4)")
    ap.add_argument("--output", type=Path,
                    default=Path("/tmp/proposed_slugs.json"))
    args = ap.parse_args()

    if not settings.anthropic_api_key:
        print("ANTHROPIC_API_KEY required.", file=sys.stderr)
        return 2

    vocab = _load_existing_vocab()
    existing_slugs = {v.slug for v in vocab}
    print(f"existing vocabulary: {len(vocab)} tags")

    client = Anthropic(api_key=settings.anthropic_api_key)
    cards_per_batch = max(1, args.sample_size // args.batches)
    all_proposals: dict[str, dict] = {}  # slug -> proposal (last write wins)

    for i in range(args.batches):
        cards = _sample_cards(cards_per_batch)
        print(f"  batch {i+1}/{args.batches}: {len(cards)} cards → Haiku...")
        msg = client.messages.create(
            model=_HAIKU_MODEL,
            max_tokens=2000,
            system=_SYSTEM_PROMPT,
            messages=[
                {"role": "user", "content": _build_user_msg(cards, vocab)},
            ],
        )
        text = "".join(
            b.text for b in msg.content if getattr(b, "type", None) == "text"
        )
        proposals = _parse_proposals(text)
        print(f"     → {len(proposals)} proposed slugs")
        for p in proposals:
            if p["slug"] in existing_slugs:
                continue
            all_proposals[p["slug"]] = p

    sorted_proposals = sorted(all_proposals.values(), key=lambda p: p["slug"])
    args.output.write_text(json.dumps(sorted_proposals, indent=2))
    print(f"\nproposed {len(sorted_proposals)} new slug(s); saved to {args.output}")
    print("\npreview:")
    for p in sorted_proposals[:10]:
        print(f"  {p['slug']:35} {p['label']}")
        if p.get("rationale"):
            print(f"    why: {p['rationale']}")
    if len(sorted_proposals) > 10:
        print(f"  ... and {len(sorted_proposals) - 10} more")
    return 0


if __name__ == "__main__":
    sys.exit(main())
