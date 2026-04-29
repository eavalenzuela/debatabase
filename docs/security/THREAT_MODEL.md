# Threat Model — debatabase

**Status:** Living — v1
**Last reviewed:** 2026-04-28
**Method:** STRIDE per component, with explicit trust boundaries, threat
actors, and residual risks. Tailored to a single-host, single-user
deployment.

---

## 1. Purpose & scope

Enumerates threats against the debatabase EC2 deployment and the controls
that mitigate them. Derived from `SECURITY.md`; feeds
`SEC_REQUIREMENTS.md` and `SEC_INVARIANTS.md`.

**In scope:**
- Auth flow (nick + password, session cookie)
- Workspace / variant cross-user isolation
- The `/admin/*` endpoints
- Cost-bearing endpoints (Voyage + Anthropic)
- HTMX request surface and CSRF posture
- Postgres exposure
- Wiki-ingest input handling

**Out of scope:**
- Operator's AWS account hardening (IAM, KMS, VPC)
- Anthropic / Voyage internals — see TA-7
- Browser / device security
- Physical security of the EC2 instance

---

## 2. System overview

```
              ┌─── TB-1 ────┐
              │             │
   ┌──────────▼──┐   HTTPS  │   ┌──────────────┐  loopback  ┌──────────────┐
   │   Browser   │──────────┼──▶│  uvicorn /   │────TB-2───▶│  PostgreSQL  │
   │  (operator) │          │   │   FastAPI    │            │  + pgvector  │
   └─────────────┘          │   └─┬───────┬────┘            └──────────────┘
                            │     │       │
                            │     │ TB-3  │ TB-4
                            │     ▼       ▼
                            │  ┌──────┐ ┌──────────┐
                            │  │Voyage│ │Anthropic │
                            │  │ API  │ │  API     │
                            │  └──────┘ └──────────┘
              ┌─────────────┘
              │ TB-5
              ▼
         (operator
          shell over
            SSH)
```

---

## 3. Trust boundaries

| ID    | Crossing                                  | Enforcement                                                                          |
|-------|-------------------------------------------|--------------------------------------------------------------------------------------|
| TB-1  | Internet → TLS terminator → app           | TLS 1.2+ at the proxy; rate limits on cost/auth endpoints; session-cookie auth on private routes |
| TB-2  | App server → Postgres                     | Loopback only (Docker network bound to 127.0.0.1); least-privilege DB user (deferred) |
| TB-3  | App server → Voyage AI                    | HTTPS via SDK; cert validation; API key in `.env`                                     |
| TB-4  | App server → Anthropic API                | HTTPS via SDK; cert validation; API key in `.env`                                     |
| TB-5  | Operator → EC2 host                       | SSH via key file; security group locks port 22 to operator IP                         |

---

## 4. Assets

| Asset                          | Confidentiality | Integrity | Availability | Notes                                           |
|--------------------------------|-----------------|-----------|--------------|-------------------------------------------------|
| User password hashes           | High            | High      | Medium       | argon2id; never logged                          |
| Session cookies                | High            | High      | Medium       | Signed but **not** encrypted (R-9)              |
| `SESSION_SECRET`               | Very High       | High      | High         | Compromise = forge any user's session           |
| `VOYAGE_API_KEY`               | High            | High      | High         | Compromise = bill abuse                         |
| `ANTHROPIC_API_KEY`            | High            | High      | High         | Compromise = bill abuse                         |
| Card corpus + embeddings       | Low             | Medium    | Medium       | Public; integrity matters for search quality    |
| Workspaces + variants          | Medium          | High      | Medium       | Per-user; cross-user access is a real bug       |

---

## 5. Threat actors

