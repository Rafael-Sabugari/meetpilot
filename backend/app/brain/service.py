"""Second brain storage helpers and the end-of-meeting memory job."""

import logging
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.ai.context import ContextSegment
from app.brain.ai import suggestions_json, summarize_meeting
from app.brain.search import Hit, MemoryDoc, recent, search
from app.db.models import Memory, UsageRecord
from app.providers.llm.base import LLMError, LLMProvider

log = logging.getLogger("meetpilot.brain")

# Memories loaded for search (newest first). Enough for years of meetings.
MAX_DOCS = 2_000


def to_doc(m: Memory) -> MemoryDoc:
    text = "\n\n".join(p for p in (m.summary, m.details) if p.strip())
    return MemoryDoc(m.id, m.kind, m.title, m.occurred_at, text)


async def load_docs(db: AsyncSession, user_id: str) -> list[MemoryDoc]:
    rows = await db.execute(
        select(Memory)
        .where(Memory.user_id == user_id, Memory.status == "ready")
        .order_by(Memory.occurred_at.desc())
        .limit(MAX_DOCS)
    )
    return [to_doc(m) for m in rows.scalars()]


def find(docs: Sequence[MemoryDoc], query: str, limit: int = 8) -> list[Hit]:
    """Best passages, or the newest memories when the question has nothing to match
    ("o que aconteceu ontem?")."""
    hits = search(docs, query, limit=limit)
    return hits or recent(docs, min(limit, 5))


def relevant_for_meeting(docs: Sequence[MemoryDoc], text: str, limit: int = 4) -> list[Hit]:
    """Memories for a live answer: only clear matches (no filler when nothing fits)."""
    return search(docs, text, limit=limit, strict=True)


def meter(
    db: AsyncSession,
    user_id: str,
    meeting_id: str | None,
    kind: str,
    provider: str,
    input_tokens: int,
    output_tokens: int,
) -> None:
    for k, qty in (
        (kind, 1),
        ("llm_input_tokens", input_tokens),
        ("llm_output_tokens", output_tokens),
    ):
        db.add(
            UsageRecord(
                user_id=user_id,
                meeting_id=meeting_id,
                kind=k,
                provider=provider,
                quantity=float(qty),
            )
        )


async def remember_meeting(
    sessionmaker: async_sessionmaker[AsyncSession],
    provider: LLMProvider,
    *,
    memory_id: str,
    segments: Sequence[ContextSegment],
    questions: Sequence[str],
    title: str | None,
    language: str,
    started_at: datetime,
) -> None:
    """Summarize a finished meeting into its memory row (runs after the response)."""
    try:
        result = await summarize_meeting(
            provider,
            segments=segments,
            questions=questions,
            title=title,
            language=language,
            started_at=started_at,
        )
    except LLMError as e:
        log.warning("meeting memory failed", extra={"memory_id": memory_id, "error": str(e)})
        async with sessionmaker() as db:
            m = await db.get(Memory, memory_id)
            if m is not None:
                m.status = "failed"
                m.summary = str(e)[:500]
                await db.commit()
        return
    async with sessionmaker() as db:
        m = await db.get(Memory, memory_id)
        if m is None:  # deleted while it was being written
            return
        m.status = "ready"
        m.title = result.title
        m.summary = result.summary
        m.details = result.details
        m.suggestions = suggestions_json(result.suggestions)
        meter(
            db,
            m.user_id,
            m.meeting_id,
            "brain_memory",
            f"{provider.name}:{result.usage.model}",
            result.usage.input_tokens,
            result.usage.output_tokens,
        )
        await db.commit()
    log.info("meeting memory saved", extra={"memory_id": memory_id})
