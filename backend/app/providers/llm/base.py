"""LLM provider abstraction. Business logic depends only on this protocol; vendor keys stay
server-side. Prompts, transcripts and answers are never logged."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol

# Called with the text generated so far for the streamed field (the whole prefix each time).
PartialCallback = Callable[[str], Awaitable[None]]


class LLMError(Exception):
    """Provider failure. Messages are user-safe and never include prompt content."""

    def __init__(self, message: str, *, retryable: bool = True) -> None:
        super().__init__(message)
        self.retryable = retryable


@dataclass(frozen=True)
class LLMResult:
    data: dict[str, Any]
    input_tokens: int
    output_tokens: int
    model: str


class LLMProvider(Protocol):
    name: str

    async def generate_json(
        self,
        *,
        instructions: str,
        input_text: str,
        schema_name: str,
        schema: dict[str, Any],
        max_output_tokens: int,
        on_partial: PartialCallback | None = None,
    ) -> LLMResult:
        """Return an object that conforms to `schema` (strict JSON schema).

        With `on_partial`, providers that can stream report the "answer" field as it is
        generated; others may call it once at the end or not at all."""
        ...
