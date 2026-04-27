-- Debatabase canonical schema. No migrations tooling: edit this file,
-- drop the .pgdata volume, recreate. Schema locks in before going public.

CREATE EXTENSION IF NOT EXISTS pg_trgm;

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
    ) STORED
);
CREATE INDEX cards_search_tsv_idx ON cards USING GIN (search_tsv);
CREATE INDEX cards_source_id_idx ON cards (source_id);
CREATE INDEX cards_tag_trgm_idx ON cards USING GIN (tag gin_trgm_ops);

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

    search_tsv      TSVECTOR GENERATED ALWAYS AS (
        to_tsvector('english', coalesce(argument, ''))
    ) STORED
);
CREATE INDEX analyticals_search_tsv_idx ON analyticals USING GIN (search_tsv);
CREATE INDEX analyticals_argument_trgm_idx ON analyticals USING GIN (argument gin_trgm_ops);

CREATE TABLE analytical_content_tags (
    analytical_id   BIGINT NOT NULL REFERENCES analyticals(id) ON DELETE CASCADE,
    content_tag_id  BIGINT NOT NULL REFERENCES content_tags(id) ON DELETE CASCADE,
    status          content_tag_status NOT NULL DEFAULT 'proposed',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (analytical_id, content_tag_id)
);
CREATE INDEX analytical_content_tags_tag_idx ON analytical_content_tags (content_tag_id, status);
