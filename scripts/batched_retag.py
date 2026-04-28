"""Batched Claude-driven retag for the wiki corpus.

Same job as ``retag_cards.py`` but sends ~15 cards per Haiku call and
caches the [system + vocabulary] prefix on the API side. That brings
the per-card cost from ~$0.0036 down to ~$0.0004 (≈10x), turning a
$300 retag of the 88k wiki corpus into ~$30.

Idempotency mirrors retag_cards.py: prior 'proposed' rows for each
processed card are deleted before re-insert; 'approved' rows are
preserved.

Usage:
    uv run python scripts/batched_retag.py --limit 100
    uv run python scripts/batched_retag.py --where "wiki_upload_id IS NOT NULL"
    uv run python scripts/batched_retag.py --batch 15
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from dataclasses import dataclass

from anthropic import Anthropic
from sqlalchemy import select, text as sqltext

from debatabase.config import settings
from debatabase.db import session_scope
from debatabase.models import Card, CardContentTag, ContentTag

_HAIKU_MODEL = "claude-haiku-4-5-20251001"
_BODY_CHARS = 1000
_MAX_TAGS_PER_CARD = 4

_SYSTEM_PROMPT = (
    "You are a debate coach helping tag policy debate evidence cards for "
    "a searchable evidence database used by competitive policy debaters "
    "(college and high school). The database has a controlled vocabulary "
    "of content tags that label what *kind of argument* a card makes — "
    "not what topic it's about, but what argumentative move it executes. "
    "Tags are reused across hundreds of cards, so they need to capture "
    "recurring argument shapes: critiques, links, impacts, alternatives, "
    "DAs, theory shells, framework moves.\n\n"
    "Your job: for EACH card I send you in a batch, choose the tags from "
    "the provided vocabulary that best capture what argument this card "
    "makes. Quality over quantity — don't stretch to fill slots. A card "
    "with no clean match should get an empty array; that's correct, not "
    "a failure.\n\n"
    "How to read a card:\n"
    "- The 'tag' field is the cutter's own one-line summary of the "
    "argument. It's the most informative signal.\n"
    "- The 'body' field is a snippet of the quoted source text. Use it "
    "to disambiguate when the tag is terse or coded.\n"
    "- Card style varies by school and topic. The 2025-26 hspolicy season "
    "is about Arctic exploration / development, but cards include K "
    "literatures (Mbembe, Wilderson, Tuck & Yang, Mignolo), policy "
    "literatures (Brookings, Foreign Affairs), and debate theory "
    "(framework, topicality, perm).\n\n"
    "Selecting tags — heuristics that work:\n"
    "1. Most cards are 'link' or 'impact' or 'solvency' shaped. The tag "
    "vocabulary mostly lines up with these — pick what kind of move the "
    "card makes.\n"
    "2. For K cards, prefer the slug that names the specific theorist "
    "or school (afropessimism, biopolitics, settler-colonialism) over a "
    "generic critical-theory slug, when the body cites that theorist.\n"
    "3. For Arctic-topic cards, the recently added arctic-* slugs are "
    "usually the best fit. Don't stretch a generic slug onto a card that "
    "has a more specific Arctic equivalent.\n"
    "4. A card can have 0, 1, 2, 3, or 4 tags. Two tags is typical for "
    "a well-cut card. Four is rare and reserved for cards that genuinely "
    "execute multiple distinct moves.\n"
    "5. If the tag and body don't clearly map to ANY of the provided "
    "slugs, output [] for that card. The vocabulary is intentionally "
    "incomplete; missing tags is fine and signals 'human review needed' "
    "rather than 'force a guess'.\n\n"
    "Worked examples:\n\n"
    "Card #1:\n"
    "  tag: 'Police abolition is the only solution — reform reproduces the "
    "carceral state'\n"
    "  body: 'The contemporary policing apparatus, as Roberts argues, is "
    "not a deviation from but a continuation of the slave patrol...'\n"
    "  → ['police-abolition', 'abolition', 'slavery-origins']\n\n"
    "Card #2:\n"
    "  tag: 'Russian aggression in the Arctic causes nuclear war'\n"
    "  body: 'Russia's modernization of its Northern Fleet, including "
    "hypersonic missile platforms positioned along the Kola Peninsula...'\n"
    "  → ['arctic-militarization', 'nuclear-escalation', 'military-escalation']\n\n"
    "Card #3:\n"
    "  tag: 'Their conception of sovereignty upholds a reactionary attachment "
    "to necropolitical slaughter'\n"
    "  body: 'For Mbembe, sovereignty resides in the power and capacity to "
    "dictate who may live and who must die...'\n"
    "  → ['necropolitics', 'mbembe', 'biopolitics']\n\n"
    "Card #4:\n"
    "  tag: 'Permitting reform is bipartisan — Manchin-Barrasso vehicle moves'\n"
    "  body: 'Senators on both sides of the aisle have signaled support for "
    "an updated NEPA framework that streamlines federal review timelines...'\n"
    "  → ['permitting-reform-bipartisan', 'politics-da']\n\n"
    "Card #5:\n"
    "  tag: 'Reformism is a prerequisite to radical change — incremental "
    "shifts build coalition'\n"
    "  body: 'Hailwood argues that anarchist critiques of liberal reform "
    "miss how marginal gains shift the Overton window...'\n"
    "  → ['reformism-fails', 'cap-k']  # note: card *defends* reformism, "
    "but reformism-fails is the slug for the *debate* about reformism\n\n"
    "Card #6 (no clean match — output empty):\n"
    "  tag: 'Greenhill RR Round 5 prep block — generic case neg'\n"
    "  body: 'This file responds to the Greenhill RR Round 5 1AC delivered "
    "by [team] and includes...'\n"
    "  → []  # this is meta / file metadata, not a substantive argument\n\n"
    "Card #7 (Arctic — multiple slugs apply):\n"
    "  tag: 'Russia is rapidly remilitarizing the Northern Sea Route — "
    "great-power conflict goes nuclear'\n"
    "  body: 'The Northern Fleet has been reconstituted as a separate "
    "military district. Putin has authorized the deployment of Bulava-class "
    "SLBM platforms to the Kola Peninsula...'\n"
    "  → ['arctic-militarization', 'nuclear-escalation', "
    "'russia-regime-fragility']  # specific Arctic slug + generic escalation "
    "+ Russia-specific\n\n"
    "Card #8 (Arctic indigenous):\n"
    "  tag: 'Arctic resource extraction without indigenous consent reproduces "
    "settler-colonial dispossession'\n"
    "  body: 'The trust doctrine, as articulated by Williams, requires "
    "free prior and informed consent before extractive activity on tribal "
    "lands. The Northwest Arctic Borough...'\n"
    "  → ['indigenous-arctic-sovereignty', 'settler-colonialism-arctic', "
    "'colonial-land-dispossession', 'bioprospecting-consent']\n\n"
    "Card #9 (impact card — pick the impact slug not the topic slug):\n"
    "  tag: 'Permafrost thaw triggers methane cascade — extinction-level "
    "warming feedback'\n"
    "  body: 'Recent IPCC modeling suggests that permafrost methane release, "
    "once initiated, becomes self-sustaining as ice-albedo feedback...'\n"
    "  → ['permafrost-ecosystem-collapse', 'extinction-as-violence']\n\n"
    "Card #10 (procedural / theory):\n"
    "  tag: 'Topicality — \"its\" requires US-only action; their plan "
    "with allied cooperation is extra-T'\n"
    "  body: 'Webster defines \"its\" as a possessive pronoun limiting "
    "the antecedent. Allowing extra-T affs explodes the topic and crowds "
    "out core-controversy literature...'\n"
    "  → ['topicality', 'framework']\n\n"
    "Card #11 (multilateralism — pick narrow over broad when both fit):\n"
    "  tag: 'NATO consensus on Arctic posture is the only check on great-power "
    "miscalc — institutions solve war'\n"
    "  body: 'The Arctic Council and NATO Atlantic command together provide "
    "the institutional bandwidth for crisis management...'\n"
    "  → ['multilateralism-prevents-war', 'arctic-council-collapse']  # "
    "prefer the narrow slug when applicable; the bare 'multilateralism' slug "
    "is for cards where the prevents-war framing is absent\n\n"
    "Card #12 (broad slug applies, narrow doesn't):\n"
    "  tag: 'The patriarchal logic of nuclear deterrence reproduces "
    "gendered violence'\n"
    "  body: 'Cohn argues that the language of nuclear strategy — "
    "throw-weight, penetration, hardness — encodes a masculinist epistemology...'\n"
    "  → ['feminism', 'patriarchy', 'fem-ir']  # 'feminism' broadly, "
    "'patriarchy' as the K name, 'fem-ir' as the existing-vocab specific\n\n"
    "Common mistakes to avoid:\n"
    "1. Don't tag a card with the topic name (e.g. 'arctic') when the "
    "vocabulary has the *argument-shaped* version (e.g. 'arctic-militarization', "
    "'arctic-resource-competition'). Argument tags are reusable across "
    "cards; topic tags aren't.\n"
    "2. Don't combine redundant slugs. If a card is about Arctic "
    "militarization, don't also tag it 'militarization' generically — the "
    "specific slug already implies the broader frame.\n"
    "3. For DA/impact cards, the impact slug (e.g. 'extinction-as-violence', "
    "'nuclear-winter-agriculture') is the primary tag. The link/internal "
    "is secondary.\n"
    "4. K cards: prefer the theorist or school slug ('mbembe', "
    "'afropessimism', 'settler-colonialism') over a generic 'critical-theory' "
    "slug. The specific name carries more search signal.\n"
    "5. If you find yourself writing the same response 4+ times in a batch, "
    "you might be over-stretching. Cards are diverse — the hit rate of any "
    "single slug across a random 15-card batch is usually < 30%.\n"
    "6. The empty array [] is correct, expected, and useful output. Probably "
    "10-25% of cards should get []. Resist the urge to fill.\n"
    "7. Card analyticals (debater-authored, no quoted source) and short "
    "procedural cards (e.g. 'AT: condo bad — 2NR collapses' with a brief "
    "body) often get 0 or 1 tag — that's correct. Don't reach for theory "
    "or framework slugs unless the body actually engages them.\n"
    "8. The 'permitting-reform-bipartisan' and 'politics-da' slugs are "
    "different: the former is a substantive policy claim about the Manchin/"
    "Barrasso vehicle and bipartisan momentum; the latter is a generic "
    "political-capital DA. Use the specific slug when the card is making "
    "the substantive claim, the generic one when it's a vanilla agenda DA.\n\n"
    "Output rules:\n"
    f"- Output ONLY a JSON object mapping card_id (integer) to an array "
    f"of slug strings (max {_MAX_TAGS_PER_CARD} per card).\n"
    "- Use only slugs from the provided vocabulary. If you find yourself "
    "wanting a slug that doesn't exist, just output [] and we'll grow "
    "the vocabulary later.\n"
    "- If no tag clearly applies for a card, output [] for it. Don't "
    "stretch — empty is fine and expected for ~10-30% of cards.\n"
    "- No preamble, no explanation, no code fences. Just the JSON object.\n"
    "- Process every card_id you receive — don't skip any."
)


@dataclass
class VocabEntry:
    slug: str
    label: str
    description: str | None


def _format_vocab(vocab: list[VocabEntry]) -> str:
    lines = []
    for v in vocab:
        if v.description:
            lines.append(f"- {v.slug}: {v.label} — {v.description}")
        else:
            lines.append(f"- {v.slug}: {v.label}")
    return "\n".join(lines)


def _format_cards(cards: list[dict]) -> str:
    blocks = []
    for c in cards:
        blocks.append(
            f"--- card_id={c['id']} ---\n"
            f"tag: {c['tag']}\n"
            f"body: {c['body']}"
        )
    return "\n\n".join(blocks)


def _parse_response(text: str, valid_slugs: set[str]) -> dict[int, list[str]]:
    match = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if match is None:
        return {}
    try:
        parsed = json.loads(match.group(0))
    except json.JSONDecodeError:
        return {}
    out: dict[int, list[str]] = {}
    for k, v in parsed.items():
        try:
            cid = int(k)
        except (TypeError, ValueError):
            continue
        if not isinstance(v, list):
            continue
        slugs: list[str] = []
        for item in v:
            if not isinstance(item, str):
                continue
            slug = item.strip().lower()
            if slug in valid_slugs and slug not in slugs:
                slugs.append(slug)
            if len(slugs) >= _MAX_TAGS_PER_CARD:
                break
        out[cid] = slugs
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--batch", type=int, default=15)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument(
        "--where",
        default="wiki_upload_id IS NOT NULL",
        help="SQL WHERE clause filtering which cards to process "
        "(default: only wiki cards)",
    )
    args = ap.parse_args()

    if not settings.anthropic_api_key:
        print("ANTHROPIC_API_KEY required.", file=sys.stderr)
        return 2

    # Load full vocabulary once.
    with session_scope() as s:
        vocab_rows = s.execute(select(ContentTag).order_by(ContentTag.slug)).scalars().all()
        vocab = [
            VocabEntry(slug=t.slug, label=t.label, description=t.description)
            for t in vocab_rows
        ]
        slug_to_id = {t.slug: t.id for t in vocab_rows}
    valid_slugs = set(slug_to_id)
    vocab_block = _format_vocab(vocab)
    print(f"vocabulary: {len(vocab)} tags")

    # Load card ids to process.
    with session_scope() as s:
        stmt = (
            select(Card.id)
            .where(sqltext(args.where))
            .order_by(Card.id)
        )
        if args.limit is not None:
            stmt = stmt.limit(args.limit)
        all_ids = list(s.execute(stmt).scalars().all())
    print(f"processing {len(all_ids)} card(s) in batches of {args.batch}")

    client = Anthropic(api_key=settings.anthropic_api_key)
    t_start = time.monotonic()
    proposed_links = 0
    cards_with_any_tag = 0
    cards_processed = 0
    api_input_tokens = 0
    api_input_cached_read = 0
    api_input_cached_create = 0
    api_output_tokens = 0

    for i in range(0, len(all_ids), args.batch):
        batch_ids = all_ids[i : i + args.batch]

        # Fetch card text for the batch.
        with session_scope() as s:
            rows = s.execute(
                select(Card.id, Card.tag, Card.card_text).where(
                    Card.id.in_(batch_ids)
                )
            ).all()
        cards = [
            {
                "id": r.id,
                "tag": r.tag,
                "body": (r.card_text or "")[:_BODY_CHARS],
            }
            for r in rows
        ]
        if not cards:
            continue

        # Single Haiku call. The system prompt + vocab is cacheable
        # (Anthropic prompt cache, ephemeral). Cards go in user msg.
        try:
            msg = client.messages.create(
                model=_HAIKU_MODEL,
                max_tokens=600,
                system=[
                    {
                        "type": "text",
                        "text": (
                            f"{_SYSTEM_PROMPT}\n\nVocabulary:\n{vocab_block}"
                        ),
                        "cache_control": {"type": "ephemeral"},
                    },
                ],
                messages=[
                    {"role": "user", "content": _format_cards(cards)},
                ],
            )
        except Exception as e:
            # Hard bail on credit exhaustion — looping for hours hitting 400s
            # is worse than failing cleanly. Other transient errors retry.
            err_repr = repr(e)
            if "credit balance is too low" in err_repr:
                print(
                    f"  batch starting at {batch_ids[0]}: credit exhausted, "
                    f"halting cleanly. {cards_processed} cards processed so far."
                )
                sys.exit(3)
            print(f"  batch starting at {batch_ids[0]} errored: {err_repr}")
            continue

        usage = msg.usage
        api_input_tokens += usage.input_tokens
        api_input_cached_read += getattr(usage, "cache_read_input_tokens", 0) or 0
        api_input_cached_create += getattr(usage, "cache_creation_input_tokens", 0) or 0
        api_output_tokens += usage.output_tokens

        text = "".join(
            b.text for b in msg.content if getattr(b, "type", None) == "text"
        )
        proposals = _parse_response(text, valid_slugs)

        # Apply: drop prior 'proposed' for each card, then insert new.
        with session_scope() as s:
            for cid in batch_ids:
                slugs = proposals.get(cid, [])
                s.execute(
                    sqltext(
                        "DELETE FROM card_content_tags "
                        "WHERE card_id = :cid AND status = 'proposed'"
                    ),
                    {"cid": cid},
                )
                if not slugs:
                    continue
                approved_ids = set(
                    s.execute(
                        sqltext(
                            "SELECT content_tag_id FROM card_content_tags "
                            "WHERE card_id = :cid AND status = 'approved'"
                        ),
                        {"cid": cid},
                    ).scalars().all()
                )
                inserted_for_card = 0
                for slug in slugs:
                    tid = slug_to_id.get(slug)
                    if tid is None or tid in approved_ids:
                        continue
                    s.add(
                        CardContentTag(
                            card_id=cid, content_tag_id=tid, status="proposed"
                        )
                    )
                    inserted_for_card += 1
                if inserted_for_card:
                    proposed_links += inserted_for_card
                    cards_with_any_tag += 1
        cards_processed += len(batch_ids)

        if (i // args.batch + 1) % 10 == 0 or (i + args.batch) >= len(all_ids):
            elapsed = time.monotonic() - t_start
            rate_cards = cards_processed / elapsed if elapsed > 0 else 0
            print(
                f"  [{cards_processed:6d}/{len(all_ids)}] "
                f"{cards_with_any_tag} tagged · {proposed_links} links · "
                f"{rate_cards:.1f} cards/s · "
                f"in/out tokens: {api_input_tokens}/{api_output_tokens} "
                f"(cache read={api_input_cached_read})"
            )

    elapsed = time.monotonic() - t_start
    print(
        f"\ndone in {elapsed:.0f}s. {cards_processed} cards processed; "
        f"{cards_with_any_tag} got at least one tag; "
        f"{proposed_links} total proposed links."
    )
    print(
        f"Tokens — input: {api_input_tokens} (cache read: "
        f"{api_input_cached_read}, cache create: {api_input_cached_create}), "
        f"output: {api_output_tokens}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
