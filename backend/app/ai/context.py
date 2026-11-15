"""Short-term meeting context for the copilot.

The desktop sends the recent transcript it already shows; the server never stores it. We
keep the most recent segments that fit the character budget (≈4 chars per token), so the
cost of a request is bounded no matter how long the meeting is.
"""

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field

Source = Literal["system", "microphone"]


class ContextSegment(BaseModel):
    source: Source
    text: str = Field(max_length=2_000)
    start_ms: int = Field(ge=0)


@dataclass(frozen=True)
class BuiltContext:
    text: str
    used_segments: int
    total_segments: int

    @property
    def truncated(self) -> bool:
        return self.used_segments < self.total_segments


def _clock(ms: int) -> str:
    s = ms // 1000
    return f"{s // 60:02d}:{s % 60:02d}"


def build_context(segments: list[ContextSegment], budget_chars: int) -> BuiltContext:
    """Most recent segments first until the budget is used; returned in chronological order."""
    lines: list[str] = []
    used = 0
    for seg in reversed(segments):
        text = " ".join(seg.text.split())
        if not text:
            continue
        who = "User" if seg.source == "microphone" else "Others"
        line = f"[{_clock(seg.start_ms)}] {who}: {text}"
        if used + len(line) + 1 > budget_chars:
            break
        lines.append(line)
        used += len(line) + 1
    lines.reverse()
    return BuiltContext(
        text="\n".join(lines),
        used_segments=len(lines),
        total_segments=sum(1 for s in segments if s.text.strip()),
    )
