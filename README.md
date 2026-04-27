# debatabase

A searchable database of policy debate evidence cards. Built to make a personal collection of `.docx` evidence files browseable, taggable, and quickly searchable — including by author, by argument, or by full body text — with the underline/highlight distinctions that debaters care about preserved end-to-end.

## What's in here

- **Postgres schema** — `sources`, `cards`, `analyticals`, an admin-curated `content_tags` vocabulary, plus join tables. Full-text search via `tsvector`; tag hierarchy via `parent_id`.
- **`.docx` extractor** — `python-docx`-based, with the catch that Word formatting can come from direct run properties OR from character styles (e.g. `<w:rStyle w:val="StyleUnderline"/>`). The extractor parses `styles.xml` and resolves character-style inheritance so underlines and highlights aren't silently lost.
- **Bulk ingest pipeline** — walks a folder of `.docx` files, segments them into cards (and analyticals — see below), auto-parses cite lines, infers content tags from keywords against a controlled vocabulary, and inserts. Successful files are moved to `parsed_docs/`.
- **Web UI** (FastAPI + Jinja2 + HTMX):
  - Live search across tag / body / author / cite shorthand
  - Card detail with **render mode toggle**: full / underline-only / **highlight-only (in-round read text)** / plain
  - Hierarchical tag sidebar with collapsible parent groups
  - Pagination with result counts
- **Seed data** — `db_seed.sql` ships with the initial corpus already loaded (2,996 cards, 88 analyticals, 2,139 sources, 86 content tags, drawn from ~75 source documents).

## Domain glossary

If you don't debate, this vocabulary is opinionated and worth knowing up front:

- **Card** — a single quoted passage from an external source, paired with a debater-authored **tag** summarizing the argument it's being used to make. Cards have a real cite.
- **Tag** — the one-line summary above the card. *Not* the source's title — it's the cutter's editorial framing.
- **Cite** — author + year shorthand (`Roberts 19`, `Lewis & Weichselbaum 6/9`) plus a longer paragraph with author qualifications, publication, title, URL, and often a **cutter** signature (`//Armaan`, `)-Selin`, etc.). The full original cite is preserved verbatim in `sources.raw_cite`; structured fields are best-effort parses.
- **Underline** = words the cutter marked as argumentatively relevant.
- **Highlight** = the subset that gets actually *spoken in-round*. Highlight is always a subset of underline. Both are preserved as offset spans over the plain `card_text`.
- **Analytical** — a debater-authored argument with no external source, typically responding to an opposing team's claim (`AT: <claim>`). Distinct from cards (no cite) and from plan/CP/alt text (those are the team's *advocacy* and aren't stored at all). Lives in its own table with an `answer_to` field.
- **Block path** — the `Heading 1 → Heading 2 → Heading 3` structure above each card in the original speech doc (e.g. `["Aff", "1AC", "Social Workers Adv."]`). Sub-block H4s like "Scenario One: Poverty" are dropped as speech-doc-order artifacts.

## Quick start

Requires: Docker, Python 3.12+, `uv`.

```bash
# 1. Start Postgres (auto-applies schema.sql on first boot)
docker compose up -d

# 2. Load the seed data — about 30 seconds for 26 MB of inserts
docker exec -i debatabase-postgres psql -U debatabase -d debatabase < db_seed.sql

# 3. Install Python deps + run the web UI
uv sync
cp .env.example .env  # optional: add your ANTHROPIC_API_KEY for tagging tools
uv run uvicorn debatabase.web.app:app --reload
```

Open http://127.0.0.1:8000.

To start fresh without seed data, skip step 2 (the schema is applied automatically by docker-compose).

## Repository layout

```
schema.sql                          canonical Postgres DDL (no migrations tooling)
db_seed.sql                         pg_dump of the initial corpus (data only)
docker-compose.yml                  postgres:16 on host port 5433
src/debatabase/
  models.py                         SQLAlchemy 2.x ORM
  db.py / config.py                 engine + settings
  parser/extract.py                 .docx → flat paragraph/run JSONL stream
  ingest.py                         insert_card / insert_analytical helpers
  bulk.py                           reusable cite parser + tag inference + map_doc
  web/
    app.py                          FastAPI routes
    render.py                       markup-span → HTML rendering (full/highlight-only/etc.)
    templates/                      Jinja2 + HTMX
    static/style.css
scripts/
  bulk_ingest.py                    walk Evidence/ → ingest → move to parsed_docs/
  ingest_*.py                       one-shot scripts from the early per-card training session
  reingest_indexerror_docs.py       fix-up script for the trailing-H4 bug
  cleanup_cite_shorts.py            quality pass on auto-parsed cite shortcuts
```

## Adding new evidence

1. Drop `.docx` files into `Evidence/` (any subdirectory structure works).
2. Run `uv run python scripts/bulk_ingest.py`. Each file is extracted, segmented, ingested, and on success moved to `parsed_docs/` (mirroring its subdirectory structure under `Evidence/`).
3. Files already represented in the DB by their `source_file` name are skipped automatically.

The pipeline is deliberately conservative on tags — it sticks to the existing controlled vocabulary plus a known list of new tag rules. Any new tag families you want should be added to `KNOWN_TAGS` and `KEYWORD_RULES` in `src/debatabase/bulk.py` first.

## Source documents

The original `.docx` source files are **not** redistributed in this repository. The seed dump contains the parsed cards (verbatim quoted text + cite metadata + markup spans), which is what's useful to debaters; the source docs themselves stay local. If you maintain your own corpus, point the bulk ingest pipeline at it and re-export `db_seed.sql` if you want to share.

## Status

Built end-to-end in one extended session as a personal tool. No tests yet. Not deployed anywhere.
