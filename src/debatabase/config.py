import warnings

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


_DEV_SESSION_FALLBACK = "dev-only-not-secret-please-set-SESSION_SECRET-in-env"


class Settings(BaseSettings):
    database_url: str = "postgresql+psycopg://debatabase:debatabase@localhost:5433/debatabase"
    anthropic_api_key: str = ""
    # Voyage embeddings power semantic search. Optional — without a key,
    # /search falls back to tsvector-only ranking (the default for the
    # corpus until you run scripts/backfill_embeddings.py).
    voyage_api_key: str = ""
    # Signing key for the session cookie. In dev, falls back to a fixed
    # placeholder string so sessions persist across reloads without the
    # user having to set anything; production must set SESSION_SECRET to
    # a random value (e.g. `python -c "import secrets; print(secrets.token_hex(32))"`).
    session_secret: str = _DEV_SESSION_FALLBACK
    # When "production", refuse to start with the dev session-secret
    # fallback and require https-only cookies. Aliased to DEBATABASE_ENV
    # rather than bare ENV so the env var is namespaced and won't
    # collide with anything generic in the host environment.
    env: str = Field(default="dev", validation_alias="DEBATABASE_ENV")
    # Comma-separated CIDRs of reverse proxies whose X-Forwarded-For we
    # trust. Empty = ignore the header and use the direct peer.
    trusted_proxies: str = ""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()


def _is_prod() -> bool:
    return settings.env.lower() in ("prod", "production")


if settings.session_secret == _DEV_SESSION_FALLBACK:
    if _is_prod():
        raise RuntimeError(
            "SESSION_SECRET must be set in production. Generate one with "
            '`python -c "import secrets; print(secrets.token_hex(32))"`.'
        )
    warnings.warn(
        "SESSION_SECRET is unset; using a public dev fallback. "
        "Set it in .env before exposing the server beyond localhost.",
        stacklevel=2,
    )
