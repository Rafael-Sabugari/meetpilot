"""Anthropic Messages API adapter (POST /v1/messages) for Claude models.

* Structured output via a single forced tool: the tool's `input_schema` is the JSON schema
  and `tool_choice` makes the model call it, so the answer is always a schema-shaped object.
* Default model is the cheapest current one (Claude Haiku 5.5); configurable.
* Error bodies are never logged or surfaced (they may echo prompt content).
"""

import logging
from typing import Any

import httpx

from app.providers.llm.base import LLMError, LLMResult

log = logging.getLogger("meetpilot.llm.anthropic")

API_VERSION = "2023-06-01"


def build_payload(
    *,
    model: str,
    instructions: str,
    input_text: str,
    schema_name: str,
    schema: dict[str, Any],
    max_output_tokens: int,
) -> dict[str, Any]:
    # Prompt caching: the instructions and the User's own context (sent first, stable for a
    # whole meeting) are cached, so repeated suggestions only pay full price for the rest.
    content: list[dict[str, Any]] = []
    marker = "</user_context>\n"
    if input_text.startswith("<user_context>") and marker in input_text:
        head, tail = input_text.split(marker, 1)
        content.append(
            {"type": "text", "text": head + marker, "cache_control": {"type": "ephemeral"}}
        )
        content.append({"type": "text", "text": tail})
    else:
        content.append({"type": "text", "text": input_text})
    return {
        "model": model,
        "max_tokens": max_output_tokens,
        "system": [{"type": "text", "text": instructions, "cache_control": {"type": "ephemeral"}}],
        "messages": [{"role": "user", "content": content}],
        "tools": [
            {
                "name": schema_name,
                "description": "Return the result. Always call this tool exactly once.",
                "input_schema": schema,
            }
        ],
        "tool_choice": {"type": "tool", "name": schema_name},
    }


def parse_response(body: dict[str, Any], schema_name: str) -> tuple[dict[str, Any], int, int]:
    """Extract the tool input and token usage from a Messages API body."""
    stop = body.get("stop_reason")
    if stop == "refusal":
        raise LLMError("the AI declined to answer this request", retryable=False)
    data: dict[str, Any] | None = None
    for block in body.get("content") or []:
        if block.get("type") == "tool_use" and block.get("name") == schema_name:
            inp = block.get("input")
            if isinstance(inp, dict):
                data = inp
    if data is None:
        if stop == "max_tokens":
            raise LLMError("the AI response was incomplete (max_tokens)", retryable=True)
        raise LLMError("the AI returned no answer", retryable=True)
    if stop == "max_tokens":
        # A truncated tool call may be missing required fields.
        raise LLMError("the AI response was incomplete (max_tokens)", retryable=True)
    usage = body.get("usage") or {}
    tin = int(usage.get("input_tokens") or 0)
    tin += int(usage.get("cache_read_input_tokens") or 0)
    tin += int(usage.get("cache_creation_input_tokens") or 0)
    return data, tin, int(usage.get("output_tokens") or 0)


class AnthropicProvider:
    name = "anthropic"

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str = "https://api.anthropic.com/v1",
        timeout: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._model = model
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={"x-api-key": api_key, "anthropic-version": API_VERSION},
            timeout=timeout,
            transport=transport,
        )

    _auto_tool_choice = False

    async def aclose(self) -> None:
        await self._client.aclose()

    async def generate_json(
        self,
        *,
        instructions: str,
        input_text: str,
        schema_name: str,
        schema: dict[str, Any],
        max_output_tokens: int,
    ) -> LLMResult:
        payload = build_payload(
            model=self._model,
            instructions=instructions,
            input_text=input_text,
            schema_name=schema_name,
            schema=schema,
            max_output_tokens=max_output_tokens,
        )
        if self._auto_tool_choice:
            payload["tool_choice"] = {"type": "auto"}
        try:
            resp = await self._client.post("/messages", json=payload)
            if (
                resp.status_code == 400
                and not self._auto_tool_choice
                and "tool_choice" in resp.text
            ):
                # Some models only accept tool_choice "auto"; the system prompt and tool
                # description still require exactly one call to the schema tool.
                self._auto_tool_choice = True
                payload["tool_choice"] = {"type": "auto"}
                resp = await self._client.post("/messages", json=payload)
        except httpx.TimeoutException as e:
            raise LLMError("the AI took too long to answer", retryable=True) from e
        except httpx.HTTPError as e:
            raise LLMError("the AI service is unreachable", retryable=True) from e
        if resp.status_code != 200:
            code = resp.status_code
            log.warning("anthropic request failed", extra={"status": code})
            if code in (401, 403):
                raise LLMError("the AI service rejected the server's credentials", retryable=False)
            if code in (400, 404, 413):
                raise LLMError("the AI service rejected the request", retryable=False)
            raise LLMError(f"the AI service is unavailable (HTTP {code})", retryable=True)
        data, tin, tout = parse_response(resp.json(), schema_name)
        return LLMResult(data=data, input_tokens=tin, output_tokens=tout, model=self._model)
