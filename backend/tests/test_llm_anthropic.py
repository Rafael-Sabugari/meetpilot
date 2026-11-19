"""Claude (Anthropic Messages API) adapter: request shape, parsing and error handling."""

import asyncio
import json
from typing import Any

import httpx
import pytest

from app.ai.copilot import SCHEMA
from app.core.config import Settings
from app.providers.llm.anthropic import AnthropicProvider, parse_response
from app.providers.llm.base import LLMError
from app.providers.llm.factory import make_llm_provider

ANSWER = {
    "answer": "A entrega está prevista para sexta.",
    "grounding": "from_conversation",
    "explanation": "",
    "missing_info": [],
}


def body(data: dict[str, Any] | None = ANSWER, stop: str = "tool_use") -> dict[str, Any]:
    content: list[dict[str, Any]] = [{"type": "text", "text": "ok"}]
    if data is not None:
        content.append({"type": "tool_use", "id": "t1", "name": "copilot_answer", "input": data})
    return {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "model": "claude-haiku-5-5",
        "content": content,
        "stop_reason": stop,
        "usage": {"input_tokens": 700, "cache_read_input_tokens": 12, "output_tokens": 90},
    }


def run(provider: AnthropicProvider, max_tokens: int = 500) -> Any:
    return asyncio.run(
        provider.generate_json(
            instructions="RULES",
            input_text="TRANSCRIPT",
            schema_name="copilot_answer",
            schema=SCHEMA,
            max_output_tokens=max_tokens,
        )
    )


def test_request_shape_forces_the_schema_tool_and_parses_usage() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["key"] = request.headers["x-api-key"]
        seen["version"] = request.headers["anthropic-version"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=body())

    p = AnthropicProvider(
        api_key="sk-ant-test", model="claude-haiku-5-5", transport=httpx.MockTransport(handler)
    )
    result = run(p)
    assert seen["url"] == "https://api.anthropic.com/v1/messages"
    assert seen["key"] == "sk-ant-test"
    assert seen["version"] == "2023-06-01"
    b = seen["body"]
    assert b["model"] == "claude-haiku-5-5"
    assert b["max_tokens"] == 500
    eph = {"type": "ephemeral"}
    assert b["system"] == [{"type": "text", "text": "RULES", "cache_control": eph}]
    assert b["messages"] == [{"role": "user", "content": [{"type": "text", "text": "TRANSCRIPT"}]}]
    assert b["tools"][0]["name"] == "copilot_answer"
    assert b["tools"][0]["input_schema"] == SCHEMA
    assert b["tool_choice"] == {"type": "tool", "name": "copilot_answer"}
    assert result.data == ANSWER
    assert (result.input_tokens, result.output_tokens) == (712, 90)
    assert result.model == "claude-haiku-5-5"


@pytest.mark.parametrize(
    ("status", "retryable"),
    [(401, False), (403, False), (400, False), (429, True), (500, True), (529, True)],
)
def test_http_errors_never_surface_provider_bodies(status: int, retryable: bool) -> None:
    p = AnthropicProvider(
        api_key="k",
        model="m",
        transport=httpx.MockTransport(
            lambda r: httpx.Response(status, json={"error": {"message": "secret prompt echo"}})
        ),
    )
    with pytest.raises(LLMError) as e:
        run(p)
    assert e.value.retryable is retryable
    assert "secret prompt echo" not in str(e.value)


def test_refusal_truncation_and_missing_tool_call() -> None:
    with pytest.raises(LLMError) as e:
        parse_response(body(None, stop="refusal"), "copilot_answer")
    assert e.value.retryable is False
    with pytest.raises(LLMError, match="incomplete"):
        parse_response(body(ANSWER, stop="max_tokens"), "copilot_answer")
    with pytest.raises(LLMError, match="no answer"):
        parse_response(body(None, stop="end_turn"), "copilot_answer")
    # A tool call with another name is not accepted.
    with pytest.raises(LLMError, match="no answer"):
        parse_response(body(), "translation")


def test_timeout_is_retryable() -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(LLMError) as e:
        run(AnthropicProvider(api_key="k", model="m", transport=httpx.MockTransport(boom)))
    assert e.value.retryable is True


def test_factory_defaults_to_the_cheapest_claude_model() -> None:
    s = Settings(env="test", jwt_secret="k" * 40, llm_provider="anthropic", anthropic_api_key="x")
    p = make_llm_provider(s)
    assert isinstance(p, AnthropicProvider)
    assert s.anthropic_model == "claude-haiku-5-5"
    with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
        make_llm_provider(Settings(env="test", jwt_secret="k" * 40, llm_provider="anthropic"))


def test_the_users_own_context_is_cached_separately_from_the_transcript() -> None:
    from app.providers.llm.anthropic import build_payload

    text = "<user_context>\nSou dev Salesforce.\n</user_context>\n<transcript>\nOi\n</transcript>"
    b = build_payload(
        model="m",
        instructions="R",
        input_text=text,
        schema_name="s",
        schema={"type": "object"},
        max_output_tokens=10,
    )
    blocks = b["messages"][0]["content"]
    assert blocks[0] == {
        "type": "text",
        "text": "<user_context>\nSou dev Salesforce.\n</user_context>\n",
        "cache_control": {"type": "ephemeral"},
    }
    assert blocks[1] == {"type": "text", "text": "<transcript>\nOi\n</transcript>"}


def test_models_without_forced_tool_choice_fall_back_to_auto() -> None:
    choices: list[Any] = []

    def handler(request: httpx.Request) -> httpx.Response:
        choice = json.loads(request.content)["tool_choice"]
        choices.append(choice)
        if choice["type"] == "tool":
            msg = 'tool_choice: type "tool" and "any" are not supported for this model.'
            return httpx.Response(
                400, json={"error": {"type": "invalid_request_error", "message": msg}}
            )
        return httpx.Response(200, json=body())

    p = AnthropicProvider(
        api_key="sk-ant-test", model="claude-sonnet-5-5", transport=httpx.MockTransport(handler)
    )
    assert run(p).data == ANSWER
    assert run(p).data == ANSWER  # remembered: no second rejected call
    assert [c["type"] for c in choices] == ["tool", "auto", "auto"]
