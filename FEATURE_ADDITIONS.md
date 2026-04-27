# Feature Additions

Planned next features, ordered by workflow impact. The first two close the read → prep → round loop; the rest scale the corpus and improve retrieval.

## 1. Speech doc assembly + Verbatim-compatible export

Right now debatabase is a reader. The loop only closes if cards can be pulled into an ordered list, grouped under block headers (Aff/Neg, 1AC, Off-case, etc.), and exported to a `.docx` that opens cleanly in Verbatim with underline and highlight markup intact.

- Cart / "speech doc" workspace: add cards from search results, reorder via drag.
- Block headers: user-defined H1/H2/H3 above groups of cards (matches the `block_path` model already in the schema).
- Export: render to `.docx` via `python-docx`, mapping markup spans back to runs with `w:u` / highlight shading. Round-trip with the existing extractor as the test.
- Save / load named speech docs in the DB.

## 2. In-browser re-highlighting (and re-underlining)

Highlights are round-specific — the 1AC read is long, the 2AC extension is six words. Editing markup in the browser and saving it as a card *variant* (not an overwrite) is the difference between cutting once and cutting per round.

- Click-drag selection over rendered card text to set highlight or underline spans.
- Save as a new card variant row scoped to the editing user's workspace (see #1). Variants are **never** visible on the global card detail page or in global search — only inside the workspace that created them. The original (canonical) card is never mutated.
- "Target N seconds at my WPM" helper: shrink highlight to fit a time budget.
- Export (#1) picks the workspace's variant when present, falls back to the canonical card otherwise.

## 3. Semantic search + "find an answer to this card"

`tsvector` handles known-author / known-phrase lookups but fails on argument-shaped queries ("heg resilient", "circumvention answers"). Embeddings unlock the queries debaters actually have.

**Status:** PR 6a + 6b + 6c shipped — feature #3 is complete.

- ✅ pgvector column on `cards` (vector(512), HNSW index over cosine distance). Voyage `voyage-3-lite` provider (`src/debatabase/embeddings.py`).
- ✅ Backfill script (`scripts/backfill_embeddings.py`), idempotent, batched.
- ✅ Hybrid search in `/search`: when `VOYAGE_API_KEY` is set, blend `tsvector` rank with cosine similarity. Cards without embeddings still match via keyword. Small "semantic" badge in the UI when active.
- ✅ "Find evidence that answers this card →" button on card detail. Claude Haiku generates the inverse claim of the card's tag, embeds it, vector-searches the corpus, returns the top 8 closest cards (`src/debatabase/answer_finder.py`). Requires both `ANTHROPIC_API_KEY` and `VOYAGE_API_KEY`; the button is hidden otherwise.
- ✅ Claude-assisted tagging (`src/debatabase/tagger.py`). On `bulk.py` ingest, when `ANTHROPIC_API_KEY` is set, each card's tag + body are sent to Haiku with the controlled vocabulary; output is constrained to existing slugs and lands in `card_content_tags` with `status='proposed'` (admin promotes to 'approved' later — currently via SQL, no admin UI yet). Falls back to legacy `KEYWORD_RULES` when no key. Retroactive script `scripts/retag_cards.py` runs the same flow over the existing corpus.

## 4. Duplicate / near-duplicate clustering ✅ shipped

Camp files repeat the same articles with different cuts. Without dedup the corpus accumulates 5–7 copies of the same Mearsheimer / Mbembe / Lamble cuts and they pollute search results.

**Status:** shipped. The seed corpus has 440 near-duplicate clusters at the default threshold; the worst clusters are 6–7 copies of identical cuts.

- ✅ `cards.canonical_card_id` (nullable FK to `cards.id`). NULL = canonical / standalone; set = "I'm a duplicate, point at the canonical."
- ✅ Clustering via embedding cosine distance + union-find (`src/debatabase/dedup.py`). Default threshold 0.08; HNSW index makes the per-card K-NN lookup fast (~3s for the full 3k-card corpus).
- ✅ `/admin/duplicates` review UI: lists clusters by descending size, radio-pick the canonical per cluster, one-click sets `canonical_card_id` on the rest.
- ✅ Search and `/cards/{id}/answers` filter `canonical_card_id IS NULL` so duplicates disappear from results once a canonical is picked.

Not yet (deferred): per-source duplicate detection at ingest time (right now it's purely retroactive); a "merge the markup spans" action that combines multiple cutters' highlights into one canonical card. Both are nice-to-haves, not blockers.

## 5. Opencaselist wiki ingest ✅ shipped (bulk-dump path)

The disclosure wiki is where pre-round prep lives. Reframed during implementation: the live opencaselist site requires login + has Google Doc embeds, but the **public S3 bucket of weekly bulk dumps** has every disclosed `.docx` directly. That's a much cleaner data source — no scraping, no auth, just `.docx` files we already know how to parse.

- ✅ `scripts/fetch_weekly_dumps.py` — HEAD-probes the public S3 bucket for available weekly zips (no listing API), downloads + optionally extracts.
- ✅ `scripts/ingest_wiki_dump.py` — walks an extracted dump, parses `{School}-{Team}-{Side}-…-Round-N.docx` filenames into structured metadata, hashes file content (SHA-256) for dedup across weekly snapshots, runs the existing `bulk.ingest_docx` per file with `wiki_upload_id` propagated to every inserted card / analytical.
- ✅ `wiki_uploads` table (school, team, side, tournament, round, source_file, file_sha256, first_seen, last_seen). FK from `cards.wiki_upload_id` and `analyticals.wiki_upload_id`.
- ✅ Filename parser (`src/debatabase/wiki_filename.py`) handles known variants: triple/double/single-hyphen separators, `.` -hyphen variants, no-sort-prefix variants, named rounds (Finals/Semis/Doubles), short school names. 13 tests cover real filenames from the corpus.
- ✅ Search results and card detail render a "from {school} {team} · {side} · {tournament} {round}" badge on every wiki card. No additional filter UI for v1 — visibility per-row is enough to triage.
- Claude tagger is force-disabled for the wiki ingest path because the corpus is large enough that per-card Haiku calls would cost real money. Legacy `KEYWORD_RULES` runs free; user can run `scripts/retag_cards.py` later if Claude-quality tagging on wiki cards is desired.

**Deferred (v2)**: opponent inference (requires the live opencaselist pages, which need auth); a scheduled `/admin/wiki-refresh` endpoint or routine that auto-fetches the latest weekly zip; richer wiki-specific filter UI on `/search` (filter by team, by tournament, by round).

## 6. IRC-style user accounts (nickname + password, no email) ✅ shipped

The workspace in #1 and the variants in #2 are per-user concepts; pre-#6 the app shipped with a single placeholder `local` user. #6 swaps in real registration / login — IRC NickServ-flavored rather than a typical webapp account system.

- No email, no password reset email loops, no "forgot password" flow. If you lose your password you lose your nick.
- Register: choose a nickname + password (min 8 chars). Server stores an `argon2` hash. Nicknames are unique, case-insensitive (CITEXT).
- Login sets a signed session cookie via Starlette's `SessionMiddleware`. No 2FA, no OAuth, no social login.
- A small middleware redirects unauthenticated requests to `/login` for any `/workspaces*` path. The card corpus (search, tags, sources, card detail, analyticals) stays publicly browseable.
- The very first registration on a fresh install can check "claim the existing pre-auth workspace data" to transmute the bootstrap `local` user (sets pw_hash + renames) so existing test workspaces aren't orphaned. Subsequent registrations create new users.
- Verified: cross-user PATCH/GET on another user's workspace or entry returns 404; sessions persist across reloads; logout clears the session cookie.
- Optional later: nick "ghosting" (kick a stale session if the same nick logs in elsewhere), nick reservation timeout. Not in v1.
