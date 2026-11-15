"""Live assistance during a transcribed meeting (brief §6.4 data flow):

    final segment ─┬─► rolling context (memory only, this meeting)
                   ├─► translation (if enabled)            ─► translation.updated
                   └─► question detection (others' speech) ─► question.detected
                            ├─ rules (free)
                            └─ AI check for lines the rules cannot settle (rate limited)
                            └─► policy (debounce/dedupe/rate) ─► answer.started/completed/failed

Nothing here is stored or logged; only usage counters are reported for metering.
"""

import asyncio
import contextlib
import logging
import time
import uuid
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from app.ai.context import ContextSegment, build_context
from app.ai.copilot import Tone, ask
from app.ai.question_check import check as check_question
from app.ai.questions import AutoAnswerPolicy, AutoMode, assess, should_answer, worth_checking
from app.ai.translation import needs_translation, translate
from app.providers.llm.base import LLMError, LLMProvider, LLMResult
from app.realtime.events import EventType, TranscriptSegment

log = logging.getLogger("meetpilot.assist")

Emit = Callable[[EventType, dict[str, Any]], Awaitable[None]]


class LiveSettings(BaseModel):
    """Sent by the desktop as `{"type": "copilot.settings", ...}`; re-sent after reconnects."""

    auto_suggestions: AutoMode = "directed"
    tone: Tone = "natural"
    translate_to: Literal["pt-BR", "en"] | None = None
    # Written by the user about themselves and the meeting (kept in memory only).
    user_context: str | None = Field(default=None, max_length=4_000)

    @field_validator("user_context")
    @classmethod
    def _blank_is_none(cls, v: str | None) -> str | None:
        return v.strip() or None if v is not None else None


@dataclass
class UsageItem:
    kind: str
    provider: str
    quantity: float


