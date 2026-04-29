# Security Architecture — debatabase

**Status:** Living — v1
**Last reviewed:** 2026-04-28
**Owner:** eavalenzuela7@gmail.com (single-maintainer)

This is the architectural baseline. It describes how debatabase is *built*
to be secure, and is the input to `THREAT_MODEL.md`,
`SEC_REQUIREMENTS.md`, `SEC_INVARIANTS.md`, and the validation docs.

---

## 1. System overview

```
   ┌──────────┐   HTTPS    ┌────────────────────────┐   ──TLS──▶ Voyage AI (embeddings)
   │  Browser │───────────▶│   FastAPI / uvicorn    │   ──TLS──▶ Anthropic API (Haiku)
   │ (single  │            │   single process       │
   │  user)   │            └────────────┬───────────┘
   └──────────┘                         │ TCP (host-only)
                                        ▼
                              ┌──────────────────┐
                              │  PostgreSQL 16   │   (Docker container,
                              │  + pgvector      │    bound to 127.0.0.1:5433)
                              └──────────────────┘
```

Components in scope:
- **Browser** — operator's browser; HTMX over HTTPS; no SPA framework, no
  service worker, no client-side persistence beyond the session cookie.
- **App server** — FastAPI on uvicorn, single uvicorn process. Hosts the
  search UI, the card-detail view, the workspace editor, and the
  admin/review endpoints. Renders Jinja templates server-side and
  returns HTMX fragments for in-page updates.
- **PostgreSQL 16 + pgvector** — primary store. Cards, tags, sources,
  users, workspaces, embeddings (HNSW index for cosine distance).
- **Voyage AI** — embedding provider (`voyage-3-lite`). Called on
  `/search` (when a query is present) and during embedding backfills.
- **Anthropic API** — Claude Haiku. Called on `/cards/{id}/answers`
  (inverse-claim generation) and during ingest (`tagger.py`).

Out of scope:
- End-user device security.
- Anthropic / Voyage internals — handled as third-party trust boundaries.
- Operator's own AWS account hardening (IAM, KMS, VPC) — separately
  managed; this doc only covers what the application does.

---

## 2. Transport security

- **External ingress:** TLS terminates at the EC2-fronting load balancer
  (or nginx, depending on deployment). uvicorn behind it speaks HTTP on
  the loopback interface. The `https_only` flag on `SessionMiddleware`
  is set when `DEBATABASE_ENV=production`; `Secure` cookies will not be
  sent over the loopback hop, which is fine because the loopback hop
  isn't internet-reachable.
- **HSTS:** Set at the proxy layer. Not yet wired into the EC2 unit. See
  `THREAT_MODEL.md#R-1`.
- **Database:** Postgres runs on the same host inside a Docker network.
  TLS is **not** required for the loopback hop. If the DB is ever
  moved off-box, switch the connection string to require
  `sslmode=verify-full`.
- **Outbound:** All outbound calls (Voyage, Anthropic) are HTTPS via
  the official SDKs, which validate certs.

---

## 3. Authentication

### 3.1 End users

