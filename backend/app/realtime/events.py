"""Versioned realtime event schemas (server -> desktop overlay, and control messages).

Event names must match `packages/shared-types/src/realtime.ts`; see
`tests/test_event_contract.py`.
"""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

EVENT_SCHEMA_VERSION: Literal[1] = 1


class EventType(StrEnum):
    CAPTURE_STARTED = "capture.started"
    CAPTURE_PAUSED = "capture.paused"
    CAPTURE_RESUMED = "capture.resumed"
    CAPTURE_STOPPED = "capture.stopped"
    TRANSCRIPT_PARTIAL = "transcript.partial"
    TRANSCRIPT_FINAL = "transcript.final"
    TRANSLATION_UPDATED = "translation.updated"
    QUESTION_DETECTED = "question.detected"
    ANSWER_STARTED = "answer.started"
    ANSWER_COMPLETED = "answer.completed"
    ANSWER_FAILED = "answer.failed"
    USAGE_UPDATED = "usage.updated"
    MEETING_SUMMARY_READY = "meeting.summary.ready"
    ERROR = "error"


AudioSource = Literal["system", "microphone"]
GroundingLabel = Literal[
    "from_your_profile", "from_your_documents", "general_suggestion", "needs_confirmation"
]


class Envelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    v: Literal[1] = EVENT_SCHEMA_VERSION
    type: EventType
    meeting_id: str = Field(min_length=1, max_length=64)
    seq: int = Field(ge=0)
    ts: datetime = Field(default_factory=lambda: datetime.now(UTC))
    payload: dict[str, Any] = Field(default_factory=dict)


class AudioFormat(BaseModel):
    """The only format accepted on the realtime audio channel."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    encoding: Literal["pcm_s16le"] = "pcm_s16le"
    sample_rate: Literal[16000] = 16000
    channels: Literal[1] = 1
    frame_ms: Literal[20] = 20


class TranscriptSegment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    segment_id: str
    source: AudioSource
    text: str
    language: str | None = None
    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)
    is_final: bool
    speaker: str | None = None


class AnswerSuggestion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question_id: str
    text: str
    grounding: GroundingLabel
    explanation: str | None = None
