from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.auth.deps import DB, CurrentUser, tokens
from app.auth.security import TokenService
from app.brain.service import remember_meeting
from app.db.base import utcnow
from app.db.models import Meeting, Memory, User
from app.usage.service import summary

router = APIRouter(tags=["meetings"])

Language = Literal["pt-BR", "en", "multi"]


class MeetingCreate(BaseModel):
    language: Language = "pt-BR"
    title: str | None = Field(default=None, max_length=200)
    # "local" = transcribed on the user's computer (free; no audio minutes needed).
    stt: Literal["cloud", "local"] = "cloud"


class MeetingOut(BaseModel):
    id: str
    title: str | None
    language: str
    status: str
    created_at: datetime
    ended_at: datetime | None
    audio_seconds: float
    # Second brain: the memory being written for this meeting, when the User asked for one.
    memory_id: str | None = None


class FinishRequest(BaseModel):
    # None = use the "remember this meeting" option the desktop sent during the meeting.
    remember: bool | None = None


class StreamGrant(BaseModel):
    stream_token: str
    expires_in: int
    stream_path: str


class MeetingCreated(BaseModel):
    meeting: MeetingOut
    stream: StreamGrant


def _out(m: Meeting) -> MeetingOut:
    return MeetingOut(
        id=m.id,
        title=m.title,
        language=m.language,
        status=m.status,
        created_at=m.created_at,
        ended_at=m.ended_at,
        audio_seconds=round(m.audio_seconds, 2),
    )


async def owned_meeting(db: DB, meeting_id: str, user: User) -> Meeting:
    m = await db.get(Meeting, meeting_id)
    # 404 (not 403) for other users' meetings: do not reveal that the id exists.
    if m is None or m.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "meeting not found")
    return m


def _grant(svc: TokenService, m: Meeting, user: User) -> StreamGrant:
    token, ttl = svc.issue("stream", user.id, mid=m.id)
    return StreamGrant(
        stream_token=token, expires_in=ttl, stream_path=f"/api/v1/meetings/{m.id}/stream"
    )


@router.post("/meetings", response_model=MeetingCreated, status_code=status.HTTP_201_CREATED)
async def create_meeting(
    body: MeetingCreate,
    request: Request,
    user: CurrentUser,
    db: DB,
    svc: Annotated[TokenService, Depends(tokens)],
) -> MeetingCreated:
    usage = await summary(db, request.app.state.settings, user)
    if usage.remaining_seconds <= 0 and body.stt == "cloud":
        raise HTTPException(status.HTTP_402_PAYMENT_REQUIRED, "monthly audio minutes exhausted")
    m = Meeting(user_id=user.id, language=body.language, title=body.title)
    db.add(m)
    await db.commit()
    return MeetingCreated(meeting=_out(m), stream=_grant(svc, m, user))


@router.post("/meetings/{meeting_id}/stream-token", response_model=StreamGrant)
async def stream_token(
    meeting_id: str,
    user: CurrentUser,
    db: DB,
    svc: Annotated[TokenService, Depends(tokens)],
) -> StreamGrant:
    """Fresh short-lived token for (re)connecting the realtime stream."""
    m = await owned_meeting(db, meeting_id, user)
    if m.status != "active":
        raise HTTPException(status.HTTP_409_CONFLICT, "meeting is finished")
    return _grant(svc, m, user)


@router.get("/meetings", response_model=list[MeetingOut])
async def list_meetings(user: CurrentUser, db: DB, limit: int = 50) -> list[MeetingOut]:
    rows = await db.execute(
        select(Meeting)
        .where(Meeting.user_id == user.id)
        .order_by(Meeting.created_at.desc())
        .limit(max(1, min(limit, 200)))
    )
    return [_out(m) for m in rows.scalars()]


@router.get("/meetings/{meeting_id}", response_model=MeetingOut)
async def get_meeting(meeting_id: str, user: CurrentUser, db: DB) -> MeetingOut:
    return _out(await owned_meeting(db, meeting_id, user))


@router.post("/meetings/{meeting_id}/finish", response_model=MeetingOut)
async def finish_meeting(
    meeting_id: str,
    request: Request,
    user: CurrentUser,
    db: DB,
    background: BackgroundTasks,
    body: FinishRequest | None = None,
) -> MeetingOut:
    m = await owned_meeting(db, meeting_id, user)
    if m.status != "finished":
        m.status = "finished"
        m.ended_at = utcnow()
        await db.commit()
    active = request.app.state.active_streams
    remember = (
        body.remember
        if body is not None and body.remember is not None
        else active.remember.get(m.id, False)
    )
    segments = [s for s in active.contexts.get(m.id, ()) if s.text.strip()]
    questions = [q for q, _ in active.suggestions.get(m.id, ())]
    # Forget the live-assistance context held in memory for this meeting.
    active.forget(m.id)
    out = _out(m)
    if remember and segments:
        existing = await db.execute(select(Memory.id).where(Memory.meeting_id == m.id))
        if existing.first() is None:
            # Only a summary is kept (written after this response); never audio or the
            # transcript itself.
            memory = Memory(
                user_id=user.id,
                meeting_id=m.id,
                kind="meeting",
                status="processing",
                title=m.title or "Reunião",
                occurred_at=m.created_at,
                summary="",
                details="",
                suggestions="[]",
            )
            db.add(memory)
            await db.commit()
            out.memory_id = memory.id
            background.add_task(
                remember_meeting,
                request.app.state.sessionmaker,
                request.app.state.answer_llm_provider,
                memory_id=memory.id,
                segments=segments,
                questions=questions,
                title=m.title,
                language=m.language,
                started_at=m.created_at,
            )
    return out


@router.delete("/meetings/{meeting_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_meeting(meeting_id: str, request: Request, user: CurrentUser, db: DB) -> Response:
    m = await owned_meeting(db, meeting_id, user)
    request.app.state.active_streams.forget(m.id)
    await db.delete(m)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
