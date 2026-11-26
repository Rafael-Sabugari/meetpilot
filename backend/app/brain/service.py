"""Second brain storage helpers and the end-of-meeting memory job."""

import logging
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.ai.context import ContextSegment
from app.brain.ai import suggestions_json, summarize_meeting
from app.brain.embeddings import Embedder, Vector, pack, unpack
from app.brain.search import Hit, MemoryDoc, hybrid, recent, split_passages
from app.db.models import Memory, MemoryVectors, UsageRecord
from app.providers.llm.base import LLMError, LLMProvider

log = logging.getLogger("meetpilot.brain")

# Memories loaded for search (newest first). Enough for years of meetings.
MAX_DOCS = 2_000


def to_doc(m: Memory, vectors: Sequence[Vector] = ()) -> MemoryDoc:
    text = "\n\n".join(p for p in (m.summary, m.details) if p.strip())
    return MemoryDoc(m.id, m.kind, m.title, m.occurred_at, text, tuple(vectors))


async def load_docs(db: AsyncSession, user_id: str, model: str | None = None) -> list[MemoryDoc]:
    """Ready memories, newest first, with their passage vectors when `model` matches."""
    rows = await db.execute(
        select(Memory, MemoryVectors)
        .outerjoin(MemoryVectors, MemoryVectors.memory_id == Memory.id)
        .where(Memory.user_id == user_id, Memory.status == "ready")
        .order_by(Memory.occurred_at.desc())
        .limit(MAX_DOCS)
    )
    return [
        to_doc(m, unpack(v.data) if v is not None and model and v.model == model else ())
        for m, v in rows.all()
    ]


async def query_vector(embedder: Embedder | None, text: str) -> Vector | None:
    if embedder is None or not text.strip():
        return None
    try:
        vecs = await embedder.embed([text[:2_000]], wait=False)
    except Exception:
        log.warning("query embedding failed", exc_info=True)
        return None
    return vecs[0] if vecs else None


def find(
    docs: Sequence[MemoryDoc], query: str, limit: int = 8, qvec: Vector | None = None
) -> list[Hit]:
    """Best passages by words and meaning, or the newest memories when nothing matches
    ("o que aconteceu ontem?")."""
    hits = hybrid(docs, query, qvec, limit=limit)
    return hits or recent(docs, min(limit, 5))


def relevant_for_meeting(
    docs: Sequence[MemoryDoc], text: str, limit: int = 4, qvec: Vector | None = None
) -> list[Hit]:
    """Memories for a live answer: only clear matches (no filler when nothing fits)."""
    return hybrid(docs, text, qvec, limit=limit, strict=True)


async def index_memory(
    sessionmaker: async_sessionmaker[AsyncSession], embedder: Embedder, memory_id: str
) -> None:
    """Embed a memory's passages (after it is created or changed)."""
    async with sessionmaker() as db:
        m = await db.get(Memory, memory_id)
        if m is None or m.status != "ready":
            return
        doc = to_doc(m)
        texts = [f"{p.title}\n{p.text}" for p in split_passages(doc)]
    try:
        vectors = await embedder.embed(texts)
    except Exception:
        log.warning("memory embedding failed", exc_info=True)
        return
    if len(vectors) != len(texts):
        return
    async with sessionmaker() as db:
        if await db.get(Memory, memory_id) is None:
            return
        row = await db.get(MemoryVectors, memory_id)
        if row is None:
            db.add(MemoryVectors(memory_id=memory_id, model=embedder.name, data=pack(vectors)))
        else:
            row.model = embedder.name
            row.data = pack(vectors)
        await db.commit()


async def index_missing(sessionmaker: async_sessionmaker[AsyncSession], embedder: Embedder) -> int:
    """Embed every ready memory without vectors for the current model (startup backfill)."""
    async with sessionmaker() as db:
        rows = await db.execute(
            select(Memory.id)
            .outerjoin(MemoryVectors, MemoryVectors.memory_id == Memory.id)
            .where(Memory.status == "ready")
            .where((MemoryVectors.memory_id.is_(None)) | (MemoryVectors.model != embedder.name))
        )
        ids = [r[0] for r in rows.all()]
    for memory_id in ids:
        await index_memory(sessionmaker, embedder, memory_id)
    return len(ids)


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
    embedder: Embedder | None = None,
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
    if embedder is not None:
        await index_memory(sessionmaker, embedder, memory_id)
