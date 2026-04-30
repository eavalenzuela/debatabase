-- Debatabase canonical schema. No migrations tooling: edit this file,
-- drop the .pgdata volume, recreate. Schema locks in before going public.

CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS citext;
-- pgvector: provides the `vector` type used for cards.embedding (semantic
-- search). Requires the pgvector/pgvector:pg16 image (set in docker-compose).
CREATE EXTENSION IF NOT EXISTS vector;

-- A debate topic season: one Policy resolution per year. Cards / analyticals
-- / wiki_uploads are tagged with the topic they were assembled FOR — distinct
-- from the source's publication year (sources.year), which is when the
-- underlying article/book came out. Filtering on `topic_id` is what lets
-- students browse "this year's evidence" once next year's camp packets land.
CREATE TABLE topics (
    id              BIGSERIAL PRIMARY KEY,
    slug            TEXT NOT NULL UNIQUE,                -- e.g. "2025-2026-arctic"
    season_start    SMALLINT NOT NULL,                   -- fall-of year, e.g. 2025
    season_end      SMALLINT NOT NULL,                   -- spring-of year, e.g. 2026
    short_name      TEXT NOT NULL,                       -- "Arctic" — for chips/dropdowns
    resolution      TEXT NOT NULL,                       -- full "Resolved: ..." text
    is_current      BOOLEAN NOT NULL DEFAULT false,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (season_start, season_end)
);
-- At most one current topic at a time. Partial unique index = "is_current
-- TRUE row is unique; FALSE rows are unconstrained."
CREATE UNIQUE INDEX topics_one_current_idx ON topics (is_current) WHERE is_current;

