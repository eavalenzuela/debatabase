from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    SmallInteger,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Source(Base):
    __tablename__ = "sources"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    cite_short: Mapped[str] = mapped_column(Text, nullable=False)
    author_last: Mapped[str] = mapped_column(Text, nullable=False)
    author_full: Mapped[str | None] = mapped_column(Text)
    qualifications: Mapped[str | None] = mapped_column(Text)
    publication: Mapped[str | None] = mapped_column(Text)
    title: Mapped[str | None] = mapped_column(Text)
    published_date: Mapped[str | None] = mapped_column(Text)
    year: Mapped[int | None] = mapped_column(SmallInteger)
    url: Mapped[str | None] = mapped_column(Text)
    raw_cite: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    cards: Mapped[list["Card"]] = relationship(back_populates="source")


class Card(Base):
    __tablename__ = "cards"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    source_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("sources.id"), nullable=False)
    tag: Mapped[str] = mapped_column(Text, nullable=False)
    tag_markup: Mapped[list[dict]] = mapped_column(JSONB, default=list, nullable=False)
    card_text: Mapped[str] = mapped_column(Text, nullable=False)
    markup: Mapped[list[dict]] = mapped_column(JSONB, default=list, nullable=False)

    source_file: Mapped[str | None] = mapped_column(Text)
    block_path: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    source: Mapped[Source] = relationship(back_populates="cards")
    content_tag_links: Mapped[list["CardContentTag"]] = relationship(
        back_populates="card", cascade="all, delete-orphan"
    )


class ContentTag(Base):
    __tablename__ = "content_tags"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    slug: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    label: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    parent_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("content_tags.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class CardContentTag(Base):
    __tablename__ = "card_content_tags"

    card_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("cards.id"), primary_key=True)
    content_tag_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("content_tags.id"), primary_key=True
    )
    status: Mapped[str] = mapped_column(
        Enum("proposed", "approved", name="content_tag_status"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    card: Mapped[Card] = relationship(back_populates="content_tag_links")
    content_tag: Mapped[ContentTag] = relationship()


class Analytical(Base):
    __tablename__ = "analyticals"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    argument: Mapped[str] = mapped_column(Text, nullable=False)
    argument_markup: Mapped[list[dict]] = mapped_column(JSONB, default=list, nullable=False)
    answer_to: Mapped[str | None] = mapped_column(Text)
    source_file: Mapped[str | None] = mapped_column(Text)
    block_path: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    content_tag_links: Mapped[list["AnalyticalContentTag"]] = relationship(
        back_populates="analytical", cascade="all, delete-orphan"
    )


class AnalyticalContentTag(Base):
    __tablename__ = "analytical_content_tags"

    analytical_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("analyticals.id"), primary_key=True
    )
    content_tag_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("content_tags.id"), primary_key=True
    )
    status: Mapped[str] = mapped_column(
        Enum("proposed", "approved", name="content_tag_status"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    analytical: Mapped[Analytical] = relationship(back_populates="content_tag_links")
    content_tag: Mapped[ContentTag] = relationship()


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    nickname: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    pw_hash: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    workspace: Mapped["Workspace"] = relationship(
        back_populates="user", uselist=False, cascade="all, delete-orphan"
    )


class Workspace(Base):
    __tablename__ = "workspaces"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id"), nullable=False, unique=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    user: Mapped[User] = relationship(back_populates="workspace")
    entries: Mapped[list["WorkspaceEntry"]] = relationship(
        back_populates="workspace",
        order_by="WorkspaceEntry.position",
        cascade="all, delete-orphan",
    )


class WorkspaceEntry(Base):
    __tablename__ = "workspace_entries"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    workspace_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("workspaces.id"), nullable=False
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    header_path: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    card_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("cards.id"))
    analytical_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("analyticals.id")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    workspace: Mapped[Workspace] = relationship(back_populates="entries")
    card: Mapped[Card | None] = relationship()
    analytical: Mapped[Analytical | None] = relationship()