- **Mechanism:** local nickname + password. IRC-style. No email, no
  password reset, no MFA, no SSO. If a user loses their password they
  lose their account; this is intentional (`FEATURE_ADDITIONS.md` #6).
- **Hashing:** `argon2id` via `argon2-cffi`'s default `PasswordHasher()`
  parameters. The library updates these as recommendations evolve.
- **Password policy:** minimum 8 characters. No max, no complexity rules,
  no breach checks.
- **Nicknames:** unique (Postgres unique index on `users.nickname`),
  whitespace-stripped, max 32 chars, no embedded whitespace.
- **Bootstrap:** the very first registration on a fresh install can opt
  into "claim the existing pre-auth `local` user" so the seed
  workspace data isn't orphaned. After any real user exists, this path
  is closed off.
- **Session:** Starlette `SessionMiddleware`. The session is a
  signed-but-not-encrypted cookie carrying `{user_id, user_nick}`,
  signed with `SESSION_SECRET` via `itsdangerous`. Cookie attributes:
  `HttpOnly` (default), `SameSite=Lax`, `Secure` when
  `DEBATABASE_ENV=production`, `max_age=30 days`.
- **Logout:** clears the cookie client-side. There is no server-side
  revocation list — see `THREAT_MODEL.md#R-2`.
- **Account lockout:** none. Brute-force defence is per-IP rate
  limiting only (`rate_limit.py:LOGIN_BURST` + `LOGIN_HOURLY`).

### 3.2 Programmatic clients

**Not implemented.** debatabase has no API-key auth, no machine tokens,
no service-to-service auth. Every authenticated request comes from a
browser session.

### 3.3 SSO

**Not implemented.** No OIDC, no SAML.

---

## 4. Authorization

- **Model:** owner-only. There are no roles. Every authenticated user
  owns a set of workspaces; no other user can read or write them.
- **Public corpus:** `/`, `/search`, `/cards/*`, `/sources/*`,
  `/tags/*`, `/analyticals` are reachable without auth. The card
  corpus is intentionally public.
- **Workspace scoping:** `_get_workspace_or_404` enforces
  `ws.user_id == user_id` on every workspace access. Cross-user
  attempts return 404 (per PATTERNS.md #6: 404 not 403, to avoid
  leaking ID validity).
- **Variant scoping:** `card_variants` rows reference `workspace_id`;
  the route layer always loads them via the workspace they belong to,
  so scope is inherited.
- **Admin endpoints:** `/admin/proposed-tags*` and `/admin/duplicates*`
  are login-gated only. **There is no admin role check.** Any
  authenticated user can approve a proposed tag or set a canonical
  card. Acceptable today (single-user instance) but tracked as
  `THREAT_MODEL.md#R-3` and slated for fix when the instance opens to
  more users.

---

## 5. Cryptography & key management

- **Password hashing:** `argon2id`, library defaults.
- **Session cookie signing:** HMAC-SHA256 via `itsdangerous`. Key is
  `SESSION_SECRET` (≥32 bytes recommended; the dev fallback is a
  documented public string and `config.py` raises in `production`
  mode if it's still in place).
- **Asymmetric crypto:** none used.
- **Key storage:** env vars in `.env` for local; for the EC2
  deployment, set via the systemd unit's `EnvironmentFile=` pointing at
  a 0600 file owned by the service user.
- **Key rotation:** `SESSION_SECRET` rotation invalidates all sessions
  (acceptable; users re-login). API keys are rotated in the provider
  console then updated in `.env`. No automated rotation.
- **CSPRNG:** the only random tokens generated are session-cookie
  signatures (handled by `itsdangerous`) and argon2 salts (handled by
  `argon2-cffi`). The application code itself does not generate
  security-bearing random values.

---

## 6. Data protection

- **At rest:** EBS volume-level encryption at AWS (operator's account
  policy). No application-level field encryption.
- **PII:** the only personal data stored is `users.nickname` and
  `users.pw_hash`. No email, no real name, no profile data.
- **Audit log:** **not implemented.** No record of logins, workspace
  edits, or admin actions. Tracked as `THREAT_MODEL.md#R-4`. Acceptable
  for a single-user instance; a real audit log is required before
  multi-user.
- **Backups:** manual `pg_dump` to S3 by the operator. Out of scope for
  this doc.

---

## 7. Input validation & output encoding

- **Schema validation:** FastAPI/Pydantic at the boundary. Path params
  are typed (`int` — invalid values get a 422 before the handler
  runs). Form bodies use `Form(...)` with type annotations.
- **SQL:** all queries use SQLAlchemy ORM or `text()` with bound
  parameters. Raw SQL is reviewed for injection on every change.
- **Vector literals:** `_vector_literal()` in `web/app.py` builds
  `[v1,v2,...]` strings from `list[float]`; floats are formatted via
  `f"{v:.6f}"`, which makes string-injection structurally impossible.
- **HTML output:** Jinja templates use auto-escaping (default for
  `.html` files). The two places that emit HTML via raw f-string —
  `add_workspace_entry`'s success blurb and `rename_workspace`'s reply
  — escape user-controlled fragments via `html.escape`.
- **CSP:** **not yet set.** Tracked as `THREAT_MODEL.md#R-5`.
- **CSRF:** the session cookie is `SameSite=Lax`, which blocks
  cross-site form POSTs and credentialed `fetch`. There is **no
  per-request CSRF token.** For a single-user, browser-only client this
  is acceptable; if multi-user with embedded content is added, revisit.
- **Body-size limits:** uvicorn defaults. The app has no large-body
  endpoints (no file upload) so this is not yet tightened.

---

## 8. Network security

- **Inbound:** the EC2 instance exposes 443 only (TLS terminator → app).
  Postgres is bound to the loopback interface inside the Docker bridge
  network and is not reachable externally.
- **Outbound:** unrestricted; the app calls `api.voyageai.com` and
  `api.anthropic.com` over HTTPS. No SSRF surface (the app does not
  fetch user-supplied URLs).
- **Internal:** single-host, single-process. No service mesh.

---

## 9. Logging, monitoring, alerting

- **Application logs:** uvicorn access log + Python `print()` from the
  ingest scripts. No structured logging today.
- **Security event alerting:** **not implemented.** No metrics on
  auth failures, rate-limit hits, or 5xx counts. Tracked as
  `THREAT_MODEL.md#R-6`.

---

## 10. Rate limiting & abuse prevention

Implemented by `src/debatabase/rate_limit.py` — in-memory sliding
windows, single-process, keyed per-user when authenticated and per-IP
otherwise:

| Endpoint                          | Burst (per-min) | Sustain (per-hour) | Reason                                                |
|-----------------------------------|-----------------|---------------------|-------------------------------------------------------|
| `POST /login`                     | 10 / IP         | 60 / IP             | Brute-force defence                                    |
| `POST /register`                  | 5 / IP          | 20 / IP             | Spam new-account creation                              |
| `GET /search?q=…`                 | 30              | 300                 | Each call may invoke Voyage `embed_query`             |
| `GET /cards/{id}/answers`         | 10              | 80                  | Each call invokes Anthropic Haiku **and** Voyage      |

- **Storage:** in-memory dict of deques per process. Single-process by
  design; if scaled to >1 worker, swap for Redis.
- **Trusted proxies:** `TRUSTED_PROXIES` (CSV of CIDRs) gates whether
  `X-Forwarded-For` is honoured. Default empty → header ignored, peer
  address used. See PATTERNS.md #7.

---

## 11. Supply chain

- **Dependency manifest:** `pyproject.toml` + `uv.lock` — pinned via
  `uv`'s lockfile.
- **Dependency scanning:** **not yet wired into CI.** Tracked as
  `THREAT_MODEL.md#R-7`.
- **SAST / secret scanning:** none in CI.
- **Container image:** the only container is `pgvector/pgvector:pg16`
  (the upstream pgvector image), pinned by tag.

---

## 12. Operational security

- **Secrets in deployment:** `.env` file owned by the service user,
  mode `0600`. Loaded via the systemd unit's `EnvironmentFile=`.
- **Production access:** SSH to the EC2 instance via a key file
  (`eevnio_debatabase_ssh_key.pem` lives on the operator's dev host).
- **DB user:** the application connects as the `debatabase` role, which
  has full DDL on its own database. This is over-privileged for the
  app's normal operation; tracked as `THREAT_MODEL.md#R-8`.

---

## 13. Known limitations

These are the items that map to residual risks in `THREAT_MODEL.md`:

- HSTS not enforced from the app — `R-1`
- No server-side session revocation — `R-2`
- No admin-role separation on `/admin/*` — `R-3`
- No audit log — `R-4`
- No CSP — `R-5`
- No security-event alerting — `R-6`
- No dependency / SAST scanning in CI — `R-7`
- DB role is over-privileged — `R-8`
- Session cookie signed but not encrypted (claims readable) — `R-9`

---

## 14. Change log

| Date       | Change                                                                  | Author |
|------------|-------------------------------------------------------------------------|--------|
| 2026-04-28 | Initial draft produced from the eevn_sec_test_templates skeleton.       | eevn   |
| 2026-04-28 | Rate-limit module landed; cookie hardening + open-redirect fix shipped. | eevn   |
