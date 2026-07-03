"""Claude-assisted content tagging for cards and analyticals.

Replaces the old keyword/substring rules in ``bulk.py`` with a Haiku
call that reads the card's tag + body and chooses tags from the
existing controlled vocabulary in ``content_tags``. Output is
constrained to that vocabulary — Claude cannot invent new tags.

Tags emitted by this module land in ``card_content_tags`` /
``analytical_content_tags`` with ``status='proposed'``, signaling that
they're LLM-generated and awaiting admin promotion. Cards still surface
in search and the tag chips render with a distinct style so the
proposed-vs-approved split is visible without an admin queue UI.

Failures (network, JSON parse, missing key) silently degrade to ``[]``
so ingest never blocks on the LLM.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

from anthropic import Anthropic

from debatabase.config import settings

logger = logging.getLogger("debatabase.tagger")

_HAIKU_MODEL = "claude-haiku-4-5-20251001"
_MAX_BODY_CHARS = 1500
_MAX_TAGS = 4


@dataclass(frozen=True)
class VocabEntry:
    slug: str
    label: str
    description: str | None = None


_SYSTEM_PROMPT = (
    "You are a debate coach helping tag policy debate evidence cards.\n"
    "Given a card's tag (its argumentative summary) and a snippet of its "
    "body, choose the most applicable content tags from the provided "
    "vocabulary.\n\n"
    "Output rules:\n"
    f"- Output ONLY a JSON array of slug strings. Maximum {_MAX_TAGS} tags.\n"
    "- Use only slugs from the provided vocabulary.\n"
    "- If no tag clearly applies, output []. Don't stretch — empty is fine.\n"
    "- No preamble, no explanation, no code fences."
)


def has_tagging_capability() -> bool:
    return bool(settings.anthropic_api_key)


def _format_vocab(vocab: list[VocabEntry]) -> str:
    lines = []
    for v in vocab:
        if v.description:
            lines.append(f"- {v.slug}: {v.label} — {v.description}")
        else:
            lines.append(f"- {v.slug}: {v.label}")
    return "\n".join(lines)


def propose_tags(
    tag: str, body: str, vocab: list[VocabEntry]
) -> list[str]:
    """Return up to ``_MAX_TAGS`` slugs from ``vocab`` that apply to the card.

    Returns ``[]`` on any failure (no key, network error, non-JSON
    response, slugs outside vocab) — callers should treat ``[]`` as
    "no tags proposed" and continue ingest.
    """
    if not has_tagging_capability() or not vocab:
        return []

    body_excerpt = body[:_MAX_BODY_CHARS]
    user_msg = (
        f"Vocabulary:\n{_format_vocab(vocab)}\n\n"
        f"Card tag:\n{tag}\n\n"
        f"Card body (truncated):\n{body_excerpt}"
    )

    try:
        client = Anthropic(api_key=settings.anthropic_api_key)
        msg = client.messages.create(
            model=_HAIKU_MODEL,
            max_tokens=200,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_msg}],
        )
        text = "".join(
            b.text for b in msg.content if getattr(b, "type", None) == "text"
        ).strip()
    except Exception:
        # Ingest must never block on the LLM — degrade to "no tags
        # proposed", but log so a dead key / quota problem is visible
        # instead of manifesting as a mysteriously untagged corpus.
        logger.warning("tag proposal call failed; returning []", exc_info=True)
        return []

    return _parse_slugs(text, {v.slug for v in vocab})


def _parse_slugs(text: str, valid_slugs: set[str]) -> list[str]:
    """Extract a JSON array of slugs from ``text``, filter to valid ones."""
    # Be lenient with stray code fences / extra text around the array.
    match = re.search(r"\[[^\[\]]*\]", text, flags=re.DOTALL)
    if match is None:
        return []
    try:
        parsed = json.loads(match.group(0))
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []

    out: list[str] = []
    seen: set[str] = set()
    for item in parsed:
        if not isinstance(item, str):
            continue
        slug = item.strip().lower()
        if slug in valid_slugs and slug not in seen:
            out.append(slug)
            seen.add(slug)
        if len(out) >= _MAX_TAGS:
            break
    return out
