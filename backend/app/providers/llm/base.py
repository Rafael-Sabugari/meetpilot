"""LLM provider abstraction. Business logic depends only on this protocol; vendor keys stay
server-side. Prompts, transcripts and answers are never logged."""

from dataclasses import dataclass
from typing import Any, Protocol


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
    ) -> LLMResult:
        """Return an object that conforms to `schema` (strict JSON schema)."""
        ...