| ID    | Actor                                | Capabilities / goal                                                              |
|-------|--------------------------------------|----------------------------------------------------------------------------------|
| TA-1  | Unauthenticated internet attacker    | Probe endpoints; brute-force login; abuse cost endpoints; scrape public corpus   |
| TA-2  | Authenticated low-privilege user     | Cross-user workspace/variant access; admin-action abuse; cost-endpoint abuse via auth bucket |
| TA-3  | Stolen session cookie holder         | Same as the original user until cookie expiry; no server-side revocation         |
| TA-4  | Malicious operator (insider)         | Direct DB access, log access, ability to deploy code                              |
| TA-5  | Compromised Voyage / Anthropic API   | Returns malicious embedding values or text — bounded by how the app uses the response |
| TA-6  | Network-adjacent attacker (proxy)    | Header injection (`X-Forwarded-For`) to bypass rate limits                        |
| TA-7  | Compromised dependency               | Code execution via a poisoned PyPI release                                        |

---

## 6. STRIDE per component

### 6.1 App server (FastAPI / uvicorn)

| ID     | Threat                                                       | STRIDE | Actor      | Control                                                                                                | Evidence                                                              | Residual |
|--------|--------------------------------------------------------------|--------|------------|--------------------------------------------------------------------------------------------------------|-----------------------------------------------------------------------|----------|
| API-S1 | Forged session cookie                                        | S      | TA-1       | `itsdangerous` HMAC over `SESSION_SECRET`; tamper rejected by middleware                               | `web/app.py:124` (`SessionMiddleware` config); `_safe_next` for redirects | —        |
| API-S2 | Open-redirect via `?next=` to phish login                    | S      | TA-1       | `_safe_next` only allows same-origin paths starting with `/`                                           | `web/app.py:_safe_next`; `tests/test_security.py`                     | —        |
| API-T1 | SQL injection                                                | T      | TA-1, TA-2 | All queries via SQLAlchemy ORM or `text()` with bound params                                           | grep over `web/app.py`; no string-built SQL                           | —        |
| API-T2 | Stored / reflected XSS via card markup                       | T      | TA-2       | `render_card` walks markup spans and emits HTML with `html.escape()` on every text chunk               | `web/render.py:_render_full`                                          | —        |
| API-T3 | XSS via raw HTMLResponse f-strings (workspace name reply)    | T      | TA-2       | User-controlled fragments escaped via `html.escape` in `add_workspace_entry`, `rename_workspace`       | `web/app.py:rename_workspace`, `add_workspace_entry`                  | —        |
| API-R1 | Action without audit trail                                   | R      | TA-2, TA-4 | **Not implemented.** Logout, workspace edits, admin actions are not logged.                            | —                                                                     | R-4      |
| API-I1 | IDOR — fetch another user's workspace                        | I      | TA-2       | `_get_workspace_or_404` filters by `user_id`; returns 404 (not 403) on miss                            | `web/app.py:_get_workspace_or_404`                                    | —        |
| API-I2 | Session-cookie claim disclosure                              | I      | TA-1       | Cookie is signed, not encrypted — `user_id` and `user_nick` are base64-readable. Acceptable: the values are non-sensitive | —                                                                     | R-9      |
| API-D1 | Brute-force login                                            | D      | TA-1       | Per-IP rate limit: 10/min, 60/hr                                                                       | `rate_limit.py:LOGIN_*`                                               | —        |
| API-D2 | Cost-endpoint abuse (Voyage / Anthropic billing)             | D      | TA-1, TA-2 | Per-IP-or-user rate limits on `/search?q=…` and `/cards/{id}/answers`                                  | `rate_limit.py:SEARCH_*`, `ANSWERS_*`                                 | —        |
| API-D3 | Resource exhaustion via huge body                            | D      | TA-1       | uvicorn defaults; no upload endpoints                                                                  | —                                                                     | Acceptable today |
| API-E1 | Privilege escalation via `/admin/*`                          | E      | TA-2       | **Login-gate only — no admin role.** Any authenticated user can act on admin endpoints                 | `web/app.py:proposed_tags_index`, `duplicates_index`                  | R-3      |
| API-E2 | CSRF on state-changing forms                                 | E      | TA-1       | `SameSite=Lax` on the session cookie blocks cross-site form POST and credentialed fetch                 | `web/app.py:SessionMiddleware` config                                 | Partial — no per-request token, see R-10 |

