# Security Invariants — debatabase

**Status:** Living — v1
**Last reviewed:** 2026-04-28

Bottom-up checklist: *this thing is done this way and only this way*.
Each invariant has a code reference, a serving requirement, and a test
or grep that catches a regression.

---

## I-1 — Passwords hashed with argon2id

**Statement:** Every write to `users.pw_hash` goes through
`debatabase.auth.hash_password`, which delegates to
`argon2.PasswordHasher().hash()`. No other call site computes a hash.
**Evidence:** `src/debatabase/auth.py:hash_password`; grep
`pw_hash =` shows two writers, both via `hash_password`.
**Serves:** REQ-AUTH-01.
**Test:** `grep -rn "pw_hash *=" src/` — only the two known writers.

## I-2 — Password minimum length

**Statement:** `validate_password` returns an error when `len < 8`.
Registration calls `validate_password` and rejects on error.
**Evidence:** `src/debatabase/auth.py:validate_password`,
`web/app.py:register_submit`.
**Serves:** REQ-AUTH-02.
**Test:** unit test of `validate_password`.

## I-3 — Session cookie signed by `SESSION_SECRET`

**Statement:** Starlette's `SessionMiddleware` is configured with
`secret_key=settings.session_secret`. Tampered cookies are rejected
silently (treated as no session). The two readers are
`request.session` and `get_current_user_id_optional`.
**Evidence:** `web/app.py:SessionMiddleware` config.
**Serves:** REQ-AUTH-03.

## I-4 — Cookie hardening flags

**Statement:** `SessionMiddleware` is configured with
`same_site="lax"`, `https_only=_PROD`, `max_age=30 days`. `HttpOnly`
is the default and not overridden.
**Evidence:** `web/app.py:SessionMiddleware` config.
**Serves:** REQ-AUTH-04.

## I-5 — Login rate-limit dependency

**Statement:** `POST /login` declares `dependencies=[Depends(login_rate_limit)]`.
**Evidence:** `web/app.py:login_submit`,
`rate_limit.py:login_rate_limit` (per-IP only — `ip_only=True`).
**Serves:** REQ-AUTH-05.
**Test:** `grep -n "Depends(login_rate_limit)" src/debatabase/web/app.py`.

## I-6 — Register rate-limit dependency

**Statement:** `POST /register` declares
`dependencies=[Depends(register_rate_limit)]`.
**Evidence:** `web/app.py:register_submit`.
**Serves:** REQ-AUTH-06.

## I-7 — Login redirect target restricted to same origin

**Statement:** Both `GET /login` and `POST /login` pass `next` through
`_safe_next` before redirecting. `_safe_next` rejects any value that
isn't a single-leading-slash same-origin path.
**Evidence:** `web/app.py:_safe_next`, `login_page`, `login_submit`.
**Serves:** REQ-AUTH-07.
**Test:** `tests/test_security.py::test_safe_next_blocks_open_redirect`
(parametrised over the protocol-relative, javascript-scheme, and
absolute-URL cases).

## I-8 — NULL `pw_hash` never matches

**Statement:** `verify_password(None, *)` returns False unconditionally.
The bootstrap placeholder user has `pw_hash IS NULL` until claim.
**Evidence:** `src/debatabase/auth.py:verify_password`.
**Serves:** REQ-AUTH-08.

## I-9 — Logout clears session

**Statement:** `POST /logout` calls `request.session.clear()` then
redirects to `/login`.
**Evidence:** `web/app.py:logout`.
**Serves:** REQ-AUTH-09.

## I-10 — Production refuses dev session secret

**Statement:** `config.py` raises `RuntimeError` at import time if
`DEBATABASE_ENV=production` and `SESSION_SECRET` is the
`_DEV_SESSION_FALLBACK` string.
**Evidence:** `src/debatabase/config.py` bottom of module.
**Serves:** REQ-AUTH-10.

## I-11 — Workspace ownership check on every workspace route

