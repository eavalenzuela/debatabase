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

- Add an embedding column on `cards.card_text` (pgvector); backfill the existing 2,996.
- Hybrid search: blend tsvector rank with vector similarity.
- "Answers this card →" button on card detail that retrieves semantically opposed cards (prompt Claude to generate the inverse claim, then vector-search).
- Replace the keyword `KEYWORD_RULES` tagger in `bulk.py` with Claude-assisted tagging on ingest, constrained to the existing `content_tags` vocabulary.

## 4. Duplicate / near-duplicate clustering

Camp files repeat the same articles with different cuts. The data model should treat one article as one `source` with many cuts attached, not as N sibling cards.

- Near-duplicate detection at ingest: hash + embedding similarity over `card_text` against existing cards.
- When a match is found, attach as a new cut of the existing source rather than inserting a new source.
- Card detail shows "N cuts of this article" with each cutter's highlight; allow merging or picking a canonical cut.
- Backfill pass over the current corpus to collapse existing duplicates.

## 5. Opencaselist wiki scraper

The disclosure wiki is where pre-round prep lives. Most teams disclose full 1AC / 1NC text. A "paste a wiki URL → ingest their disclosed positions" flow is enormous for tournament prep.

- Fetch + parse opencaselist team pages (HTML, sometimes embedded Google Docs).
- Reuse the existing `.docx` extractor where possible; add an HTML-run extractor for the inline cases.
- Tag ingested cards with team / school / tournament / round metadata so they can be filtered separately from the personal corpus.
- Respect the wiki's rate limits and cache aggressively; never re-fetch a page within a tournament window.

## 6. IRC-style user accounts (nickname + password, no email) ✅ shipped

The workspace in #1 and the variants in #2 are per-user concepts; pre-#6 the app shipped with a single placeholder `local` user. #6 swaps in real registration / login — IRC NickServ-flavored rather than a typical webapp account system.

- No email, no password reset email loops, no "forgot password" flow. If you lose your password you lose your nick.
- Register: choose a nickname + password (min 8 chars). Server stores an `argon2` hash. Nicknames are unique, case-insensitive (CITEXT).
- Login sets a signed session cookie via Starlette's `SessionMiddleware`. No 2FA, no OAuth, no social login.
- A small middleware redirects unauthenticated requests to `/login` for any `/workspaces*` path. The card corpus (search, tags, sources, card detail, analyticals) stays publicly browseable.
- The very first registration on a fresh install can check "claim the existing pre-auth workspace data" to transmute the bootstrap `local` user (sets pw_hash + renames) so existing test workspaces aren't orphaned. Subsequent registrations create new users.
- Verified: cross-user PATCH/GET on another user's workspace or entry returns 404; sessions persist across reloads; logout clears the session cookie.
- Optional later: nick "ghosting" (kick a stale session if the same nick logs in elsewhere), nick reservation timeout. Not in v1.
