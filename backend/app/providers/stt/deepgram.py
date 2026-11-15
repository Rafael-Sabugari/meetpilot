"""Deepgram live streaming adapter (wss /v1/listen, Nova-3).

Protocol (verified against developers.deepgram.com, Nov 2026):
* Auth header `Authorization: Token <key>`; query parameters configure model/format.
* Client sends binary audio, `{"type":"KeepAlive"}` while idle, `{"type":"CloseStream"}` to
  flush and finish. Server sends `Results` (with `is_final`, `speech_final`), `Metadata`,
  `UtteranceEnd`, `SpeechStarted`.
* `mip_opt_out=true` excludes requests from Deepgram's model-improvement program; opted-out
  data is retained only as long as needed to process the request.
"""

import asyncio
import contextlib
import json
import logging
from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import urlencode

from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed, InvalidStatus

from app.providers.stt.base import STTError
from app.realtime.events import AudioSource, TranscriptSegment

log = logging.getLogger("meetpilot.stt.deepgram")

LANGUAGES = {"pt-BR": "pt-BR", "en": "en", "multi": "multi"}


def build_url(base_url: str, *, model: str, language: str, mip_opt_out: bool) -> str:
    params = {
        "model": model,
        "language": LANGUAGES.get(language, language),
        "encoding": "linear16",
        "sample_rate": "16000",
        "channels": "1",
        "interim_results": "true",
        "punctuate": "true",
        "smart_format": "true",
        "endpointing": "300",
    }
    if mip_opt_out:
        params["mip_opt_out"] = "true"
    return f"{base_url}?{urlencode(params)}"


def parse_message(raw: str, source: AudioSource, language: str) -> TranscriptSegment | None:
    """Convert a Deepgram server message into a segment; None for non-transcript messages."""
    msg: dict[str, Any] = json.loads(raw)
    if msg.get("type") != "Results":
        if msg.get("type") == "Error" or "err_code" in msg:
            raise STTError(f"provider error: {msg.get('err_code') or msg.get('description')}")
        return None
    alts = (msg.get("channel") or {}).get("alternatives") or []
    text = (alts[0].get("transcript") if alts else "") or ""
    if not text.strip():
        return None
    start = float(msg.get("start") or 0.0)
    duration = float(msg.get("duration") or 0.0)
    return TranscriptSegment(
        segment_id=f"{source}-{start:.3f}",
        source=source,
        text=text,
        language=language,
        start_ms=round(start * 1000),
        end_ms=round((start + duration) * 1000),
        is_final=bool(msg.get("is_final")),
    )


class DeepgramSession:
    def __init__(self, ws: ClientConnection, source: AudioSource, language: str) -> None:
        self._ws = ws
        self._source = source
        self._language = language
        self._queue: asyncio.Queue[TranscriptSegment | STTError | None] = asyncio.Queue()
        self._reader = asyncio.create_task(self._read())
        self._closing = False

    async def _read(self) -> None:
        try:
            async for raw in self._ws:
                if isinstance(raw, bytes):
                    continue
                try:
                    seg = parse_message(raw, self._source, self._language)
                except STTError as e:
                    await self._queue.put(e)
                    continue
                if seg is not None:
                    await self._queue.put(seg)
        except ConnectionClosed as e:
            if not self._closing:
                code = e.rcvd.code if e.rcvd else None
                log.warning("deepgram connection closed unexpectedly", extra={"code": code})
                await self._queue.put(STTError("speech-to-text connection lost"))
        finally:
            await self._queue.put(None)

    async def send_audio(self, pcm: bytes) -> None:
        try:
            await self._ws.send(pcm)
        except ConnectionClosed as e:
            raise STTError("speech-to-text connection lost") from e

    async def keepalive(self) -> None:
        with contextlib.suppress(ConnectionClosed):
            await self._ws.send(json.dumps({"type": "KeepAlive"}))

    async def results(self) -> AsyncIterator[TranscriptSegment]:
        while True:
            item = await self._queue.get()
            if item is None:
                return
            if isinstance(item, STTError):
                raise item
            yield item

    async def close(self) -> None:
        if self._closing:
            return
        self._closing = True
        with contextlib.suppress(ConnectionClosed):
            await self._ws.send(json.dumps({"type": "CloseStream"}))
        # Deepgram sends remaining finals, then closes the socket.
        try:
            await asyncio.wait_for(asyncio.shield(self._reader), timeout=5)
        except TimeoutError:
            pass
        finally:
            await self._ws.close()


class DeepgramProvider:
    name = "deepgram"

    def __init__(self, *, api_key: str, url: str, model: str, mip_opt_out: bool) -> None:
        self._key = api_key
        self._url = url
        self._model = model
        self._mip_opt_out = mip_opt_out

    async def open(self, *, language: str, source: AudioSource) -> DeepgramSession:
        url = build_url(
            self._url, model=self._model, language=language, mip_opt_out=self._mip_opt_out
        )
        try:
            ws = await connect(
                url,
                additional_headers={"Authorization": f"Token {self._key}"},
                open_timeout=10,
                max_size=2**20,
            )
        except InvalidStatus as e:
            status = e.response.status_code
            raise STTError(
                f"speech-to-text provider rejected the connection (HTTP {status})",
                retryable=status >= 500 or status == 429,
            ) from None
        except (OSError, TimeoutError) as e:
            raise STTError("speech-to-text provider unreachable") from e
        return DeepgramSession(ws, source, language)
