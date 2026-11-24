"""Second brain API: memories (meeting summaries and notes), questions and briefings."""

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.auth.deps import DB, CurrentUser
from app.auth.ratelimit import RateLimiter
from app.brain.ai import ask_brain, briefing, suggestions_json, suggestions_list
from app.brain.search import search
from app.brain.service import find, load_docs, meter, to_doc
from app.db.base import utcnow
from app.db.models import Memory, User
from app.providers.llm.base import LLMError, LLMProvider

router = APIRouter(prefix="/brain", tags=["brain"])
_limiter = RateLimiter(max_events=20, window_seconds=60)

MAX_NOTE_CHARS = 60_000


class MemoryOut(BaseModel):
    id: str
    kind: str
    status: str
    title: str
    occurred_at: datetime
    meeting_id: str | None
    summary: str
    details: str
    suggestions: list[str]


class NoteIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    text: str = Field(min_length=1, max_length=MAX_NOTE_CHARS)
    occurred_at: datetime | None = None


class MemoryPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    summary: str | None = Field(default=None, max_length=3_000)
    details: str | None = Field(default=None, max_length=MAX_NOTE_CHARS)
    suggestions: list[str] | None = Field(default=None, max_length=20)


class Turn(BaseModel):
    role: Literal["user", "brain"]
    text: str = Field(max_length=8_000)


class AskIn(BaseModel):
    question: str = Field(min_length=1, max_length=2_000)
    history: list[Turn] = Field(default_factory=list, max_length=20)
    user_context: str | None = Field(default=None, max_length=60_000)


class BriefingIn(BaseModel):
    topic: str = Field(min_length=1, max_length=1_000)
    user_context: str | None = Field(default=None, max_length=60_000)


class SourceOut(BaseModel):
    id: str
    title: str
    kind: str
    occurred_at: datetime


class AnswerOut(BaseModel):
    answer: str
    sources: list[SourceOut]
    input_tokens: int
    output_tokens: int


def _out(m: Memory) -> MemoryOut:
    return MemoryOut(
        id=m.id,
        kind=m.kind,
        status=m.status,
        title=m.title,
        occurred_at=m.occurred_at,
        meeting_id=m.meeting_id,
        summary=m.summary,
        details=m.details,
        suggestions=suggestions_list(m.suggestions),
    )


async def _owned(db: DB, memory_id: str, user: User) -> Memory:
    m = await db.get(Memory, memory_id)
    if m is None or m.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "memory not found")
    return m


@router.get("/memories", response_model=list[MemoryOut])
async def list_memories(
    user: CurrentUser, db: DB, q: str | None = None, limit: int = 100
) -> list[MemoryOut]:
    limit = max(1, min(limit, 500))
    rows = await db.execute(
        select(Memory).where(Memory.user_id == user.id).order_by(Memory.occurred_at.desc())
    )
    memories = list(rows.scalars())
    if q and q.strip():
        by_id = {m.id: m for m in memories}
        ranked: list[str] = []
        docs = [to_doc(m) for m in memories if m.status == "ready"]
        for hit in search(docs, q, limit=limit * 3):
            if hit.passage.memory_id not in ranked:
                ranked.append(hit.passage.memory_id)
        memories = [by_id[i] for i in ranked]
    return [_out(m) for m in memories[:limit]]


@router.post("/memories", response_model=MemoryOut, status_code=status.HTTP_201_CREATED)
async def create_note(body: NoteIn, user: CurrentUser, db: DB) -> MemoryOut:
    m = Memory(
        user_id=user.id,
        kind="note",
        status="ready",
        title=" ".join(body.title.split()),
        occurred_at=body.occurred_at or utcnow(),
        summary="",
        details=body.text.strip(),
        suggestions="[]",
    )
    db.add(m)
    await db.commit()
    return _out(m)


@router.get("/memories/{memory_id}", response_model=MemoryOut)
async def get_memory(memory_id: str, user: CurrentUser, db: DB) -> MemoryOut:
    return _out(await _owned(db, memory_id, user))


@router.patch("/memories/{memory_id}", response_model=MemoryOut)
async def update_memory(memory_id: str, body: MemoryPatch, user: CurrentUser, db: DB) -> MemoryOut:
    m = await _owned(db, memory_id, user)
    if body.title is not None:
        m.title = " ".join(body.title.split())
    if body.summary is not None:
        m.summary = body.summary.strip()
    if body.details is not None:
        m.details = body.details.strip()
    if body.suggestions is not None:
        m.suggestions = suggestions_json([s.strip()[:400] for s in body.suggestions if s.strip()])
    await db.commit()
    return _out(m)


@router.delete("/memories/{memory_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_memory(memory_id: str, user: CurrentUser, db: DB) -> Response:
    await db.delete(await _owned(db, memory_id, user))
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


async def _run(
    request: Request,
    user: User,
    db: DB,
    query: str,
    call: str,
    body: AskIn | BriefingIn,
) -> AnswerOut:
    if not _limiter.hit(user.id):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "too many brain requests")
    provider: LLMProvider = request.app.state.answer_llm_provider
    docs = await load_docs(db, user.id)
    hits = find(docs, query)
    try:
        if isinstance(body, AskIn):
            result = await ask_brain(
                provider,
                question=body.question,
                hits=hits,
                user_context=body.user_context,
                history=[(t.role, t.text) for t in body.history],
            )
        else:
            result = await briefing(
                provider, topic=body.topic, hits=hits, user_context=body.user_context
            )
    except LLMError as e:
        code = status.HTTP_503_SERVICE_UNAVAILABLE if e.retryable else status.HTTP_502_BAD_GATEWAY
        raise HTTPException(code, str(e)) from None
    meter(
        db,
        user.id,
        None,
        call,
        f"{provider.name}:{result.usage.model}",
        result.usage.input_tokens,
        result.usage.output_tokens,
    )
    await db.commit()
    by_id = {h.passage.memory_id: h.passage for h in hits}
    return AnswerOut(
        answer=result.answer,
        sources=[
            SourceOut(
                id=i, title=by_id[i].title, kind=by_id[i].kind, occurred_at=by_id[i].occurred_at
            )
            for i in result.sources
        ],
        input_tokens=result.usage.input_tokens,
        output_tokens=result.usage.output_tokens,
    )


@router.post("/ask", response_model=AnswerOut)
async def ask(body: AskIn, request: Request, user: CurrentUser, db: DB) -> AnswerOut:
    # Earlier user turns help follow-ups ("e com ele, o que ficou combinado?").
    earlier = " ".join(t.text for t in body.history if t.role == "user")[-500:]
    return await _run(request, user, db, f"{body.question} {earlier}", "brain_request", body)


@router.post("/briefing", response_model=AnswerOut)
async def make_briefing(body: BriefingIn, request: Request, user: CurrentUser, db: DB) -> AnswerOut:
    return await _run(request, user, db, body.topic, "brain_briefing", body)
