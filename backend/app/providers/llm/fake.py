"""Deterministic local LLM for development and tests. Clearly labelled; not real AI."""

from typing import Any

from app.providers.llm.base import LLMError, LLMResult


class FakeLLMProvider:
    name = "fake"

    def __init__(self, *, fail: LLMError | None = None) -> None:
        self.fail = fail
        self.calls: list[dict[str, Any]] = []

    async def generate_json(
        self,
        *,
        instructions: str,
        input_text: str,
        schema_name: str,
        schema: dict[str, Any],
        max_output_tokens: int,
    ) -> LLMResult:
        self.calls.append(
            {"instructions": instructions, "input_text": input_text, "schema_name": schema_name}
        )
        if self.fail:
            raise self.fail
        if schema_name == "translation":
            return LLMResult(
                data={"translation": f"(tradução de teste) {_strip_line_tags(input_text)}"},
                input_tokens=max(1, len(input_text) // 4),
                output_tokens=max(1, len(input_text) // 4),
                model="fake",
            )
        has_transcript = "<transcript>\n(empty)" not in input_text
        question = input_text.split("<question>\n", 1)[-1].split("\n</question>", 1)[0]
        return LLMResult(
            data={
                "answer": f"(fake LLM) {question[:120]}",
                "grounding": "from_conversation" if has_transcript else "general_suggestion",
                "explanation": "Development provider: placeholder answer, not real AI.",
                "missing_info": [],
            },
            input_tokens=max(1, (len(instructions) + len(input_text)) // 4),
            output_tokens=40,
            model="fake",
        )


def _strip_line_tags(text: str) -> str:
    return text.removeprefix("<line>").removesuffix("</line>")
