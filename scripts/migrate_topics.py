"""One-shot migration: add `topics` table + topic_id FK columns and
backfill the entire existing corpus to the 2025-2026 Arctic topic.

Idempotent: every CREATE / ALTER uses IF NOT EXISTS, the seed INSERT uses
ON CONFLICT DO NOTHING, and the UPDATEs only touch rows where topic_id IS
NULL. Safe to run twice. Run locally first; once verified, run against
prod (per the local-first-then-propagate workflow).

Usage:
    uv run python -m scripts.migrate_topics
"""

from __future__ import annotations

from sqlalchemy import text

from debatabase.db import session_scope


ARCTIC_RESOLUTION = (
    "Resolved: The United States federal government should substantially "
    "increase its security and/or economic cooperation with the Kingdom of "
    "Denmark, the Republic of Finland, the Republic of Iceland, the Kingdom "
    "of Norway, and/or the Kingdom of Sweden in the Arctic."
)


DDL_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS topics (
        id              BIGSERIAL PRIMARY KEY,
        slug            TEXT NOT NULL UNIQUE,
        season_start    SMALLINT NOT NULL,
        season_end      SMALLINT NOT NULL,
        short_name      TEXT NOT NULL,
        resolution      TEXT NOT NULL,
        is_current      BOOLEAN NOT NULL DEFAULT false,
        created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
        UNIQUE (season_start, season_end)
    )
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS topics_one_current_idx
        ON topics (is_current) WHERE is_current
    """,
    "ALTER TABLE cards        ADD COLUMN IF NOT EXISTS topic_id BIGINT REFERENCES topics(id) ON DELETE SET NULL",
    "ALTER TABLE analyticals  ADD COLUMN IF NOT EXISTS topic_id BIGINT REFERENCES topics(id) ON DELETE SET NULL",
    "ALTER TABLE wiki_uploads ADD COLUMN IF NOT EXISTS topic_id BIGINT REFERENCES topics(id) ON DELETE SET NULL",
    "CREATE INDEX IF NOT EXISTS cards_topic_idx        ON cards        (topic_id) WHERE topic_id IS NOT NULL",
    "CREATE INDEX IF NOT EXISTS analyticals_topic_idx  ON analyticals  (topic_id) WHERE topic_id IS NOT NULL",
    "CREATE INDEX IF NOT EXISTS wiki_uploads_topic_idx ON wiki_uploads (topic_id) WHERE topic_id IS NOT NULL",
]


def main() -> None:
    with session_scope() as s:
        for stmt in DDL_STATEMENTS:
            s.execute(text(stmt))

        s.execute(
            text(
                """
                INSERT INTO topics
                    (slug, season_start, season_end, short_name, resolution, is_current)
                VALUES
                    ('2025-2026-arctic', 2025, 2026, 'Arctic', :res, true)
                ON CONFLICT (slug) DO NOTHING
                """
            ),
            {"res": ARCTIC_RESOLUTION},
        )

        topic_id = s.execute(
            text("SELECT id FROM topics WHERE slug = '2025-2026-arctic'")
        ).scalar_one()

        for table in ("cards", "analyticals", "wiki_uploads"):
            result = s.execute(
                text(f"UPDATE {table} SET topic_id = :tid WHERE topic_id IS NULL"),
                {"tid": topic_id},
            )
            print(f"  {table}: stamped {result.rowcount} rows")

    print(f"Done. Arctic topic id = {topic_id}.")


if __name__ == "__main__":
    main()
