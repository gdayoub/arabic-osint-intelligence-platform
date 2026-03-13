"""SQLAlchemy ORM models for raw and processed article layers."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    JSON,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class RawArticle(Base):
    __tablename__ = "raw_articles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(100), index=True)
    title: Mapped[str] = mapped_column(Text)
    subtitle: Mapped[str | None] = mapped_column(Text, nullable=True)
    body: Mapped[str] = mapped_column(Text)
    author: Mapped[str | None] = mapped_column(String(255), nullable=True)
    published_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    url: Mapped[str] = mapped_column(String(1024), unique=True, index=True)
    tags: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    source_section: Mapped[str | None] = mapped_column(String(255), nullable=True)
    collected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )
    content_hash: Mapped[str] = mapped_column(String(128), unique=True, index=True)

    processed_article: Mapped["ProcessedArticle | None"] = relationship(
        back_populates="raw_article",
        cascade="all, delete-orphan",
    )


class ProcessedArticle(Base):
    __tablename__ = "processed_articles"
    __table_args__ = (UniqueConstraint("raw_article_id", name="uq_processed_raw_article"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    raw_article_id: Mapped[int] = mapped_column(
        ForeignKey("raw_articles.id", ondelete="CASCADE"),
        index=True,
    )
    cleaned_text: Mapped[str] = mapped_column(Text)
    topic: Mapped[str] = mapped_column(String(100), index=True)
    sentiment_or_escalation: Mapped[str] = mapped_column(String(64), index=True)
    country_guess: Mapped[str | None] = mapped_column(String(100), nullable=True)
    keyword_matches: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    ml_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    processed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )

    raw_article: Mapped[RawArticle] = relationship(back_populates="processed_article")
