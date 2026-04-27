"""One-shot backfill: embed every card with NULL embedding.

Idempotent — safe to re-run; rows that already have a vector are
skipped. Batches the API calls (default 128 cards per request) so a
3k-card corpus completes in well under a minute.

Usage:
    uv run python scripts/backfill_embeddings.py            # all NULL
    uv run python scripts/backfill_embeddings.py --batch 64
    uv run python scripts/backfill_embeddings.py --limit 100  # smoke test

Requires VOYAGE_API_KEY in the environment / .env.
"""

from __future__ import annotations

import argparse
import sys
import time

from sqlalchemy import select

from debatabase.db import session_scope
from debatabase.embeddings import embed_documents, has_embedding_key
from debatabase.models import Card


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--batch", type=int, default=128, help="cards per API call")
    ap.add_argument("--limit", type=int, default=None, help="stop after N cards")
    args = ap.parse_args()

    if not has_embedding_key():
        print("VOYAGE_API_KEY is not set. Add it to .env then re-run.")
        return 2

    embedded = 0
    while True:
        if args.limit is not None and embedded >= args.limit:
            break

        with session_scope() as s:
            remaining = args.limit - embedded if args.limit is not None else args.batch
            chunk = min(args.batch, remaining)
            rows = s.execute(
                select(Card.id, Card.card_text)
                .where(Card.embedding.is_(None))
                .order_by(Card.id)
                .limit(chunk)
            ).all()
            if not rows:
                break
            ids = [r.id for r in rows]
            texts = [r.card_text for r in rows]

        t0 = time.monotonic()
        vectors = embed_documents(texts)
        elapsed = time.monotonic() - t0

        with session_scope() as s:
            for cid, vec in zip(ids, vectors, strict=True):
                card = s.get(Card, cid)
                if card is not None:
                    card.embedding = vec

        embedded += len(rows)
        print(
            f"  embedded {len(rows):3d} cards in {elapsed:.1f}s "
            f"(running total: {embedded})"
        )

    print(f"done. {embedded} card(s) embedded.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
