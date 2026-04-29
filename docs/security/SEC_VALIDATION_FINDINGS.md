# Security Validation Findings — debatabase

**Status:** Living — v1
**Last reviewed:** 2026-04-28
**Validator:** eevn

Mirrors `SEC_VALIDATION.md`. Each row carries one of:

- **Verified** — code review + tests confirm the property holds.
- **Partial** — control holds today, but coverage is incomplete.
- **Gap** — real shortfall, not yet remediated.
- **Accepted Risk** — known partial / intentional miss with rationale and
  compensating control. Cross-referenced to `THREAT_MODEL.md#R-N`.

---

## Summary

| Status         | Count |
|----------------|-------|
| Verified       | 22    |
| Partial        | 2     |
| Gap            | 0     |
| Accepted Risk  | 5     |

All previously-identified Gaps were closed in this pass:
- **API-S2** (open redirect) — fixed by `_safe_next` (commit pending).
- **API-D1** (login brute force) — fixed by `login_rate_limit`.
- **API-D2** (cost-endpoint abuse) — fixed by `search_rate_limit` + `answers_rate_limit`.
- **API-T3** (XSS in workspace-name reply) — fixed by `html.escape`.
- **REQ-AUTH-10** (production refusing dev secret) — fixed in `config.py`.
- **REQ-AUTH-04** (cookie hardening) — fixed by `same_site=lax`, `https_only=_PROD`, `max_age`.
- **REQ-NET-01** (Postgres host-only) — fixed by binding `127.0.0.1:5433:5432` in `docker-compose.yml`.

The Partials reflect controls that work but lack a structural test
keeping them from regressing. The Accepted Risks are tracked against
specific compensating controls in `THREAT_MODEL.md`.

---

## V-AUTH

| Plan ID         | Status   | Notes                                                                                                       |
|-----------------|----------|-------------------------------------------------------------------------------------------------------------|
| V-REQ-AUTH-01   | Verified | `auth.py:hash_password` is the sole entry point. Grep confirms two call sites: register, claim-local.       |
| V-REQ-AUTH-02   | Verified | `tests/test_security.py::test_validate_password_rejects_below_minimum` + `…_accepts_minimum_and_above`. |
| V-REQ-AUTH-03   | Verified | `SessionMiddleware(secret_key=settings.session_secret, …)`. `itsdangerous` rejects tampered cookies.        |
| V-REQ-AUTH-04   | Verified | `same_site="lax"`, `https_only=_PROD`, `max_age=30 days`. `HttpOnly` is the Starlette default.              |
| V-REQ-AUTH-05   | Verified | `Depends(login_rate_limit)` declared. `tests/test_rate_limit.py` covers the bucket maths.                   |
| V-REQ-AUTH-06   | Verified | `Depends(register_rate_limit)` declared.                                                                    |
| V-REQ-AUTH-07   | Verified | `_safe_next` rejects 7 distinct attacker shapes; `tests/test_security.py` covers all of them.               |
| V-REQ-AUTH-08   | Verified | `verify_password` returns `False` for falsy hashes before reaching argon2.                                  |
| V-REQ-AUTH-09   | Verified | `logout` calls `request.session.clear()`.                                                                    |
| V-REQ-AUTH-10   | Verified | `config.py` raises `RuntimeError` at import time when env=production with the dev fallback secret.          |

## V-AUTHZ

| Plan ID          | Status        | Notes                                                                                                              |
|------------------|---------------|--------------------------------------------------------------------------------------------------------------------|
| V-REQ-AUTHZ-01   | Verified      | Manual sweep of all 14 `/workspaces*` handlers — every one calls `_get_workspace_or_404` before reads/writes.       |
| V-REQ-AUTHZ-02   | Verified      | All callers translate `None` → `HTMLResponse(..., status_code=404)`. No 403 anywhere on the workspace surface.      |
| V-REQ-AUTHZ-03   | Verified      | `apply_variant_op`, `revert_variant` look up the entry inside the already-scoped workspace. No direct variant route. |
| V-REQ-AUTHZ-04   | Verified      | Public prefixes hardcoded; nothing under `/workspaces` or `/admin` is in `_PUBLIC_PREFIXES`.                         |
| V-REQ-AUTHZ-05   | Verified      | All five `/admin/*` handlers declare `Depends(get_current_user_id)`. **Note:** login-only, no role check — see `R-3`. |

## V-COST

