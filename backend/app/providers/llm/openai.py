"""OpenAI Responses API adapter (POST /v1/responses).

* Structured output via `text.format = {type: json_schema, strict: true}`.
* `store: false`: OpenAI is asked not to retain the response for later retrieval.
* Reasoning effort is configurable (GPT-5 family); low keeps latency down for live use.
"""

import json
import logging
from typing import Any

import httpx

from app.providers.llm.base import LLMError, LLMResult, PartialCallback

log = logging.getLogger("meetpilot.llm.openai")


def parse_response(body: dict[str, Any]) -> tuple[dict[str, Any], int, int]:
    """Extract the JSON object and token usage from a Responses API body."""
    status = body.get("status")
    if status == "incomplete":
        reason = (body.get("incomplete_details") or {}).get("reason", "unknown")
        raise LLMError(f"the AI response was incomplete ({reason})", retryable=True)
    if status not in (None, "completed"):
        raise LLMError(f"the AI response failed ({status})", retryable=True)
    text: str | None = None
    for item in body.get("output") or []:
        if item.get("type") != "message":
            continue
        for part in item.get("content") or []:
            if part.get("type") == "refusal":
                raise LLMError("the AI declined to answer this request", retryable=False)
            if part.get("type") == "output_text":
                text = part.get("text")
    if not text:
        raise LLMError("the AI returned no answer", retryable=True)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise LLMError("the AI returned an invalid answer", retryable=True) from e
    if not isinstance(data, dict):
        raise LLMError("the AI returned an invalid answer", retryable=True)
    usage = body.get("usage") or {}
    return data, int(usage.get("input_tokens") or 0), int(usage.get("output_tokens") or 0)


class OpenAIProvider:
    name = "openai"

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str = "https://api.openai.com/v1",
        reasoning_effort: str = "low",
        timeout: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._model = model
        self._effort = reasoning_effort
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout,
            transport=transport,
        )

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
        payload: dict[str, Any] = {
            "model": self._model,
            "instructions": instructions,
            "input": input_text,
            "store": False,
            "max_output_tokens": max_output_tokens,
            "reasoning": {"effort": self._effort},
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": schema_name,
                    "strict": True,
                    "schema": schema,
                }
            },
        }
        try:
            resp = await self._client.post("/responses", json=payload)
        except httpx.TimeoutException as e:
            raise LLMError("the AI took too long to answer", retryable=True) from e
        except httpx.HTTPError as e:
            raise LLMError("the AI service is unreachable", retryable=True) from e
        if resp.status_code != 200:
            code = resp.status_code
            # Log status only; bodies may echo prompt content.
            log.warning("openai request failed", extra={"status": code})
            if code in (401, 403):
                raise LLMError("the AI service rejected the server's credentials", retryable=False)
            if code == 400:
                raise LLMError("the AI service rejected the request", retryable=False)
            raise LLMError(f"the AI service is unavailable (HTTP {code})", retryable=True)
        data, tin, tout = parse_response(resp.json())
        return LLMResult(data=data, input_tokens=tin, output_tokens=tout, model=self._model)
