"""Anthropic Messages API adapter (POST /v1/messages) for Claude models.

* Structured output via a single forced tool: the tool's `input_schema` is the JSON schema
  and `tool_choice` makes the model call it, so the answer is always a schema-shaped object.
* Default model is the cheapest current one (Claude Haiku 5.5); configurable.
* Error bodies are never logged or surfaced (they may echo prompt content).
"""

import json
import logging
from typing import Any

import httpx

from app.providers.llm.base import LLMError, LLMResult, PartialCallback

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


_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "b": "\b", "f": "\f", '"': '"', "\\": "\\", "/": "/"}


def partial_string_field(partial_json: str, field: str) -> str | None:
    """The value generated so far for a top-level string `field` in an incomplete JSON
    object (as streamed in tool input deltas), or None while it has not started."""
    key = f'"{field}"'
    i = partial_json.find(key)
    if i < 0:
        return None
    i += len(key)
    n = len(partial_json)
    while i < n and partial_json[i] in " \t\r\n":
        i += 1
    if i >= n or partial_json[i] != ":":
        return None
    i += 1
    while i < n and partial_json[i] in " \t\r\n":
        i += 1
    if i >= n or partial_json[i] != '"':
        return None
    i += 1
    out: list[str] = []
    while i < n:
        c = partial_json[i]
        if c == '"':
            break
        if c == "\\":
            if i + 1 >= n:
                break
            e = partial_json[i + 1]
            if e == "u":
                if i + 6 > n:
                    break
                try:
                    out.append(chr(int(partial_json[i + 2 : i + 6], 16)))
                except ValueError:
                    break
                i += 6
                continue
            out.append(_ESCAPES.get(e, e))
            i += 2
            continue
        out.append(c)
        i += 1
    return "".join(out)


def _usage_in(usage: dict[str, Any]) -> int:
    return (
        int(usage.get("input_tokens") or 0)
        + int(usage.get("cache_read_input_tokens") or 0)
        + int(usage.get("cache_creation_input_tokens") or 0)
    )


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
    return data, _usage_in(usage), int(usage.get("output_tokens") or 0)


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

    async def _stream(
        self, payload: dict[str, Any], schema_name: str, on_partial: PartialCallback
    ) -> tuple[dict[str, Any], int, int]:
        """Server-sent events: report the "answer" field while the tool input streams in."""
        # Fine-grained tool streaming: without it the tool input arrives in a few big
        # buffered chunks, so the text would show up only near the end.
        body = {
            **payload,
            "stream": True,
            "tools": [{**t, "eager_input_streaming": True} for t in payload["tools"]],
        }
        headers = {"anthropic-beta": "fine-grained-tool-streaming-2025-05-14"}
        for attempt in (0, 1, 2):
            buf, tin, tout, in_tool, last = "", 0, 0, False, ""
            stop: str | None = None
            try:
                async with self._client.stream(
                    "POST", "/messages", json=body, headers=headers
                ) as resp:
                    if resp.status_code != 200:
                        text = (await resp.aread()).decode("utf-8", "replace")
                        if attempt < 2 and resp.status_code == 400:
                            if not self._auto_tool_choice and "tool_choice" in text:
                                self._auto_tool_choice = True
                                body["tool_choice"] = {"type": "auto"}
                                continue
                            if "eager_input_streaming" in text or "anthropic-beta" in text:
                                body["tools"] = payload["tools"]
                                headers = {}
                                continue
                        _raise_for_status(resp.status_code)
                    async for line in resp.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        try:
                            ev = json.loads(line[5:])
                        except ValueError:
                            continue
                        kind = ev.get("type")
                        if kind == "message_start":
                            tin = _usage_in((ev.get("message") or {}).get("usage") or {})
                        elif kind == "content_block_start":
                            block = ev.get("content_block") or {}
                            in_tool = (
                                block.get("type") == "tool_use" and block.get("name") == schema_name
                            )
                        elif kind == "content_block_delta" and in_tool:
                            delta = ev.get("delta") or {}
                            if delta.get("type") == "input_json_delta":
                                buf += delta.get("partial_json") or ""
                                text = partial_string_field(buf, "answer") or ""
                                # Report every ~3 words, not every token.
                                if text and len(text) - len(last) >= 16:
                                    last = text
                                    await on_partial(text)
                        elif kind == "content_block_stop":
                            in_tool = False
                        elif kind == "message_delta":
                            stop = (ev.get("delta") or {}).get("stop_reason") or stop
                            tout = int((ev.get("usage") or {}).get("output_tokens") or tout)
                        elif kind == "error":
                            raise LLMError("the AI service is unavailable", retryable=True)
            except httpx.TimeoutException as e:
                raise LLMError("the AI took too long to answer", retryable=True) from e
            except httpx.HTTPError as e:
                raise LLMError("the AI service is unreachable", retryable=True) from e
            if stop == "refusal":
                raise LLMError("the AI declined to answer this request", retryable=False)
            if stop == "max_tokens":
                raise LLMError("the AI response was incomplete (max_tokens)", retryable=True)
            try:
                data = json.loads(buf) if buf else None
            except ValueError:
                data = None
            if not isinstance(data, dict):
                raise LLMError("the AI returned no answer", retryable=True)
            return data, tin, tout
        raise LLMError("the AI service rejected the request", retryable=False)

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
        on_partial: PartialCallback | None = None,
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
        if on_partial is not None:
            data, tin, tout = await self._stream(payload, schema_name, on_partial)
            return LLMResult(data=data, input_tokens=tin, output_tokens=tout, model=self._model)
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
            _raise_for_status(resp.status_code)
        data, tin, tout = parse_response(resp.json(), schema_name)
        return LLMResult(data=data, input_tokens=tin, output_tokens=tout, model=self._model)


def _raise_for_status(code: int) -> None:
    log.warning("anthropic request failed", extra={"status": code})
    if code in (401, 403):
        raise LLMError("the AI service rejected the server's credentials", retryable=False)
    if code in (400, 404, 413):
        raise LLMError("the AI service rejected the request", retryable=False)
    raise LLMError(f"the AI service is unavailable (HTTP {code})", retryable=True)