class LiveAssist:
    MAX_TRANSLATION_BACKLOG = 20
    MAX_CHECKS_PER_MINUTE = 30
    MAX_CHECKS_IN_FLIGHT = 3

    def __init__(
        self,
        *,
        emit: Emit,
        llm: LLMProvider,
        meeting_language: str,
        context: deque[ContextSegment],
        copilot_remaining: int,
        max_context_chars: int,
        max_output_tokens: int,
        policy: AutoAnswerPolicy | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.emit = emit
        self.llm = llm
        self.meeting_language = meeting_language
        self.context = context
        self.copilot_remaining = copilot_remaining
        self.max_context_chars = max_context_chars
        self.max_output_tokens = max_output_tokens
        self.policy = policy or AutoAnswerPolicy()
        self.clock = clock
        self.settings = LiveSettings()
        self.usage: list[UsageItem] = []
        # Question text -> id of the detection event, so a deferred answer reuses its id.
        self._question_ids: dict[str, str] = {}
        self._tasks: set[asyncio.Task[None]] = set()
        self._translations: asyncio.Queue[TranscriptSegment] = asyncio.Queue(
            self.MAX_TRANSLATION_BACKLOG
        )
        self._translated: set[str] = set()
        self._translator: asyncio.Task[None] | None = None
        self._backlog_warned = False
        self._quota_warned = False
        self._checks: deque[float] = deque()
        self._checks_in_flight = 0

    # -- settings -----------------------------------------------------------------------

    def update_settings(self, raw: dict[str, Any]) -> None:
        data = {k: v for k, v in raw.items() if k != "type"}
        self.settings = LiveSettings.model_validate(data)

    # -- inputs -------------------------------------------------------------------------

    async def on_final(self, seg: TranscriptSegment) -> None:
        text = seg.text.strip()
        if not text:
            return
        self.context.append(
            ContextSegment(source=seg.source, text=text[:2_000], start_ms=seg.start_ms)
        )
        if needs_translation(self.meeting_language, self.settings.translate_to):
            self._enqueue_translation(seg)
        if seg.source == "system":
            mode = self.settings.auto_suggestions
            a = assess(text)
            if should_answer(a, mode):
                await self._detected(seg, text, a.directed, "rules")
            elif worth_checking(text, a, mode) and self._check_allowed():
                recent = list(self.context)[-8:]
                self._spawn(self._check(seg, text, recent))

    async def _detected(self, seg: TranscriptSegment, text: str, directed: bool, via: str) -> None:
        # Derived from the segment id, which stays unique across reconnects.
        qid = f"q-{seg.segment_id}"
        self._question_ids[text] = qid
        while len(self._question_ids) > 50:
            self._question_ids.pop(next(iter(self._question_ids)))
        await self.emit(
            EventType.QUESTION_DETECTED,
            {
                "question_id": qid,
                "segment_id": seg.segment_id,
                "text": text,
                "directed": directed,
                "via": via,
            },
        )
        if self.policy.offer(text, self.clock()):
            self._spawn(self._answer(qid, text))

    def _check_allowed(self) -> bool:
        now = self.clock()
        while self._checks and now - self._checks[0] > 60:
            self._checks.popleft()
        if (
            len(self._checks) >= self.MAX_CHECKS_PER_MINUTE
            or self._checks_in_flight >= self.MAX_CHECKS_IN_FLIGHT
        ):
            return False
        self._checks.append(now)
        return True

    async def _check(self, seg: TranscriptSegment, text: str, recent: list[ContextSegment]) -> None:
        self._checks_in_flight += 1
        try:
            is_q, directed, result = await check_question(
                self.llm, recent, self.settings.user_context
            )
        except LLMError:
            return  # best effort: a failed check just means no automatic suggestion
        finally:
            self._checks_in_flight -= 1
        self._meter(result, "question_check")
        mode = self.settings.auto_suggestions
        if is_q and (mode == "all" or (mode == "directed" and directed)):
            await self._detected(seg, text, directed, "ai")

    async def tick(self) -> None:
        nxt = self.policy.tick(self.clock())
        if nxt is not None:
            self._spawn(self._answer(self._qid(nxt), nxt))

    def _qid(self, text: str) -> str:
        return self._question_ids.get(text) or f"q-{uuid.uuid4().hex[:12]}"

    # -- answers ------------------------------------------------------------------------

    def _spawn(self, coro: Awaitable[None]) -> None:
        task: asyncio.Task[None] = asyncio.ensure_future(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _meter(self, result: LLMResult, kind: str) -> None:
        provider = f"{self.llm.name}:{result.model}"
        self.usage.append(UsageItem(kind, provider, 1))
        self.usage.append(UsageItem("llm_input_tokens", provider, result.input_tokens))
        self.usage.append(UsageItem("llm_output_tokens", provider, result.output_tokens))

    async def _answer(self, qid: str, question: str) -> None:
        try:
            if self.copilot_remaining <= 0:
                if not self._quota_warned:
                    self._quota_warned = True
                    await self.emit(
                        EventType.ANSWER_FAILED,
                        {
                            "question_id": qid,
                            "question": question,
                            "code": "copilot_quota",
                            "message": "monthly copilot requests exhausted",
                            "retryable": False,
                        },
                    )
                return
            await self.emit(EventType.ANSWER_STARTED, {"question_id": qid, "question": question})
            self.copilot_remaining -= 1  # reserve before the call (parallel safety)
            try:
                result = await ask(
                    self.llm,
                    mode="ask",
                    question=(
                        f'Others just asked: "{question}". Suggest what the User could say in '
                        "reply, following all rules."
                    ),
                    ctx=build_context(list(self.context), self.max_context_chars),
                    answer_language=(
                        self.meeting_language
                        if self.meeting_language in ("pt-BR", "en")
                        else "auto"
                    ),
                    meeting_language=self.meeting_language,
                    tone=self.settings.tone,
                    max_output_tokens=self.max_output_tokens,
                    user_context=self.settings.user_context,
                )
            except LLMError as e:
                self.copilot_remaining += 1  # failed calls are not billed
                await self.emit(
                    EventType.ANSWER_FAILED,
                    {
                        "question_id": qid,
                        "question": question,
                        "code": "llm_error",
                        "message": str(e),
                        "retryable": e.retryable,
                    },
                )
                return
            self._meter(
                LLMResult({}, result.input_tokens, result.output_tokens, result.model),
                "copilot_request",
            )
            await self.emit(
                EventType.ANSWER_COMPLETED,
                {
                    "question_id": qid,
                    "question": question,
                    "answer": result.answer,
                    "grounding": result.grounding,
                    "explanation": result.explanation,
                    "missing_info": result.missing_info,
                    "provider": self.llm.name,
                    "requests_remaining": max(0, self.copilot_remaining),
                },
            )
        finally:
            nxt = self.policy.finished(self.clock())
            if nxt is not None:
                self._spawn(self._answer(self._qid(nxt), nxt))

    # -- translation --------------------------------------------------------------------

    def _enqueue_translation(self, seg: TranscriptSegment) -> None:
        if seg.segment_id in self._translated:
            return  # never translate the same segment twice
        self._translated.add(seg.segment_id)
        if self._translator is None:
            self._translator = asyncio.ensure_future(self._translate_loop())
        try:
            self._translations.put_nowait(seg)
        except asyncio.QueueFull:
            if not self._backlog_warned:
                self._backlog_warned = True
                self._spawn(
                    self.emit(
                        EventType.ERROR,
                        {
                            "code": "translation_backlog",
                            "message": "translation is falling behind; some lines were skipped",
                            "retryable": True,
                        },
                    )
                )

    async def _translate_loop(self) -> None:
        while True:
            seg = await self._translations.get()
            target = self.settings.translate_to
            if target is None:
                continue
            try:
                text, result = await translate(self.llm, seg.text, target)
            except LLMError as e:
                log.warning("translation failed", extra={"retryable": e.retryable})
                continue
            finally:
                self._translations.task_done()
            self._meter(result, "translation_request")
            await self.emit(
                EventType.TRANSLATION_UPDATED,
                {
                    "segment_id": seg.segment_id,
                    "source": seg.source,
                    "target_language": target,
                    "text": text,
                },
            )

    # -- shutdown -----------------------------------------------------------------------

    async def drain(self, timeout: float) -> None:
        """Let in-flight answers and queued translations finish (bounded), then stop."""
        deadline = self.clock() + timeout
        with contextlib.suppress(TimeoutError):
            if self._translator is not None:
                await asyncio.wait_for(self._translations.join(), timeout)
        while self._tasks and self.clock() < deadline:
            await asyncio.wait(set(self._tasks), timeout=max(0.0, deadline - self.clock()))
        for t in list(self._tasks):
            t.cancel()
        if self._translator is not None:
            self._translator.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._translator