### 6.2 Database

| ID    | Threat                                  | STRIDE | Actor | Control                                                                              | Evidence                                | Residual |
|-------|-----------------------------------------|--------|-------|--------------------------------------------------------------------------------------|-----------------------------------------|----------|
| DB-S1 | Imposter database (MITM)                | S      | TA-6  | Loopback connection only; not reachable from outside the host                        | `docker-compose.yml`                    | —        |
| DB-T1 | Audit log tampering                     | T      | TA-4  | **Not applicable today** — no audit log exists                                       | —                                       | R-4      |
| DB-I1 | Backup leakage                          | I      | TA-4  | Operator-managed; not in app scope                                                   | —                                       | —        |
| DB-E1 | App user has DDL on its own database    | E      | TA-1+code | Acceptable today (single-app DB); revisit if the role is reused elsewhere         | `docker-compose.yml`                    | R-8      |

### 6.3 Voyage / Anthropic boundaries

| ID     | Threat                                          | STRIDE | Actor | Control                                                                              | Evidence                                | Residual |
|--------|-------------------------------------------------|--------|-------|--------------------------------------------------------------------------------------|-----------------------------------------|----------|
| EXT-S1 | Imposter API endpoint (DNS hijack / MITM)       | S      | TA-6  | TLS validation by SDK                                                                | `voyageai`, `anthropic` SDKs            | —        |
| EXT-T1 | Malicious response affects app behaviour        | T      | TA-5  | Embedding values are floats consumed by pgvector — bounded. Haiku text is treated as a search query string and embedded; the inverse claim is rendered via Jinja auto-escape | `answer_finder.py`, `embeddings.py`     | —        |
| EXT-D1 | Cost-spike via runaway client loop              | D      | TA-1, TA-2 | Rate limits on `/search`, `/cards/{id}/answers`                                    | `rate_limit.py`                         | —        |

### 6.4 Browser / web UI

| ID    | Threat                                | STRIDE | Actor | Control                                                                              | Evidence                                | Residual |
|-------|---------------------------------------|--------|-------|--------------------------------------------------------------------------------------|-----------------------------------------|----------|
| UI-T1 | XSS via card text                     | T      | TA-2  | `render_card` escapes all text                                                       | `web/render.py`                         | —        |
| UI-T2 | XSS via workspace name                | T      | TA-2  | `html.escape` applied to `ws.name` in raw-HTMLResponse paths; Jinja auto-escapes elsewhere | `web/app.py`                            | —        |
| UI-I1 | Sensitive data in browser cache       | I      | TA-3  | Authenticated pages are served fresh; no extra `Cache-Control` header today          | —                                       | Low risk: no PII beyond nickname rendered |
| UI-S1 | CSRF                                  | S      | TA-1  | `SameSite=Lax`; no per-request token                                                 | —                                       | R-10     |

### 6.5 Wiki ingest path

| ID    | Threat                                | STRIDE | Actor | Control                                                                              | Evidence                                | Residual |
|-------|---------------------------------------|--------|-------|--------------------------------------------------------------------------------------|-----------------------------------------|----------|
| ING-T1 | Path traversal via filename          | T      | TA-1  | Filenames are read via `Path.rglob("*.docx")` from a directory the operator passes; contents are never written back, only parsed | `scripts/ingest_wiki_dump.py`           | —        |
| ING-T2 | Malicious .docx triggers parser RCE  | T      | TA-1  | `python-docx` is the only parser used; no exec/eval                                  | `parser/extract.py`                     | —        |
| ING-D1 | Re-ingest blow-up on huge dump       | D      | TA-4  | Operator-controlled input; SHA-256 dedup short-circuits already-seen files            | `ingest_wiki_dump.py:_sha256` + `wiki_uploads.file_sha256` UNIQUE | — |

