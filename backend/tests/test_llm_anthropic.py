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


def test_partial_string_field_reads_an_unfinished_json_string() -> None:
    from app.providers.llm.anthropic import partial_string_field

    assert partial_string_field('{"answ', "answer") is None
    assert partial_string_field('{"answer": ', "answer") is None
    assert partial_string_field('{"answer": "First I', "answer") == "First I"
    assert partial_string_field('{"answer": "Say \\"hi\\"\\nthen', "answer") == 'Say "hi"\nthen'
    assert partial_string_field('{"answer": "caf\\u00e9 done", "g', "answer") == "café done"
    assert partial_string_field('{"answer": "cut \\u00', "answer") == "cut "


def test_streaming_reports_the_answer_while_it_is_written() -> None:
    full = json.dumps(ANSWER)
    chunks = [full[i : i + 7] for i in range(0, len(full), 7)]
    events: list[dict[str, Any]] = [
        {
            "type": "message_start",
            "message": {"usage": {"input_tokens": 10, "cache_read_input_tokens": 5}},
        },
        {
            "type": "content_block_start",
            "index": 0,
            "content_block": {"type": "tool_use", "name": "copilot_answer"},
        },
        *[
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "input_json_delta", "partial_json": c},
            }
            for c in chunks
        ],
        {"type": "content_block_stop", "index": 0},
        {
            "type": "message_delta",
            "delta": {"stop_reason": "tool_use"},
            "usage": {"output_tokens": 42},
        },
        {"type": "message_stop"},
    ]
    sse = "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events)
    seen: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, text=sse, headers={"content-type": "text/event-stream"})

    p = AnthropicProvider(api_key="sk-ant-test", model="m", transport=httpx.MockTransport(handler))
    partials: list[str] = []

    async def on_partial(text: str) -> None:
        partials.append(text)

    res = asyncio.run(
        p.generate_json(
            instructions="R",
            input_text="T",
            schema_name="copilot_answer",
            schema={"type": "object"},
            max_output_tokens=100,
            on_partial=on_partial,
        )
    )
    assert seen[0]["stream"] is True
    assert res.data == ANSWER
    assert (res.input_tokens, res.output_tokens) == (15, 42)
    assert partials and all(str(ANSWER["answer"]).startswith(x) for x in partials)
    assert len(partials) >= 2
