from typing import Literal

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field, model_validator

from app.ai.context import ContextSegment, build_context
from app.ai.copilot import Grounding, Mode, Scenario, Tone, ask, translate_answer
from app.auth.deps import DB, CurrentUser
from app.auth.ratelimit import RateLimiter
from app.brain.search import render
from app.brain.service import load_docs, relevant_for_meeting
from app.core.config import Settings
from app.db.models import UsageRecord
from app.meetings.router import owned_meeting
from app.providers.llm.base import LLMError, LLMProvider
from app.usage.service import count_this_period

router = APIRouter(tags=["copilot"])
_per_minute: dict[int, RateLimiter] = {}


def _limiter(limit: int) -> RateLimiter:
    if limit not in _per_minute:
        _per_minute[limit] = RateLimiter(max_events=limit, window_seconds=60)
    return _per_minute[limit]


class CopilotRequest(BaseModel):
    mode: Mode = "ask"
    # Up to a few minutes of what Others said, for "Responder ao que falaram".
    question: str | None = Field(default=None, max_length=4_000)
    transcript: list[ContextSegment] = Field(default_factory=list, max_length=500)
    tone: Tone = "natural"
    scenario: Scenario = "interview"
    # Answer language; defaults to the meeting language.
    language: Literal["pt-BR", "en"] | None = None
    # Written by the user about themselves and the meeting; used for this answer only.
    user_context: str | None = Field(default=None, max_length=60_000)
    # Also return the answer translated into this language (the user's own), when different.
    translate_to: Literal["pt-BR", "en"] | None = None
    # Second brain: let the answer use relevant memories (earlier meetings and notes).
    use_memory: bool = True

    @model_validator(mode="after")
    def _question_for_ask(self) -> "CopilotRequest":
        if self.mode == "ask" and not (self.question or "").strip():
            raise ValueError("question is required when mode is 'ask'")
        return self


class ContextInfo(BaseModel):
    used_segments: int
    total_segments: int
    truncated: bool


class TokenUsage(BaseModel):
    input_tokens: int
    output_tokens: int


class CopilotResponse(BaseModel):
    answer: str
    grounding: Grounding
    explanation: str
    missing_info: list[str]
    answer_translation: str | None = None
    provider: str
    context: ContextInfo
    usage: TokenUsage
    requests_remaining: int


@router.post("/meetings/{meeting_id}/copilot", response_model=CopilotResponse)
async def copilot(
    meeting_id: str, body: CopilotRequest, request: Request, user: CurrentUser, db: DB
) -> CopilotResponse:
    settings: Settings = request.app.state.settings
    provider: LLMProvider = request.app.state.answer_llm_provider
    meeting = await owned_meeting(db, meeting_id, user)

    if not _limiter(settings.copilot_requests_per_minute).hit(user.id):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "too many copilot requests")
    used = await count_this_period(db, user, "copilot_request")
    remaining = settings.copilot_requests_per_month - int(used)
    if remaining <= 0:
        raise HTTPException(status.HTTP_402_PAYMENT_REQUIRED, "monthly copilot requests exhausted")

    ctx = build_context(body.transcript, settings.copilot_max_context_chars)
    history = request.app.state.active_streams.history_for(meeting.id)
    answer_language = body.language or (
        meeting.language if meeting.language in ("pt-BR", "en") else "auto"
    )
    memory: str | None = None
    if body.use_memory:
        docs = await load_docs(db, user.id)
        recent = " ".join(s.text for s in body.transcript[-3:])
        query = f"{body.question or ''} {body.question or ''} {recent}"
        memory = render(relevant_for_meeting(docs, query), 8_000) or None
    try:
        result = await ask(
            provider,
            mode=body.mode,
            question=body.question,
            ctx=ctx,
            answer_language=answer_language,
            meeting_language=meeting.language,
            tone=body.tone,
            scenario=body.scenario,
            max_output_tokens=settings.copilot_max_output_tokens,
            user_context=body.user_context,
            earlier=list(history),
            memory=memory,
        )
    except LLMError as e:
        code = status.HTTP_503_SERVICE_UNAVAILABLE if e.retryable else status.HTTP_502_BAD_GATEWAY
        raise HTTPException(code, str(e)) from None

    # Translation in a separate fast call: the answer model writes half as much.
    translation: str | None = None
    if body.translate_to:
        try:
            translation, _ = await translate_answer(
                request.app.state.llm_provider, result.answer, answer_language
            )
        except LLMError:
            translation = None  # the answer still goes out

    if body.mode == "ask" and body.question:
        history.append((body.question, result.answer))
    elif body.mode == "reply":
        history.append((body.question or "(how to reply to the latest question)", result.answer))
    # Metering only (no prompt or answer content is stored).
    for kind, qty in (
        ("copilot_request", 1),
        ("llm_input_tokens", result.input_tokens),
        ("llm_output_tokens", result.output_tokens),
    ):
        db.add(
            UsageRecord(
                user_id=user.id,
                meeting_id=meeting.id,
                kind=kind,
                provider=f"{provider.name}:{result.model}",
                quantity=float(qty),
            )
        )
    await db.commit()

    return CopilotResponse(
        answer=result.answer,
        grounding=result.grounding,
        explanation=result.explanation,
        missing_info=result.missing_info,
        answer_translation=translation,
        provider=provider.name,
        context=ContextInfo(
            used_segments=ctx.used_segments,
            total_segments=ctx.total_segments,
            truncated=ctx.truncated,
        ),
        usage=TokenUsage(input_tokens=result.input_tokens, output_tokens=result.output_tokens),
        requests_remaining=remaining - 1,
    )
