# Security Validation Plan — debatabase

**Status:** Living — v1
**Last reviewed:** 2026-04-28

For every REQ-* and I-*, this defines *how it will be validated* and
*what "Verified" means*. Results live in `SEC_VALIDATION_FINDINGS.md`
and reference this plan by REQ/I id.

---

## How to read

Each row carries:
- **Method:** code review (`CR`), unit test (`UT`), structural test
  (`ST`), manual probe (`MP`), or observation (`OBS`).
- **Where:** file path, test name, or grep pattern.
- **Acceptance:** the binary check that flips a row to **Verified**.

---

## V-AUTH — Authentication

| ID            | Method | Where                                                     | Acceptance                                                             |
|---------------|--------|-----------------------------------------------------------|-------------------------------------------------------------------------|
| V-REQ-AUTH-01 | CR     | `src/debatabase/auth.py:hash_password`                    | Single hash entry-point; argon2 lib used; no crypto invented in-repo.  |
| V-REQ-AUTH-02 | UT     | `tests/test_security.py` (TODO: add password-policy test) | `validate_password("1234567")` returns an error; `len==8` accepted.    |
| V-REQ-AUTH-03 | CR     | `web/app.py:SessionMiddleware` config                      | `secret_key` is `settings.session_secret`; tampered cookie triggers no session. |
| V-REQ-AUTH-04 | CR     | `web/app.py:SessionMiddleware` config                      | `same_site="lax"`, `https_only=_PROD`, `max_age=30 days` set.           |
| V-REQ-AUTH-05 | CR + UT | `web/app.py:login_submit` decorator; `tests/test_rate_limit.py` | `Depends(login_rate_limit)` present; sliding-window test passes.        |
| V-REQ-AUTH-06 | CR + UT | `web/app.py:register_submit` decorator                    | `Depends(register_rate_limit)` present.                                  |
| V-REQ-AUTH-07 | UT     | `tests/test_security.py::test_safe_next_blocks_open_redirect` | All 11 parametrised cases pass.                                          |
| V-REQ-AUTH-08 | CR     | `src/debatabase/auth.py:verify_password`                   | First two lines reject `None`/empty hash unconditionally.               |
| V-REQ-AUTH-09 | CR     | `web/app.py:logout`                                        | Calls `request.session.clear()`.                                         |
| V-REQ-AUTH-10 | CR + MP | `src/debatabase/config.py`                                | `DEBATABASE_ENV=production python -c "import debatabase.config"` raises. |

## V-AUTHZ — Authorization

| ID             | Method | Where                                                            | Acceptance                                                                                  |
|----------------|--------|------------------------------------------------------------------|---------------------------------------------------------------------------------------------|
| V-REQ-AUTHZ-01 | CR     | `web/app.py:_get_workspace_or_404`; grep call sites              | All `/workspaces/{ws_id}*` handlers call it before any DB access.                            |
| V-REQ-AUTHZ-02 | CR     | `web/app.py:_get_workspace_or_404` body                          | `None` → `HTMLResponse(..., status_code=404)`. Never 403.                                    |
| V-REQ-AUTHZ-03 | CR     | `web/app.py:apply_variant_op`, `revert_variant`                  | Both look up the entry inside an already-scoped workspace.                                   |
| V-REQ-AUTHZ-04 | CR     | `web/app.py:_PUBLIC_PREFIXES`, `_require_login`                  | Listed paths bypass `_require_login`; nothing under `/workspaces` or `/admin` is in the list. |
| V-REQ-AUTHZ-05 | CR     | `/admin/*` handler decorators                                    | Each has `Depends(get_current_user_id)`.                                                     |

## V-COST — API-cost protection

| ID            | Method | Where                                                  | Acceptance                                                                                |
|---------------|--------|--------------------------------------------------------|-------------------------------------------------------------------------------------------|
| V-REQ-COST-01 | CR + UT | `web/app.py:search` body; `tests/test_rate_limit.py`  | `check_rate_limit(... SEARCH_BURST, SEARCH_HOURLY ...)` invoked when `q` is truthy.        |
| V-REQ-COST-02 | CR + UT | `web/app.py:card_answers` decorator                   | `Depends(answers_rate_limit)` present; sliding-window test passes.                          |
| V-REQ-COST-03 | CR     | `web/app.py:_search` and `_embed_query_safe`          | Voyage call is gated on `q` truthy AND `has_embedding_key()`.                              |
| V-REQ-COST-04 | CR + ST | grep `voyage_api_key|anthropic_api_key` in `web/`     | No template / response references the raw key.                                             |

## V-INPUT — Input handling

| ID             | Method | Where                                                         | Acceptance                                                                            |
|----------------|--------|---------------------------------------------------------------|---------------------------------------------------------------------------------------|
| V-REQ-INPUT-01 | ST     | grep `text\(f"\|text\(f'\|sqltext\(f` in `src/`              | Zero matches.                                                                          |
| V-REQ-INPUT-02 | CR + ST | `web/render.py`; `web/app.py` raw-`HTMLResponse(f...)` sites | All user-controlled fragments wrapped in `html.escape` / Jinja autoescape.            |
| V-REQ-INPUT-03 | OBS    | FastAPI typed-path-param behaviour                            | `GET /cards/abc` returns 422 (verified manually).                                      |

## V-NET — Network surface

| ID          | Method | Where                                          | Acceptance                                                                            |
|-------------|--------|------------------------------------------------|---------------------------------------------------------------------------------------|
| V-REQ-NET-01 | CR + MP | `docker-compose.yml`; `ss -tlnp` on the host  | Postgres listens on 127.0.0.1:5433 only; no 0.0.0.0 binding; security group blocks 5433. |
| V-REQ-NET-02 | CR + UT | `rate_limit.py:client_ip`; (TODO: add test)   | `X-Forwarded-For` only honoured when peer ∈ `TRUSTED_PROXIES`.                          |

## V-SECRETS — Secrets handling

| ID              | Method | Where                                       | Acceptance                                                                  |
|-----------------|--------|---------------------------------------------|-----------------------------------------------------------------------------|
| V-REQ-SECRETS-01 | ST     | `git ls-files | grep -E '\.env$|\.pem$'`   | Zero matches.                                                                |
| V-REQ-SECRETS-02 | CR     | `web/app.py:card_answers` exception paths   | Renders only `type(e).__name__`, never `str(e)` or the request body.        |

## V-DEPLOY — Deployment posture

| ID              | Method | Where                                        | Acceptance                                                                  |
|-----------------|--------|----------------------------------------------|-----------------------------------------------------------------------------|
| V-REQ-DEPLOY-01 | OBS    | TLS terminator config (nginx / ALB)         | `curl -I http://<host>` 301s to https; uvicorn not directly internet-reachable. |
| V-REQ-DEPLOY-02 | OBS    | `stat -c '%a %U %G' .env` on the EC2 host   | `600 debatabase debatabase`.                                                |
