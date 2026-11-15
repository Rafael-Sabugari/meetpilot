"""Deepgram adapter tests against a local server that speaks Deepgram's live protocol.

These verify our request shape (URL, auth header, binary audio, KeepAlive, CloseStream)
and response parsing. They do not call Deepgram; a real-key smoke test is in
scripts/deepgram_smoke.py.
"""

import asyncio
import json
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
from websockets.asyncio.server import ServerConnection, serve
from websockets.datastructures import Headers
from websockets.http11 import Request, Response

from app.providers.stt.base import STTError
from app.providers.stt.deepgram import DeepgramProvider, build_url, parse_message


def results(text: str, *, final: bool, start: float = 1.0, duration: float = 0.5) -> str:
    return json.dumps(
        {
            "type": "Results",
            "channel_index": [0, 1],
            "duration": duration,
            "start": start,
            "is_final": final,
            "speech_final": final,
            "channel": {"alternatives": [{"transcript": text, "confidence": 0.9, "words": []}]},
            "metadata": {"request_id": "r"},
        }
    )


def test_url_has_streaming_params_and_training_opt_out() -> None:
    url = build_url(
        "wss://api.deepgram.com/v1/listen", model="nova-3", language="pt-BR", mip_opt_out=True
    )
    q = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
    assert q == {
        "model": "nova-3",
        "language": "pt-BR",
        "encoding": "linear16",
        "sample_rate": "16000",
        "channels": "1",
        "interim_results": "true",
        "punctuate": "true",
        "smart_format": "true",
        "endpointing": "300",
        "mip_opt_out": "true",
    }
    no_opt = build_url("wss://x", model="nova-3", language="en", mip_opt_out=False)
    assert "mip_opt_out" not in no_opt


def test_parse_results_messages() -> None:
    seg = parse_message(
        results("Olá, tudo bem?", final=True, start=2.0, duration=1.25), "system", "pt-BR"
    )
    assert seg is not None
    assert (seg.text, seg.is_final, seg.start_ms, seg.end_ms) == (
        "Olá, tudo bem?",
        True,
        2000,
        3250,
    )
    assert seg.source == "system"
    partial = parse_message(results("Olá", final=False), "microphone", "pt-BR")
    assert partial is not None and not partial.is_final
    assert parse_message(results("   ", final=False), "system", "en") is None
    assert (
        parse_message(json.dumps({"type": "Metadata", "request_id": "x"}), "system", "en") is None
    )
    assert (
        parse_message(json.dumps({"type": "UtteranceEnd", "last_word_end": 1}), "system", "en")
        is None
    )
    with pytest.raises(STTError):
        parse_message(
            json.dumps({"type": "Error", "err_code": "BAD", "description": "x"}), "system", "en"
        )


class FakeDeepgram:
    """Records what the client sends; replies like Deepgram."""

    def __init__(self, *, reject_status: int | None = None) -> None:
        self.reject_status = reject_status
        self.path: str | None = None
        self.auth: str | None = None
        self.audio_bytes = 0
        self.control: list[str] = []

    def process_request(self, conn: ServerConnection, request: Request) -> Response | None:
        self.path = request.path
        self.auth = request.headers.get("Authorization")
        if self.reject_status:
            return Response(self.reject_status, "Unauthorized", Headers(), b"")
        return None

    async def handler(self, ws: ServerConnection) -> None:
        async for msg in ws:
            if isinstance(msg, bytes):
                self.audio_bytes += len(msg)
                if self.audio_bytes == 640 * 3:
                    await ws.send(results("Bom dia", final=False))
                continue
            data = json.loads(msg)
            self.control.append(data["type"])
            if data["type"] == "CloseStream":
                await ws.send(results("Bom dia a todos.", final=True))
                await ws.send(json.dumps({"type": "Metadata", "request_id": "r"}))
                await ws.close()
                return


async def _run_session(server: FakeDeepgram) -> tuple[list[Any], STTError | None]:
    async with serve(server.handler, "127.0.0.1", 0, process_request=server.process_request) as srv:
        port = srv.sockets[0].getsockname()[1]
        provider = DeepgramProvider(
            api_key="dg-test-key",
            url=f"ws://127.0.0.1:{port}/v1/listen",
            model="nova-3",
            mip_opt_out=True,
        )
        session = await provider.open(language="pt-BR", source="system")
        segments: list[Any] = []
        error: STTError | None = None

        async def collect() -> None:
            nonlocal error
            try:
                async for seg in session.results():
                    segments.append(seg)
            except STTError as e:
                error = e

        task = asyncio.create_task(collect())
        for _ in range(3):
            await session.send_audio(b"\x00" * 640)
        await session.keepalive()
        await asyncio.sleep(0.05)
        await session.close()
        await asyncio.wait_for(task, 5)
        return segments, error


def test_session_against_fake_deepgram_server() -> None:
    server = FakeDeepgram()
    segments, error = asyncio.run(_run_session(server))
    assert error is None
    assert server.auth == "Token dg-test-key"
    assert server.path is not None and "mip_opt_out=true" in server.path
    assert server.audio_bytes == 640 * 3
    assert server.control == ["KeepAlive", "CloseStream"]
    assert [(s.text, s.is_final) for s in segments] == [
        ("Bom dia", False),
        ("Bom dia a todos.", True),
    ]


def test_rejected_api_key_is_not_retryable() -> None:
    async def go() -> None:
        server = FakeDeepgram(reject_status=401)
        async with serve(
            server.handler, "127.0.0.1", 0, process_request=server.process_request
        ) as srv:
            port = srv.sockets[0].getsockname()[1]
            provider = DeepgramProvider(
                api_key="bad", url=f"ws://127.0.0.1:{port}", model="nova-3", mip_opt_out=True
            )
            with pytest.raises(STTError) as e:
                await provider.open(language="en", source="system")
            assert e.value.retryable is False
            assert "401" in str(e.value)

    asyncio.run(go())


def test_unexpected_disconnect_surfaces_retryable_error() -> None:
    async def go() -> None:
        async def handler(ws: ServerConnection) -> None:
            await ws.recv()
            await ws.close(1011, "boom")

        async with serve(handler, "127.0.0.1", 0) as srv:
            port = srv.sockets[0].getsockname()[1]
            provider = DeepgramProvider(
                api_key="k", url=f"ws://127.0.0.1:{port}", model="nova-3", mip_opt_out=True
            )
            session = await provider.open(language="en", source="system")
            await session.send_audio(b"\x00" * 640)
            with pytest.raises(STTError) as e:
                async for _ in session.results():
                    pass
            assert e.value.retryable

    asyncio.run(go())
