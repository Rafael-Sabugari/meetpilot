"""Relational model. Every tenant-owned row carries `user_id` (docs/DATA_MODEL in ARCHITECTURE)."""

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, new_id, utcnow


class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    plan: Mapped[str] = mapped_column(String(32), default="trial")
    # Privacy default: transcripts are not persisted unless the user opts in.
    store_transcripts: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    meetings: Mapped[list["Meeting"]] = relationship(
        back_populates="user", cascade="all, delete-orphan", passive_deletes=True
    )


class Meeting(TimestampMixin, Base):
    __tablename__ = "meetings"
    __table_args__ = (Index("ix_meetings_user_created", "user_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    title: Mapped[str | None] = mapped_column(String(200), nullable=True)
    language: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), default="active")  # active | finished
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    audio_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    # Highest audio frame sequence already transcribed and billed, per source. Persisted with
    # usage so frames replayed after a reconnect (even to a restarted server) are not billed
    # or transcribed twice.
    last_seq_system: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_seq_microphone: Mapped[int | None] = mapped_column(Integer, nullable=True)

    user: Mapped[User] = relationship(back_populates="meetings")


class UsageRecord(TimestampMixin, Base):
    __tablename__ = "usage_records"
    __table_args__ = (Index("ix_usage_user_created", "user_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    meeting_id: Mapped[str | None] = mapped_column(
        ForeignKey("meetings.id", ondelete="SET NULL"), nullable=True
    )
    kind: Mapped[str] = mapped_column(String(32))  # audio_seconds
    source: Mapped[str | None] = mapped_column(String(16), nullable=True)
    provider: Mapped[str] = mapped_column(String(32))
    quantity: Mapped[float] = mapped_column(Float)


class RefreshToken(TimestampMixin, Base):
    """Opaque, rotating refresh tokens. Only a SHA-256 hash is stored."""

    __tablename__ = "refresh_tokens"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Memory(TimestampMixin, Base):
    """Second brain: what the User chose to remember. A meeting summary (opt-in per meeting,
    never audio or the raw transcript) or a note the User wrote or imported."""

    __tablename__ = "memories"
    __table_args__ = (Index("ix_memories_user_occurred", "user_id", "occurred_at"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    meeting_id: Mapped[str | None] = mapped_column(
        ForeignKey("meetings.id", ondelete="SET NULL"), nullable=True
    )
    kind: Mapped[str] = mapped_column(String(16))  # meeting | note
    status: Mapped[str] = mapped_column(String(16), default="ready")  # processing|ready|failed
    title: Mapped[str] = mapped_column(String(200))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    summary: Mapped[str] = mapped_column(Text, default="")
    # Markdown body: topics, decisions, action items... (meetings) or the note itself.
    details: Mapped[str] = mapped_column(Text, default="")
    # JSON list of facts about the User worth adding to "My context" (meetings only).
    suggestions: Mapped[str] = mapped_column(Text, default="[]")


class SuggestionFeedback(TimestampMixin, Base):
    """A suggestion the User rated (thumbs up/down). Approved ones become examples the
    copilot imitates; both feed the evaluation set. Saved only when the User rates."""

    __tablename__ = "suggestion_feedback"
    __table_args__ = (Index("ix_feedback_user_question", "user_id", "question_id", unique=True),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    question_id: Mapped[str] = mapped_column(String(64))
    scenario: Mapped[str] = mapped_column(String(16), default="interview")
    question: Mapped[str] = mapped_column(Text, default="")
    answer: Mapped[str] = mapped_column(Text)
    rating: Mapped[int] = mapped_column(Integer)  # +1 or -1
    note: Mapped[str] = mapped_column(Text, default="")


class MemoryVectors(TimestampMixin, Base):
    """Embeddings of a memory's passages for semantic search (a separate table, so existing
    SQLite databases only gain a table)."""

    __tablename__ = "memory_vectors"

    memory_id: Mapped[str] = mapped_column(
        ForeignKey("memories.id", ondelete="CASCADE"), primary_key=True
    )
    model: Mapped[str] = mapped_column(String(200))
    data: Mapped[bytes] = mapped_column(LargeBinary)
