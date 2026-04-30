"""One-shot migration: add cards.dedup_excluded boolean.

The /admin/duplicates UI grew a "split off" action — clicking it tells
the system this card isn't a duplicate of anything and should never
re-appear in a cluster. That's persisted as cards.dedup_excluded=true.

Idempotent. Safe to run twice. Run locally first, then prod.

Usage:
    uv run python -m scripts.migrate_dedup_excluded
"""

from __future__ import annotations

from sqlalchemy import text

from debatabase.db import session_scope


DDL_STATEMENTS = [
    "ALTER TABLE cards ADD COLUMN IF NOT EXISTS dedup_excluded BOOLEAN NOT NULL DEFAULT false",
    "CREATE INDEX IF NOT EXISTS cards_dedup_excluded_idx ON cards (dedup_excluded) WHERE dedup_excluded",
]


def main() -> None:
    with session_scope() as s:
        for stmt in DDL_STATEMENTS:
            s.execute(text(stmt))
    print("Done.")


if __name__ == "__main__":
    main()
