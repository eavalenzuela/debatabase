"""Re-run Claude-driven tagging across the existing card corpus.

For each card, asks Claude (Haiku) to choose tags from the existing
controlled vocabulary, then upserts those into ``card_content_tags``
with ``status='proposed'``. Existing 'approved' tags on the card are
left alone — proposed tags are additive, never overwrite admin-vetted
ones.

Idempotent: re-running on cards that already have proposed tags from
this script will replace them with the latest Claude output (proposed
rows for the card are deleted before re-insert, approved rows stay).

Usage:
    uv run python scripts/retag_cards.py --limit 20      # dry-ish smoke test
    uv run python scripts/retag_cards.py                 # full corpus
    uv run python scripts/retag_cards.py --where "id < 100"

Requires ANTHROPIC_API_KEY in .env.
"""

from __future__ import annotations

import argparse
import sys
import time

from sqlalchemy import select, text as sqltext

from debatabase.db import session_scope
from debatabase.models import Card, CardContentTag, ContentTag
from debatabase.tagger import (
    VocabEntry,
    has_tagging_capability,
    propose_tags,
)


def _load_vocab() -> list[VocabEntry]:
    with session_scope() as s:
        rows = s.execute(select(ContentTag)).scalars().all()
        return [
            VocabEntry(slug=t.slug, label=t.label, description=t.description)
            for t in rows
        ]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, default=None, help="stop after N cards")
    ap.add_argument(
        "--where",
        default=None,
        help="extra SQL WHERE clause appended to the card query (e.g. 'id < 100')",
    )
    args = ap.parse_args()

    if not has_tagging_capability():
        print("ANTHROPIC_API_KEY is not set. Add it to .env then re-run.")
        return 2

    vocab = _load_vocab()
    if not vocab:
        print("No content_tags rows in the DB. Seed the vocabulary first.")
        return 2
    print(f"Loaded vocabulary: {len(vocab)} tags")

    # Build the card-id list once so we can iterate without holding a session.
    with session_scope() as s:
        stmt = select(Card.id).order_by(Card.id)
        if args.where:
            stmt = stmt.where(sqltext(args.where))
        if args.limit is not None:
            stmt = stmt.limit(args.limit)
        card_ids = list(s.execute(stmt).scalars().all())

    print(f"Re-tagging {len(card_ids)} card(s)...")
    proposed_count = 0
    skipped = 0
    t_start = time.monotonic()

    slug_to_id = {v.slug: None for v in vocab}
    with session_scope() as s:
        rows = s.execute(select(ContentTag.id, ContentTag.slug)).all()
        for tid, slug in rows:
            slug_to_id[slug] = tid

    for i, cid in enumerate(card_ids, start=1):
        with session_scope() as s:
            card = s.get(Card, cid)
            if card is None:
                continue
            tag_text = card.tag
            body = card.card_text

        slugs = propose_tags(tag_text, body, vocab)
        if not slugs:
            skipped += 1
        else:
            with session_scope() as s:
                # Drop prior proposed rows for this card (idempotent re-run).
                s.execute(
                    sqltext(
                        "DELETE FROM card_content_tags "
                        "WHERE card_id = :cid AND status = 'proposed'"
                    ),
                    {"cid": cid},
                )
                # Insert fresh proposed rows; skip slugs already approved
                # on this card so we don't duplicate-key conflict.
                approved_ids = set(
                    s.execute(
                        sqltext(
                            "SELECT content_tag_id FROM card_content_tags "
                            "WHERE card_id = :cid AND status = 'approved'"
                        ),
                        {"cid": cid},
                    ).scalars().all()
                )
                for slug in slugs:
                    tid = slug_to_id.get(slug)
                    if tid is None or tid in approved_ids:
                        continue
                    s.add(
                        CardContentTag(
                            card_id=cid, content_tag_id=tid, status="proposed"
                        )
                    )
                    proposed_count += 1

        if i % 25 == 0 or i == len(card_ids):
            elapsed = time.monotonic() - t_start
            rate = i / elapsed if elapsed > 0 else 0
            print(
                f"  {i}/{len(card_ids)} cards processed "
                f"({proposed_count} proposed rows, {skipped} no-tag) — "
                f"{rate:.1f}/s"
            )

    elapsed = time.monotonic() - t_start
    print(
        f"done. {len(card_ids)} cards in {elapsed:.0f}s; "
        f"{proposed_count} proposed tag links inserted, {skipped} cards "
        f"got no tag."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
