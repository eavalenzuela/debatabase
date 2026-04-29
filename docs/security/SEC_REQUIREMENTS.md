# Security Requirements — debatabase

**Status:** Living — v1
**Last reviewed:** 2026-04-28

Top-down checklist: *the app must do X*. Each requirement names the
invariants that enforce it and the threat-model rows it serves.

---

## REQ-AUTH — Authentication

| ID            | Requirement                                                                  | Enforced by    | Serves       |
|---------------|------------------------------------------------------------------------------|----------------|--------------|
| REQ-AUTH-01   | Passwords must be hashed with a memory-hard KDF (argon2 / bcrypt / scrypt).  | I-1            | API-S1       |
| REQ-AUTH-02   | Passwords must be ≥ 8 characters at registration.                            | I-2            | —            |
| REQ-AUTH-03   | The session cookie must be signed and tamper-rejected by the server.         | I-3            | API-S1       |
| REQ-AUTH-04   | The session cookie must be `HttpOnly`, `SameSite=Lax`, and `Secure` in prod. | I-4            | API-E2, UI-S1 |
| REQ-AUTH-05   | Login attempts must be rate-limited per IP.                                   | I-5            | API-D1       |
| REQ-AUTH-06   | New-account creation must be rate-limited per IP.                            | I-6            | API-D1       |
| REQ-AUTH-07   | The `next=` redirect target on login must be a same-origin path.             | I-7            | API-S2       |
| REQ-AUTH-08   | A NULL `pw_hash` must never accept any password.                              | I-8            | API-S1       |
| REQ-AUTH-09   | Logout must clear the session cookie client-side.                            | I-9            | —            |
| REQ-AUTH-10   | Production must refuse to start with the dev `SESSION_SECRET` fallback.      | I-10           | API-S1       |

## REQ-AUTHZ — Authorization

| ID            | Requirement                                                                                  | Enforced by | Serves |
|---------------|----------------------------------------------------------------------------------------------|-------------|--------|
| REQ-AUTHZ-01  | Every workspace endpoint must verify `ws.user_id == current_user_id`.                        | I-11        | API-I1 |
| REQ-AUTHZ-02  | Cross-user workspace access must return 404, not 403, to avoid ID-existence leaks.            | I-12        | API-I1 |
| REQ-AUTHZ-03  | Variant edits must only mutate variants whose workspace belongs to the current user.          | I-11, I-13  | API-I1 |
| REQ-AUTHZ-04  | The card corpus (`/`, `/search`, `/cards/*`, `/sources/*`, `/tags/*`, `/analyticals`) is intentionally public. | I-14        | —      |
| REQ-AUTHZ-05  | `/admin/*` endpoints must require an authenticated session.                                  | I-15        | API-E1 |

## REQ-COST — API-cost protection

| ID           | Requirement                                                                       | Enforced by | Serves |
|--------------|-----------------------------------------------------------------------------------|-------------|--------|
| REQ-COST-01  | `/search?q=…` calls must be rate-limited per user/IP.                             | I-16        | API-D2 |
| REQ-COST-02  | `/cards/{id}/answers` calls must be rate-limited per user/IP.                     | I-17        | API-D2 |
| REQ-COST-03  | `/search` without a query (browse mode) must not invoke the embedding API.        | I-18        | API-D2 |
| REQ-COST-04  | Voyage / Anthropic API keys must never be sent to the client.                     | I-19        | —      |

## REQ-INPUT — Input handling

| ID            | Requirement                                                                       | Enforced by | Serves      |
|---------------|-----------------------------------------------------------------------------------|-------------|-------------|
| REQ-INPUT-01  | All SQL must be parameterised. No string-built queries from request data.          | I-20        | API-T1      |
| REQ-INPUT-02  | All user-provided text rendered into HTML must be escaped (Jinja auto-escape or `html.escape`). | I-21 | API-T2, UI-T1, UI-T2 |
| REQ-INPUT-03  | Path params declared as `int` must reject non-integer input before the handler.   | I-22        | API-T1      |

## REQ-NET — Network surface

| ID         | Requirement                                                                       | Enforced by | Serves |
|------------|-----------------------------------------------------------------------------------|-------------|--------|
| REQ-NET-01 | Postgres must be reachable only from the app server (loopback / Docker bridge).   | I-23        | DB-S1  |
| REQ-NET-02 | `X-Forwarded-For` must be honoured only when the direct peer is in `TRUSTED_PROXIES`. | I-24    | TA-6   |

## REQ-SECRETS — Secrets handling

| ID             | Requirement                                                                       | Enforced by | Serves |
|----------------|-----------------------------------------------------------------------------------|-------------|--------|
| REQ-SECRETS-01 | API keys, `SESSION_SECRET`, DB password live in `.env` (local) / `EnvironmentFile=` (prod) — never in git. | I-25        | —      |
| REQ-SECRETS-02 | Secrets must not be echoed in error responses or HTML.                             | I-26        | —      |

## REQ-DEPLOY — Deployment posture

| ID            | Requirement                                                                       | Enforced by | Serves |
|---------------|-----------------------------------------------------------------------------------|-------------|--------|
| REQ-DEPLOY-01 | TLS terminates in front of uvicorn; the app is not directly internet-reachable on plain HTTP. | (operator) | TB-1 |
| REQ-DEPLOY-02 | The `.env` file (or `EnvironmentFile`) is mode `0600`, owned by the service user. | (operator)  | —      |
