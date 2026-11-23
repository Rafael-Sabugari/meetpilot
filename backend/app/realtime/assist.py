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
from app.ai.copilot import Tone, ask, translate_answer
from app.ai.question_check import check as check_question
from app.ai.questions import AutoAnswerPolicy, AutoMode, assess, should_answer, worth_checking
from app.ai.translation import needs_translation, same_text, translate
from app.providers.llm.base import LLMError, LLMProvider, LLMResult
from app.realtime.events import EventType, TranscriptSegment

log = logging.getLogger("meetpilot.assist")

Emit = Callable[[EventType, dict[str, Any]], Awaitable[None]]


# What Others said, sent with "Responder ao que falaram".
MAX_REQUEST_CHARS = 4_000


class LiveSettings(BaseModel):
    """Sent by the desktop as `{"type": "copilot.settings", ...}`; re-sent after reconnects."""

    auto_suggestions: AutoMode = "directed"
    tone: Tone = "natural"
    translate_to: Literal["pt-BR", "en"] | None = None
    # Written by the user about themselves and the meeting (kept in memory only).
    user_context: str | None = Field(default=None, max_length=60_000)

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
    MAX_UTTERANCE_PARTS = 6
    MAX_UTTERANCE_CHARS = 800

    def __init__(
        self,
        *,
        emit: Emit,
        llm: LLMProvider,
        meeting_language: str,
        context: deque[ContextSegment],
        copilot_remaining: int,
        answer_llm: LLMProvider | None = None,
        history: deque[tuple[str, str]] | None = None,
        id_prefix: str = "",
        max_context_chars: int,
        max_output_tokens: int,
        policy: AutoAnswerPolicy | None = None,
        clock: Callable[[], float] = time.monotonic,
        utterance_gap: float = 1.5,
    ) -> None:
        self.utterance_gap = utterance_gap
        self.emit = emit
        self.llm = llm
        self.answer_llm = answer_llm or llm
        self._requests = 0
        # Segment ids restart with each meeting; the prefix keeps question ids unique.
        self.id_prefix = f"{id_prefix}-" if id_prefix else ""
        self._last_request = float("-inf")
        self.meeting_language = meeting_language
        self.context = context
        self.history: deque[tuple[str, str]] = history if history is not None else deque(maxlen=12)
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
        self._utterance: list[TranscriptSegment] = []
        self._utterance_at = 0.0
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
            # Speech recognition splits a sentence at short pauses ("…com data cloud" /
            # "ou marketing cloud…?"). Wait until the other person stops talking and judge
            # the whole utterance, so one question gives one answer.
            self._utterance.append(seg)
            self._utterance_at = self.clock()
            if (
                self.utterance_gap <= 0
                or len(self._utterance) >= self.MAX_UTTERANCE_PARTS
                or sum(len(s.text) for s in self._utterance) >= self.MAX_UTTERANCE_CHARS
            ):
                await self._flush_utterance()
        else:
            # The user started talking: the other person's turn is over.
            await self._flush_utterance()

    async def _flush_utterance(self) -> None:
        parts, self._utterance = self._utterance, []
        if not parts:
            return
        text = " ".join(p.text.strip() for p in parts if p.text.strip())
        last = parts[-1]
        mode = self.settings.auto_suggestions
        a = assess(text)
        ids = [p.segment_id for p in parts]
        if should_answer(a, mode):
            await self._detected(last, text, a.directed, "rules", ids)
        elif worth_checking(text, a, mode) and self._check_allowed():
            recent = list(self.context)[-8:]
            self._spawn(self._check(last, text, recent, ids))

    async def _detected(
        self,
        seg: TranscriptSegment,
        text: str,
        directed: bool,
        via: str,
        segment_ids: list[str] | None = None,
    ) -> None:
        if self.policy.is_repeat(text, self.clock()):
            return  # same question again (re-transcribed or rephrased): already handled
        # Derived from the segment id, which stays unique across reconnects.
        qid = f"q-{self.id_prefix}{seg.segment_id}"
        self._question_ids[text] = qid
        while len(self._question_ids) > 50:
            self._question_ids.pop(next(iter(self._question_ids)))
        await self.emit(
            EventType.QUESTION_DETECTED,
            {
                "question_id": qid,
                "segment_id": seg.segment_id,
                "segment_ids": segment_ids or [seg.segment_id],
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

    async def _check(
        self,
        seg: TranscriptSegment,
        text: str,
        recent: list[ContextSegment],
        segment_ids: list[str] | None = None,
    ) -> None:
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
            await self._detected(seg, text, directed, "ai", segment_ids)

    async def tick(self) -> None:
        if self._utterance and self.clock() - self._utterance_at >= self.utterance_gap:
            await self._flush_utterance()
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

    async def _translate(self, qid: str, answer: str, answer_language: str) -> None:
        """The suggestion in the other language, sent right after the answer itself."""
        try:
            text, result = await translate_answer(self.llm, answer, answer_language)
        except LLMError:
            return  # the answer is already there; the translation is a bonus
        self._meter(result, "answer_translation")
        if text:
            await self.emit(
                EventType.ANSWER_TRANSLATED, {"question_id": qid, "answer_translation": text}
            )

    def request(self, said: str) -> None:
        """The User asked for a suggestion to what Others said (not auto-detected)."""
        text = " ".join(said.split())[:MAX_REQUEST_CHARS]
        now = self.clock()
        if not text or now - self._last_request < 1.0:
            return  # empty, or a double press
        self._last_request = now
        self._requests += 1
        self._spawn(self._answer(f"r-{uuid.uuid4().hex[:12]}", text, requested=True))

    async def _answer(self, qid: str, question: str, *, requested: bool = False) -> None:
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
            answer_language = (
                self.meeting_language if self.meeting_language in ("pt-BR", "en") else "auto"
            )
            self.copilot_remaining -= 1  # reserve before the call (parallel safety)

            async def partial(text: str) -> None:
                await self.emit(EventType.ANSWER_DELTA, {"question_id": qid, "text": text})

            try:
                result = await ask(
                    self.answer_llm,
                    mode="ask",
                    question=(
                        "Others just said (the part not answered yet; the User asked for a "
                        f'reply): "{question}". Suggest what the User could say in reply, '
                        "following all rules."
                        if requested
                        else f'Others just asked: "{question}". Suggest what the User could '
                        "say in reply, following all rules."
                    ),
                    ctx=build_context(list(self.context), self.max_context_chars),
                    answer_language=answer_language,
                    meeting_language=self.meeting_language,
                    tone=self.settings.tone,
                    max_output_tokens=self.max_output_tokens,
                    user_context=self.settings.user_context,
                    earlier=list(self.history),
                    on_partial=partial,
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
            self.history.append((question, result.answer))
            await self.emit(
                EventType.ANSWER_COMPLETED,
                {
                    "question_id": qid,
                    "question": question,
                    "answer": result.answer,
                    "grounding": result.grounding,
                    "explanation": result.explanation,
                    "missing_info": result.missing_info,
                    "answer_translation": None,
                    "provider": self.llm.name,
                    "requests_remaining": max(0, self.copilot_remaining),
                },
            )
            if self.settings.translate_to:
                self._spawn(self._translate(qid, result.answer, answer_language))
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
            if same_text(text, seg.text):
                continue  # already in the target language: nothing to show
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
        await self._flush_utterance()
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