---

## 7. Cross-cutting threats

### 7.1 Cross-user isolation

`workspaces`, `workspace_entries`, and `card_variants` are scoped by
`user_id` (workspaces) or `workspace_id` (entries, variants). Every
route under `/workspaces` calls `_get_workspace_or_404` first. Variants
inherit scope from the workspace they belong to. Cross-user GET / PATCH
/ DELETE returns 404, never 403 — see `SEC_VALIDATION_FINDINGS.md`.

### 7.2 Audit integrity

Not yet implemented. See `R-4`. Until then, the database is the only
source of state and changes are not retroactively traceable.

### 7.3 Supply chain

`uv.lock` pins all dependency hashes. No CI-level scanning yet
(`R-7`). Anthropic + Voyage SDKs are pulled from PyPI at install time
with verified checksums.

### 7.4 Secrets handling

Secrets live in `.env` (local) and the systemd `EnvironmentFile=`
(production). Never in git, never in logs. The `.env` file is owned by
the service user with mode `0600`.

---

## 8. Residual & accepted risks

| ID    | Risk                                                              | Rationale / why accepted                                                                              | Compensating control                                                                              |
|-------|-------------------------------------------------------------------|-------------------------------------------------------------------------------------------------------|---------------------------------------------------------------------------------------------------|
| R-1   | HSTS not enforced from the app                                     | Should be set at the TLS-terminating proxy; tracked there                                              | Use `Strict-Transport-Security: max-age=31536000; includeSubDomains` at nginx / ALB              |
| R-2   | No server-side session revocation                                  | Acceptable for single-user; no server state needed for stateless cookie sessions                       | Logout clears client cookie; `SESSION_SECRET` rotation invalidates all sessions globally          |
| R-3   | `/admin/*` endpoints are login-only — no admin role                | Single-operator instance; the only logged-in user *is* the admin                                        | Closed off when more than one user exists; tracked as a SEC_REQUIREMENT for the multi-user phase  |
| R-4   | No audit log                                                       | Single-operator; nothing to attribute                                                                  | Re-evaluate before opening to multi-user                                                          |
| R-5   | No Content-Security-Policy                                         | Templates are auto-escaping and there's no `unsafe-inline`-needing JS in our own code                  | Add a default-src `'self'` plus the unpkg/jsdelivr origins for HTMX + Sortable when CDN-pinning  |
| R-6   | No security-event alerting                                         | Single-user volume is too low to warrant a metrics stack                                                | Manual log review post-incident; rate-limit triggers visible via 429 responses                    |
| R-7   | No dependency / SAST scanning in CI                                | No CI yet; local `uv.lock` provides hash-pinning                                                       | Run `pip-audit` manually before deploy; revisit when CI is added                                  |
| R-8   | DB role has DDL grants on its own DB                               | Convenient for `schema.sql` re-apply; the user is local-only                                            | The DB user can't reach the host network outside the Docker bridge; loopback-only port binding  |
| R-9   | Session cookie signed, not encrypted (claims readable)             | Claims are `user_id` (int) and `user_nick` (already public via the URL); no PII                         | If sensitive claims are added, switch to encrypted cookies or move to server-side sessions       |
| R-10  | No per-request CSRF token                                          | `SameSite=Lax` covers the realistic CSRF surface for a same-origin HTMX app; per-request tokens add complexity | Revisit if any state-changing endpoint becomes embeddable cross-origin or if multi-user lands     |

Every risk above must be re-evaluated whenever its compensating
control changes, and certainly before the instance is exposed to
multiple users.

---

## 9. Change log

| Date       | Change                                                                  | Author |
|------------|-------------------------------------------------------------------------|--------|
| 2026-04-28 | Initial threat enumeration after rate-limit + cookie-hardening landed.  | eevn   |
