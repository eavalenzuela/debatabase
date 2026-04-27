"""Insert helpers used during interactive card review.

These are deliberately small and explicit — during the training phase Claude
calls them per-card after the user approves a proposed parse. No batch
auto-ingest. No silent inserts.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from debatabase.models import (
    Analytical,
    AnalyticalContentTag,
    Card,
    CardContentTag,
    ContentTag,
    Source,
)


@dataclass
class SourceData:
    cite_short: str
    author_last: str
    raw_cite: str
    author_full: str | None = None
    qualifications: str | None = None
    publication: str | None = None
    title: str | None = None
    published_date: str | None = None
    year: int | None = None
    url: str | None = None


@dataclass
class CardData:
    tag: str
    card_text: str
    markup: list[dict]
    tag_markup: list[dict] | None = None
    source_file: str | None = None
    block_path: list[str] | None = None


@dataclass
class AnalyticalData:
    argument: str
    answer_to: str | None = None
    argument_markup: list[dict] | None = None
    source_file: str | None = None
    block_path: list[str] | None = None


def get_or_create_source(session: Session, data: SourceData) -> Source:
    """Look up by (cite_short, raw_cite) so identical cites collapse to one row."""
    existing = (
        session.query(Source)
        .filter(Source.cite_short == data.cite_short, Source.raw_cite == data.raw_cite)
        .one_or_none()
    )
    if existing:
        return existing
    src = Source(
        cite_short=data.cite_short,
        author_last=data.author_last,
        author_full=data.author_full,
        qualifications=data.qualifications,
        publication=data.publication,
        title=data.title,
        published_date=data.published_date,
        year=data.year,
        url=data.url,
        raw_cite=data.raw_cite,
        created_at=datetime.now(UTC),
    )
    session.add(src)
    session.flush()
    return src


def get_or_create_content_tag(session: Session, slug: str, label: str) -> ContentTag:
    existing = session.query(ContentTag).filter(ContentTag.slug == slug).one_or_none()
    if existing:
        return existing
    tag = ContentTag(slug=slug, label=label, created_at=datetime.now(UTC))
    session.add(tag)
    session.flush()
    return tag


def insert_card(
    session: Session,
    source: Source,
    card: CardData,
    approved_tag_slugs: list[tuple[str, str]],  # [(slug, label), ...]
    *,
    status: str = "approved",
    wiki_upload_id: int | None = None,
) -> Card:
    """Insert a card and its tag links.

    ``status`` controls ``card_content_tags.status`` for the inserted
    rows. Pre-#3 (KEYWORD_RULES) tags came in "approved" because the
    rules were hand-vetted; Claude-proposed tags from ``tagger.py``
    use "proposed" so an admin can promote them. Enforced by the
    ``content_tag_status`` enum.
    """
    c = Card(
        source_id=source.id,
        tag=card.tag,
        tag_markup=card.tag_markup or [],
        card_text=card.card_text,
        markup=card.markup,
        source_file=card.source_file,
        block_path=card.block_path,
        ingested_at=datetime.now(UTC),
        wiki_upload_id=wiki_upload_id,
    )
    session.add(c)
    session.flush()
    for slug, label in approved_tag_slugs:
        ct = get_or_create_content_tag(session, slug, label)
        session.add(
            CardContentTag(
                card_id=c.id,
                content_tag_id=ct.id,
                status=status,
                created_at=datetime.now(UTC),
            )
        )
    return c


def insert_analytical(
    session: Session,
    data: AnalyticalData,
    approved_tag_slugs: list[tuple[str, str]],
    *,
    status: str = "approved",
    wiki_upload_id: int | None = None,
) -> Analytical:
    a = Analytical(
        argument=data.argument,
        argument_markup=data.argument_markup or [],
        answer_to=data.answer_to,
        source_file=data.source_file,
        block_path=data.block_path,
        ingested_at=datetime.now(UTC),
        wiki_upload_id=wiki_upload_id,
    )
    session.add(a)
    session.flush()
    for slug, label in approved_tag_slugs:
        ct = get_or_create_content_tag(session, slug, label)
        session.add(
            AnalyticalContentTag(
                analytical_id=a.id,
                content_tag_id=ct.id,
                status=status,
                created_at=datetime.now(UTC),
            )
        )
    return a