-- A source is a single citable work (article, book, etc.). Multiple cards
-- may be cut from the same source; we dedupe at the source level so
-- author/qualifications/url stay consistent across cards.
CREATE TABLE sources (
    id              BIGSERIAL PRIMARY KEY,
    cite_short      TEXT NOT NULL,                     -- e.g. "Roberts 19"
    author_last     TEXT NOT NULL,                     -- e.g. "Roberts"
    author_full     TEXT,                              -- e.g. "Dorothy E. Roberts"
    qualifications  TEXT,                              -- titles, affiliations
    publication     TEXT,                              -- journal/book/site
    title           TEXT,                              -- article/chapter title
    published_date  TEXT,                              -- free-form; sources vary
    year            SMALLINT,                          -- parsed for filtering
    url             TEXT,
    raw_cite        TEXT NOT NULL,                     -- the full original cite line, verbatim
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX sources_cite_short_idx ON sources (cite_short);
CREATE INDEX sources_author_last_idx ON sources (author_last);
CREATE INDEX sources_year_idx ON sources (year);

-- A card: one quoted passage cut by a debater, with a tag and markup.
-- markup spans are offsets over card_text; highlighted is always a subset
-- of underlined per the project domain rules.
CREATE TABLE cards (
    id              BIGSERIAL PRIMARY KEY,
    source_id       BIGINT NOT NULL REFERENCES sources(id) ON DELETE RESTRICT,
    tag             TEXT NOT NULL,
    tag_markup      JSONB NOT NULL DEFAULT '[]'::jsonb,  -- spans over `tag`
    card_text       TEXT NOT NULL,
    markup          JSONB NOT NULL DEFAULT '[]'::jsonb,  -- spans over `card_text`
    -- shape: [{"start": int, "end": int, "kind": "underline"|"highlight"}, ...]

    -- Provenance: where this card was ingested from.
    source_file     TEXT,                              -- e.g. "Cap K Commodification Cards.docx"
    block_path      TEXT[],                            -- e.g. ["Neg", "1NC", "Shell"]
    ingested_at     TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- Full-text search: tag + card_text. Generated column kept in sync.
    search_tsv      TSVECTOR GENERATED ALWAYS AS (
        setweight(to_tsvector('english', coalesce(tag, '')), 'A') ||
        setweight(to_tsvector('english', coalesce(card_text, '')), 'B')
    ) STORED,
    -- Semantic search vector. NULL until backfilled by
    -- scripts/backfill_embeddings.py (requires VOYAGE_API_KEY).
    -- Dimension matches voyage-3-lite (512). Search blends this with
    -- search_tsv when populated; falls back to tsvector-only otherwise.
    embedding       vector(512),
    -- Dedup: when set, this card has been marked as a duplicate of the
    -- referenced canonical card. Search and answer-finder filter
    -- non-canonical rows out (canonical_card_id IS NOT NULL means
    -- "hidden duplicate"). Set via /admin/duplicates. Self-reference
    -- is intentionally not used — canonicals have NULL here.
    canonical_card_id BIGINT REFERENCES cards(id) ON DELETE SET NULL,
    -- Wiki provenance (FEATURE_ADDITIONS.md #5): set on cards extracted
    -- from opencaselist weekly bulk dumps; NULL for cards from the
    -- user's personal corpus. The FK is added by ALTER TABLE after
    -- wiki_uploads is defined below — we keep cards' DDL ordering
    -- stable so existing migrations / dumps don't get re-shuffled.
    wiki_upload_id  BIGINT,
    -- Topic season this card was cut for. NULL is allowed for legacy
    -- imports, but every ingest path stamps it. Filtering on this in
    -- /search is how we keep next year's camp packets from drowning
    -- out (or being drowned out by) prior years.
    topic_id        BIGINT REFERENCES topics(id) ON DELETE SET NULL,
    -- Set true when the admin explicitly tells /admin/duplicates that
    -- this card is NOT a duplicate of anything ("split off"). dedup
    -- skips it on subsequent passes so the cluster doesn't re-surface;
    -- the card itself still appears in /search normally.
    dedup_excluded  BOOLEAN NOT NULL DEFAULT false
);
CREATE INDEX cards_search_tsv_idx ON cards USING GIN (search_tsv);
CREATE INDEX cards_source_id_idx ON cards (source_id);
CREATE INDEX cards_tag_trgm_idx ON cards USING GIN (tag gin_trgm_ops);
CREATE INDEX cards_embedding_hnsw_idx ON cards USING hnsw (embedding vector_cosine_ops);
CREATE INDEX cards_canonical_idx ON cards (canonical_card_id)
    WHERE canonical_card_id IS NOT NULL;
CREATE INDEX cards_topic_idx ON cards (topic_id) WHERE topic_id IS NOT NULL;
CREATE INDEX cards_dedup_excluded_idx ON cards (dedup_excluded) WHERE dedup_excluded;

-- Controlled vocabulary of argument-type tags. Grown collaboratively
-- (admin approves each one); never auto-created at ingest time.
CREATE TABLE content_tags (
    id              BIGSERIAL PRIMARY KEY,
    slug            TEXT NOT NULL UNIQUE,              -- e.g. "anti-blackness", "condo-bad"
    label           TEXT NOT NULL,                     -- human-readable
    description     TEXT,
    parent_id       BIGINT REFERENCES content_tags(id) ON DELETE SET NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- M:N between cards and content_tags. `status` distinguishes LLM-proposed
-- vs admin-approved. The admin-review UI promotes proposed -> approved.
CREATE TYPE content_tag_status AS ENUM ('proposed', 'approved');

CREATE TABLE card_content_tags (
    card_id         BIGINT NOT NULL REFERENCES cards(id) ON DELETE CASCADE,
    content_tag_id  BIGINT NOT NULL REFERENCES content_tags(id) ON DELETE CASCADE,
    status          content_tag_status NOT NULL DEFAULT 'proposed',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (card_id, content_tag_id)
);
CREATE INDEX card_content_tags_tag_idx ON card_content_tags (content_tag_id, status);

-- Analyticals: debater-authored arguments without an external source. Distinct
-- from cards (which have a real cite + quoted body) and from plan/CP/alt text
-- (which is the team's advocacy, not an argument). Often answer an opposing
-- argument named in `answer_to` (typically derived from the H2 above the H4).
CREATE TABLE analyticals (
    id              BIGSERIAL PRIMARY KEY,
    argument        TEXT NOT NULL,                       -- the H4-styled argument text, verbatim
    argument_markup JSONB NOT NULL DEFAULT '[]'::jsonb,  -- spans over `argument`

    answer_to       TEXT,                                -- free-text, e.g. "Poverty Adv. CP"

    source_file     TEXT,
    block_path      TEXT[],
    ingested_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    topic_id        BIGINT REFERENCES topics(id) ON DELETE SET NULL,

    search_tsv      TSVECTOR GENERATED ALWAYS AS (
        to_tsvector('english', coalesce(argument, ''))
    ) STORED
);
CREATE INDEX analyticals_search_tsv_idx ON analyticals USING GIN (search_tsv);
CREATE INDEX analyticals_argument_trgm_idx ON analyticals USING GIN (argument gin_trgm_ops);
CREATE INDEX analyticals_topic_idx ON analyticals (topic_id) WHERE topic_id IS NOT NULL;

CREATE TABLE analytical_content_tags (
    analytical_id   BIGINT NOT NULL REFERENCES analyticals(id) ON DELETE CASCADE,
    content_tag_id  BIGINT NOT NULL REFERENCES content_tags(id) ON DELETE CASCADE,
    status          content_tag_status NOT NULL DEFAULT 'proposed',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (analytical_id, content_tag_id)
);
CREATE INDEX analytical_content_tags_tag_idx ON analytical_content_tags (content_tag_id, status);

-- Users. IRC-style: nickname + password, no email. pw_hash is nullable so
-- the bootstrap "local" user (single-user mode, pre-auth) has no password
-- yet. Real registration / login flow lands with FEATURE_ADDITIONS.md #6.
-- current_workspace_id points at the workspace the user is currently
-- editing; nullable because workspaces are created after the user, and
-- because a deleted-last-workspace race could briefly orphan it.
CREATE TABLE users (
    id                    BIGSERIAL PRIMARY KEY,
    nickname              CITEXT NOT NULL UNIQUE,
    pw_hash               TEXT,
    current_workspace_id  BIGINT,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Workspaces: a user can have many named speech-doc workspaces. The
-- "current" one (the workspace that "+ Add to workspace" buttons target,
-- and the one /workspace redirects to) is tracked on users.
CREATE TABLE workspaces (
    id              BIGSERIAL PRIMARY KEY,
    user_id         BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name            TEXT NOT NULL,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX workspaces_user_id_idx ON workspaces (user_id);

-- Deferred FK so users.current_workspace_id can reference a row that
-- doesn't exist yet at user-creation time.
ALTER TABLE users
    ADD CONSTRAINT users_current_workspace_fk
    FOREIGN KEY (current_workspace_id) REFERENCES workspaces(id)
    ON DELETE SET NULL;

-- One row per card or analytical added to a workspace, ordered by
-- `position` and grouped under a per-entry `header_path` (mirrors
-- cards.block_path so the export can emit Heading 1/2/3 by diffing
-- consecutive entries' paths).
-- Card variants: a per-workspace override of a canonical card's markup
-- (highlight + underline spans). Created when a debater re-cuts a card
-- for a specific round / extension / 2AC; never visible outside the
-- owning workspace. Global /cards/{id} and search NEVER join this
-- table — the canonical markup is the authoritative shared view, and
-- variants are private to the cutter's workspace. (FEATURE_ADDITIONS.md
-- #2 captures the scoping rule.)
CREATE TABLE card_variants (
    id              BIGSERIAL PRIMARY KEY,
    workspace_id    BIGINT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    card_id         BIGINT NOT NULL REFERENCES cards(id) ON DELETE CASCADE,
    markup          JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX card_variants_workspace_idx ON card_variants (workspace_id);
CREATE INDEX card_variants_card_idx ON card_variants (card_id);

CREATE TABLE workspace_entries (
    id                BIGSERIAL PRIMARY KEY,
    workspace_id      BIGINT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    position          INTEGER NOT NULL,
    header_path       TEXT[],
    card_id           BIGINT REFERENCES cards(id) ON DELETE SET NULL,
    analytical_id     BIGINT REFERENCES analyticals(id) ON DELETE SET NULL,
    -- When set, the export and the workspace UI use this variant's
    -- markup instead of cards.markup. NULL means "use canonical."
    card_variant_id   BIGINT REFERENCES card_variants(id) ON DELETE SET NULL,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT workspace_entry_one_target
        CHECK ((card_id IS NULL) <> (analytical_id IS NULL)),
    UNIQUE (workspace_id, position)
);
CREATE INDEX workspace_entries_workspace_idx ON workspace_entries (workspace_id, position);

-- Wiki uploads (FEATURE_ADDITIONS.md #5): one row per disclosed .docx
-- pulled from the opencaselist weekly bulk dumps. cards.wiki_upload_id
-- links each card back to the disclosed file it came from so the UI
-- can show "from MontgomeryBell HoLi · Aff · TOC R1" per card.
-- Dedup at the file-content level via file_sha256 — the same .docx
-- appears across many weekly snapshots, and we only want one upload
-- row per unique file. first_seen / last_seen track the date range we
-- saw the content in the weekly dumps.
CREATE TABLE wiki_uploads (
    id              BIGSERIAL PRIMARY KEY,
    school          TEXT NOT NULL,
    team            TEXT NOT NULL,
    side            TEXT NOT NULL,                     -- "Aff" or "Neg"
    tournament      TEXT,
    round_name      TEXT,
    source_file     TEXT NOT NULL,
    file_sha256     TEXT NOT NULL UNIQUE,
    first_seen      DATE NOT NULL,
    last_seen       DATE NOT NULL,
    ingested_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    topic_id        BIGINT REFERENCES topics(id) ON DELETE SET NULL
);
CREATE INDEX wiki_uploads_school_idx ON wiki_uploads (school);
CREATE INDEX wiki_uploads_team_idx ON wiki_uploads (school, team);
CREATE INDEX wiki_uploads_tournament_idx ON wiki_uploads (tournament, side);
CREATE INDEX wiki_uploads_topic_idx ON wiki_uploads (topic_id) WHERE topic_id IS NOT NULL;

-- Now that wiki_uploads exists, wire up the FKs from cards/analyticals.
ALTER TABLE cards
    ADD CONSTRAINT cards_wiki_upload_fk
    FOREIGN KEY (wiki_upload_id) REFERENCES wiki_uploads(id) ON DELETE SET NULL;
ALTER TABLE analyticals
    ADD COLUMN wiki_upload_id BIGINT REFERENCES wiki_uploads(id) ON DELETE SET NULL;
CREATE INDEX cards_wiki_upload_idx ON cards (wiki_upload_id)
    WHERE wiki_upload_id IS NOT NULL;
CREATE INDEX analyticals_wiki_upload_idx ON analyticals (wiki_upload_id)
    WHERE wiki_upload_id IS NOT NULL;

-- Bootstrap: a single placeholder user + their default workspace so v1
-- routes can resolve a "current user" without auth. Auth
-- (FEATURE_ADDITIONS.md #6) replaces the placeholder user with real
-- registration; rows stay valid through that change.
INSERT INTO users (id, nickname) VALUES (1, 'local');
SELECT setval(pg_get_serial_sequence('users', 'id'), 1, true);
INSERT INTO workspaces (id, user_id, name) VALUES (1, 1, 'My Workspace');
SELECT setval(pg_get_serial_sequence('workspaces', 'id'), 1, true);
UPDATE users SET current_workspace_id = 1 WHERE id = 1;

-- Seed the current Policy topic. New ingests stamp this topic by default
-- (see scripts/bulk_ingest.py and scripts/ingest_wiki_dump.py). The
-- partial unique index on is_current keeps "current" to one row at a time.
INSERT INTO topics (id, slug, season_start, season_end, short_name, resolution, is_current)
VALUES (
    1,
    '2025-2026-arctic',
    2025,
    2026,
    'Arctic',
    'Resolved: The United States federal government should substantially increase its security and/or economic cooperation with the Kingdom of Denmark, the Republic of Finland, the Republic of Iceland, the Kingdom of Norway, and/or the Kingdom of Sweden in the Arctic.',
    true
);
SELECT setval(pg_get_serial_sequence('topics', 'id'), 1, true);
