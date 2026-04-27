import secrets
import warnings

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

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()

if settings.session_secret == _DEV_SESSION_FALLBACK:
    warnings.warn(
        "SESSION_SECRET is unset; using a public dev fallback. "
        "Set it in .env before exposing the server beyond localhost.",
        stacklevel=2,
    )
