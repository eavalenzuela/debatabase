"""Card-text embedding provider for semantic search.

Wraps the Voyage AI client. ``voyage-3-lite`` was chosen for the corpus:
512 dimensions, $0.02 per million tokens, generous rate limits. Cheap
enough that re-embedding the whole 3k-card corpus from scratch is well
under a cent.

The provider is intentionally a small surface (one function +
``EMBEDDING_DIM``) so swapping models or providers later is one file.

If ``VOYAGE_API_KEY`` is unset, ``get_embeddings`` raises ``RuntimeError``
— callers (web search, the backfill script) check ``has_embedding_key``
first and gracefully degrade to tsvector-only behavior.
"""

from __future__ import annotations

from debatabase.config import settings

EMBEDDING_DIM = 512
_MODEL = "voyage-3-lite"
_QUERY_INPUT_TYPE = "query"
_DOCUMENT_INPUT_TYPE = "document"


def has_embedding_key() -> bool:
    return bool(settings.voyage_api_key)


def _client():
    import voyageai

    return voyageai.Client(api_key=settings.voyage_api_key)


def embed_documents(texts: list[str]) -> list[list[float]]:
    """Embed card-body texts for storage.

    Voyage's ``input_type="document"`` is asymmetric with ``"query"``:
    documents are tuned for storage, queries for the search side. Using
    the right type on each side measurably improves retrieval.
    """
    if not has_embedding_key():
        raise RuntimeError(
            "VOYAGE_API_KEY is not set; cannot generate embeddings"
        )
    if not texts:
        return []
    result = _client().embed(
        texts, model=_MODEL, input_type=_DOCUMENT_INPUT_TYPE
    )
    return [list(v) for v in result.embeddings]


def embed_query(text: str) -> list[float]:
    """Embed a single user search string."""
    if not has_embedding_key():
        raise RuntimeError(
            "VOYAGE_API_KEY is not set; cannot generate embeddings"
        )
    result = _client().embed(
        [text], model=_MODEL, input_type=_QUERY_INPUT_TYPE
    )
    return list(result.embeddings[0])
