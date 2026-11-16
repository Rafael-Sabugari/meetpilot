from typing import Literal

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field, model_validator

from app.ai.context import ContextSegment, build_context
from app.ai.copilot import Grounding, Mode, Tone, ask
from app.auth.deps import DB, CurrentUser
from app.auth.ratelimit import RateLimiter
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
    question: str | None = Field(default=None, max_length=1_000)
    transcript: list[ContextSegment] = Field(default_factory=list, max_length=500)
    tone: Tone = "natural"
    # Answer language; defaults to the meeting language.
    language: Literal["pt-BR", "en"] | None = None
    # Written by the user about themselves and the meeting; used for this answer only.
    user_context: str | None = Field(default=None, max_length=20_000)
    # Also return the answer translated into this language (the user's own), when different.
    translate_to: Literal["pt-BR", "en"] | None = None

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
    provider: LLMProvider = request.app.state.llm_provider
    meeting = await owned_meeting(db, meeting_id, user)

    if not _limiter(settings.copilot_requests_per_minute).hit(user.id):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "too many copilot requests")
    used = await count_this_period(db, user, "copilot_request")
    remaining = settings.copilot_requests_per_month - int(used)
    if remaining <= 0:
        raise HTTPException(status.HTTP_402_PAYMENT_REQUIRED, "monthly copilot requests exhausted")

    ctx = build_context(body.transcript, settings.copilot_max_context_chars)
    answer_language = body.language or (
        meeting.language if meeting.language in ("pt-BR", "en") else "auto"
    )
    try:
        result = await ask(
            provider,
            mode=body.mode,
            question=body.question,
            ctx=ctx,
            answer_language=answer_language,
            meeting_language=meeting.language,
            tone=body.tone,
            max_output_tokens=settings.copilot_max_output_tokens,
            user_context=body.user_context,
            translate_to=body.translate_to,
        )
    except LLMError as e:
        code = status.HTTP_503_SERVICE_UNAVAILABLE if e.retryable else status.HTTP_502_BAD_GATEWAY
        raise HTTPException(code, str(e)) from None

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
        answer_translation=result.answer_translation,
        provider=provider.name,
        context=ContextInfo(
            used_segments=ctx.used_segments,
            total_segments=ctx.total_segments,
            truncated=ctx.truncated,
        ),
        usage=TokenUsage(input_tokens=result.input_tokens, output_tokens=result.output_tokens),
        requests_remaining=remaining - 1,
    )
