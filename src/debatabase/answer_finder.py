"""Generate the inverse claim of a card and find evidence answering it.

The flow is small: ask Claude to rewrite a card's tag as the strongest
opposing argument (one declarative sentence), embed that, then vector-
search the corpus for the closest cards. Cards saying the inverse claim
will have embeddings close to it — that's what "answers this card" means.

Keeps the Claude call cheap by using Haiku and a tiny prompt.
"""

from __future__ import annotations

from anthropic import Anthropic

from debatabase.config import settings

_HAIKU_MODEL = "claude-haiku-4-5-20251001"
_INVERSE_PROMPT = (
    "You are helping a policy debater find counter-arguments. Given an "
    "argument tag from a debate evidence card, write the strongest opposing "
    "claim — the one a debater would search their evidence base for to "
    "answer this card.\n\n"
    "Output ONLY the opposing claim as a single declarative sentence. "
    "No preamble. No quotes. No phrases like 'the opposing argument is'."
)


def has_inverse_capability() -> bool:
    return bool(settings.anthropic_api_key)


def generate_inverse_claim(tag: str) -> str:
    """Return the opposing-claim sentence Claude generates for `tag`."""
    if not has_inverse_capability():
        raise RuntimeError("ANTHROPIC_API_KEY is not set")
    client = Anthropic(api_key=settings.anthropic_api_key)
    msg = client.messages.create(
        model=_HAIKU_MODEL,
        max_tokens=200,
        system=_INVERSE_PROMPT,
        messages=[{"role": "user", "content": f"Tag: {tag}"}],
    )
    parts = [b.text for b in msg.content if getattr(b, "type", None) == "text"]
    return "".join(parts).strip()