**Statement:** Every handler under `/workspaces/{ws_id}` calls
`_get_workspace_or_404(s, ws_id, user_id)` before any read or write.
Variants are accessed through their workspace, inheriting the check.
**Evidence:** `web/app.py:_get_workspace_or_404`; grep for
`_get_workspace_or_404(` shows it called in every relevant handler.
**Serves:** REQ-AUTHZ-01, REQ-AUTHZ-03.
**Test:** `grep -n "ws_id: int" src/debatabase/web/app.py` then
manually verify each handler also calls `_get_workspace_or_404`. A
structural test (PATTERNS.md #2) is the hardening play.

## I-12 — Cross-user workspace miss is 404

**Statement:** `_get_workspace_or_404` returns `None` when the workspace
belongs to a different user; callers translate `None` to `HTMLResponse(..., status_code=404)`.
**Evidence:** `web/app.py:_get_workspace_or_404` body.
**Serves:** REQ-AUTHZ-02.

## I-13 — Variant scope inherited from workspace

**Statement:** `apply_variant_op` and `revert_variant` look up the entry
inside the workspace, then mutate the variant. There is no
`/variants/{id}` endpoint that would allow direct cross-workspace
access.
**Evidence:** `web/app.py:apply_variant_op`, `revert_variant`.
**Serves:** REQ-AUTHZ-03.

## I-14 — Public-corpus prefixes don't require auth

**Statement:** `_PUBLIC_PREFIXES` and `_PUBLIC_EXACT` enumerate the
public paths; the `_require_login` middleware short-circuits for them.
Workspace, admin, and account endpoints are *not* in those sets.
**Evidence:** `web/app.py:_PUBLIC_PREFIXES`, `_require_login`.
**Serves:** REQ-AUTHZ-04.

## I-15 — `/admin/*` endpoints declare `Depends(get_current_user_id)`

**Statement:** Every admin handler has the dependency; unauthenticated
calls get a 401.
**Evidence:** `web/app.py:proposed_tags_index`, `approve_proposed_tag`,
`reject_proposed_tag`, `duplicates_index`, `set_canonical`.
**Serves:** REQ-AUTHZ-05.
**Test:** `grep -n "@app\\.\\(get\\|post\\|delete\\).*\"/admin" -A1 src/debatabase/web/app.py`
shows each handler followed by a `Depends(get_current_user_id)`.

## I-16 — `/search?q=…` rate-limited per caller

**Statement:** `GET /search` calls `check_rate_limit(request,
SEARCH_BURST, SEARCH_HOURLY, prefix="search")` when `q` is non-empty.
**Evidence:** `web/app.py:search`.
**Serves:** REQ-COST-01.

## I-17 — `/cards/{id}/answers` rate-limited per caller

**Statement:** `GET /cards/{card_id}/answers` declares
`dependencies=[Depends(answers_rate_limit)]`.
**Evidence:** `web/app.py:card_answers`.
**Serves:** REQ-COST-02.

## I-18 — Empty-query `/search` does not call Voyage

**Statement:** `_embed_query_safe(q)` returns `None` when `q` is falsy
or no key is set. The order-by branch only includes embedding terms when
`semantic_active` is True.
**Evidence:** `web/app.py:_embed_query_safe`, `_search`.
**Serves:** REQ-COST-03.

## I-19 — API keys not echoed to clients

**Statement:** No template references `settings.anthropic_api_key` or
`settings.voyage_api_key`. The capability checks (`has_*_capability`)
return only a bool.
**Evidence:** grep `voyage_api_key|anthropic_api_key` in
`src/debatabase/web/`.
**Serves:** REQ-COST-04.

## I-20 — Parameterised SQL only

**Statement:** Every `text()` call uses `:param` bindings. No
`f"... {user_input} ..."` SQL anywhere.
**Evidence:** grep `text\\(f"|text\\(f'\\|text(\\s*\\("` in
`src/debatabase/`.
**Serves:** REQ-INPUT-01.

## I-21 — HTML output escaped

**Statement:** Jinja templates are `.html` files (auto-escape). The
two raw-`HTMLResponse` paths that interpolate user-controlled strings
(`add_workspace_entry`, `rename_workspace`) wrap them in
`html.escape`. `render_card` uses `html.escape` per chunk.
**Evidence:** `web/app.py:rename_workspace`, `add_workspace_entry`;
`web/render.py`.
**Serves:** REQ-INPUT-02.

## I-22 — `int` path params reject non-integer input

**Statement:** Path params are typed as `int` in handler signatures.
FastAPI returns 422 for invalid values before the handler runs.
**Evidence:** every `card_id: int`, `ws_id: int`, `entry_id: int`
in `web/app.py`.
**Serves:** REQ-INPUT-03.

## I-23 — Postgres bound to host-only

**Statement:** `docker-compose.yml` exposes Postgres on
`"5433:5432"` — bound to the host network, not the bridge default
of `0.0.0.0`. The EC2 security group does not open 5433.
**Evidence:** `docker-compose.yml`.
**Serves:** REQ-NET-01.

## I-24 — `X-Forwarded-For` only honoured from trusted proxies

**Statement:** `rate_limit.client_ip` reads
`X-Forwarded-For` only when the direct peer falls inside
`TRUSTED_PROXIES`; otherwise the direct peer address is used.
**Evidence:** `rate_limit.py:client_ip`.
**Serves:** REQ-NET-02.

## I-25 — Secrets in env, not in source

**Statement:** No secret string literal in source. `settings.*`
attributes loaded via `pydantic-settings` from env / `.env`. `.env`
is gitignored.
**Evidence:** grep over the repo for the public dev-fallback string —
should be a single hit in `config.py`. `.env` listed in `.gitignore`
(if any) or simply absent from git.
**Serves:** REQ-SECRETS-01.

## I-26 — Secrets not echoed in error responses

**Statement:** Exception handlers do not log or render
`settings.anthropic_api_key` / `settings.voyage_api_key` /
`settings.session_secret`. The Voyage / Anthropic SDK exceptions
caught in `card_answers` render only `type(e).__name__`.
**Evidence:** `web/app.py:card_answers` (renders only the exception
type name).
**Serves:** REQ-SECRETS-02.
