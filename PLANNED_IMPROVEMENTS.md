# Planned Improvements

Plan for this maintenance pass. Ten improvements to existing behavior, five new
features. Each item is scoped to land as part of a single commit.

## Improvements

1. **URL-encode `q` / `tag` / `topic` in template links** — pagination, tag
   sidebar, chips, and active-filter links interpolate the raw query string, so
   a search containing `&`, `#`, or `+` produces broken navigation links.
2. **Skip sidebar/count queries on HTMX fragment requests** — `_search` runs
   the tag-tree query plus four corpus-wide COUNTs on every debounced
   keystroke, but `search_results.html` renders none of them; compute them only
   for full-page loads.
3. **Reject auth paths in `_safe_next`** — `?next=/login` currently redirects a
   logged-in user to `/login`, which redirects again: an infinite loop. Treat
   `/login`, `/register`, `/logout` as invalid targets.
4. **Handle the register nickname race** — two concurrent registrations of the
   same nick pass the pre-check and the second 500s on the unique constraint;
   catch `IntegrityError` and re-render "nickname already taken".
5. **Security-headers middleware** — emit `X-Content-Type-Options: nosniff`,
   `X-Frame-Options: DENY`, `Referrer-Policy: strict-origin-when-cross-origin`
   on every response (the deployed instance currently sends none of them).
6. **Log swallowed exceptions** — `_embed_query_safe`, the `card_answers` error
   paths, and `tagger.propose_tags` all `except Exception` silently; a dead
   Voyage/Anthropic key is invisible in production logs today.
7. **Flatten canonical chains in `set_canonical`** — when a member that was
   itself the canonical of older duplicates gets marked a duplicate, its
   children keep pointing at a non-canonical card; re-point them in the same
   UPDATE pass.
8. **Fix the stale README Status section** — it says "Not deployed anywhere"
   five paragraphs under the live-instance link; also document the features
   added in this pass.
9. **Document `DEBATABASE_ENV` and `TRUSTED_PROXIES` in `.env.example`** — both
   are read by `config.py` / `rate_limit.py` but are undiscoverable without
   reading source.
10. **Add `tests/test_render.py`** — `web/render.py` decides what every page
    shows (all four modes, escaping, snippets) and has zero test coverage.

## New features

11. **Speech-time estimates in workspaces** — per-entry highlighted word count
    and estimated read seconds at a debate-speed WPM, plus a workspace total in
    the toolbar. The read-side half of FEATURE_ADDITIONS #2's "target N
    seconds at my WPM" helper. New pure `speech_time.py` + tests.
12. **`/sources` browse page** — paginated list of all sources with per-source
    card counts and a filter over author / cite / publication / title; today
    sources are only reachable by clicking through a card.
13. **Search filter by wiki school** — the school badge on wiki-ingested cards
    becomes a link that sets `?school=`, with an active-filter chip to clear
    it. First slice of the deferred "richer wiki-specific filter UI" from
    FEATURE_ADDITIONS #5.
14. **`/healthz` endpoint** — JSON liveness + DB connectivity check for the
    EC2/Cloudflare deployment described in DEPLOYMENT_INFORMATION.md.
15. **"Adopt markup" from an identical-text alt cut** — one-click union of a
    duplicate cutting's underline/highlight spans into the canonical card (the
    deferred "merge the markup spans" action from FEATURE_ADDITIONS #4), via a
    new pure `markup_ops.merge_markups` + tests. Only offered when the alt
    cut's `card_text` matches the canonical exactly, so span offsets transfer.