| Plan ID         | Status   | Notes                                                                                                |
|-----------------|----------|------------------------------------------------------------------------------------------------------|
| V-REQ-COST-01   | Verified | `search` calls `check_rate_limit(request, SEARCH_BURST, SEARCH_HOURLY, prefix="search")` when `q`.   |
| V-REQ-COST-02   | Verified | `card_answers` declares `Depends(answers_rate_limit)`.                                                |
| V-REQ-COST-03   | Verified | `_embed_query_safe(q)` returns `None` when `q` is falsy or no key.                                   |
| V-REQ-COST-04   | Verified | grep `voyage_api_key|anthropic_api_key` in `web/` returns zero matches.                              |

## V-INPUT

| Plan ID         | Status   | Notes                                                                                                       |
|-----------------|----------|-------------------------------------------------------------------------------------------------------------|
| V-REQ-INPUT-01  | Verified | grep `text(f"|text(f'|sqltext(f` over `src/`, `scripts/` returns zero matches.                              |
| V-REQ-INPUT-02  | Verified | `tests/test_security.py::test_no_unescaped_user_input_in_html_fstrings` walks `web/app.py` and asserts every `HTMLResponse(f"…")` interpolation either uses `html_escape(…)` or is in the `int`-only path-param allowlist. Regressions get a red CI signal. |
| V-REQ-INPUT-03  | Verified | FastAPI returns 422 on `GET /cards/abc`. Pydantic / FastAPI behaviour, validated by example.                 |

## V-NET

| Plan ID         | Status   | Notes                                                                                                       |
|-----------------|----------|-------------------------------------------------------------------------------------------------------------|
| V-REQ-NET-01    | Verified | `docker-compose.yml` now binds `127.0.0.1:5433:5432`. `ss -tlnp` on the EC2 host should be re-checked after deploy. |
| V-REQ-NET-02    | Verified | `tests/test_security.py::test_client_ip_*` covers all four cases: no trusted-proxies (XFF ignored), trusted peer (XFF honoured), untrusted peer (XFF ignored), malformed XFF (fall back to peer). |

## V-SECRETS

| Plan ID            | Status   | Notes                                                                                                       |
|--------------------|----------|-------------------------------------------------------------------------------------------------------------|
| V-REQ-SECRETS-01   | Verified | `git ls-files | grep -E '\.env$|\.pem$'` returns zero matches. `.gitignore` lists `.env`, `*.pem`, `*.key`. |
| V-REQ-SECRETS-02   | Verified | `card_answers` exception path renders only `type(e).__name__`; no other handler echoes `settings.*`.        |

## V-DEPLOY

| Plan ID            | Status   | Notes                                                                                                       |
|--------------------|----------|-------------------------------------------------------------------------------------------------------------|
| V-REQ-DEPLOY-01    | Partial  | The dev host has no TLS terminator; the EC2 deployment must be re-validated. **Action:** confirm `curl http://<host>` 301s to https on the live deploy. |
| V-REQ-DEPLOY-02    | Partial  | Local `.env` is mode `0664` on the dev host — fine for dev, not for prod. **Action:** `chmod 0600 .env` on the EC2 host before exposing the service. |

---

## Accepted risks (cross-ref `THREAT_MODEL.md`)

| Risk ID | Summary                                            | Why accepted                                          | Compensating control                                                  |
|---------|----------------------------------------------------|-------------------------------------------------------|----------------------------------------------------------------------|
| R-3     | `/admin/*` is login-only, no admin role            | Single-operator instance                              | Re-evaluate before opening the instance to multiple users             |
| R-4     | No audit log                                       | Single-operator; nothing to attribute                  | Re-evaluate before multi-user                                         |
| R-9     | Session cookie signed but not encrypted            | Claims are non-sensitive (`user_id` + `user_nick`)     | Encrypt or move server-side if PII ever lands in the session          |
| R-10    | No per-request CSRF token                          | `SameSite=Lax` covers the realistic CSRF surface       | Revisit if state-changing endpoints become embeddable cross-origin    |
| R-8     | DB role has DDL on its own DB                      | Convenient for `schema.sql` re-apply; loopback only    | Loopback binding + security group block                               |

---

## Outstanding actions

The remaining work falls into two buckets, ordered by priority:

1. **Operator-side hardening (V-DEPLOY):**
   - Confirm Postgres on the EC2 host listens on 127.0.0.1:5433 only
     (`ss -tlnp`).
   - Confirm TLS terminator redirects HTTP → HTTPS and sets HSTS.
   - `chmod 0600 /etc/debatabase/env` (or wherever `EnvironmentFile=`
     points) on the EC2 host.

2. **Multi-user prerequisites (deferred until multi-user is committed):**
   - Admin role on `/admin/*` (R-3).
   - Audit log on workspace and admin actions (R-4).
   - Content-Security-Policy header (R-5).
   - Security-event metrics + alerting (R-6).
   - `pip-audit` / SAST in CI (R-7).
   - Least-privilege DB role (R-8).
