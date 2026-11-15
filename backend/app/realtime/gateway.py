"""Authenticated realtime gateway: desktop audio frames in, transcript events out.

Wire protocol (docs/API.md §3):
* First client message (text, within 5 s): {"type": "auth", "token": "<stream token>"}.
* Binary audio frames: [u8 source 0=system 1=microphone][u32 LE seq][640 B PCM s16le 16 kHz].
* Text control: {"type": "capture.paused" | "capture.resumed" | "capture.stopped"}.
* Server events: versioned `Envelope` JSON.

Audio and transcript text are never logged. Usage is metered from frames actually received.
"""

import asyncio
import contextlib
import json
import logging
import struct
import time
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.auth.security import TokenError, TokenService
from app.core.config import Settings
from app.db.models import Meeting, UsageRecord, User
from app.providers.stt.base import StreamingSTTProvider, StreamingSTTSession, STTError
from app.realtime.events import AudioFormat, AudioSource, Envelope, EventType
from app.usage.service import summary

log = logging.getLogger("meetpilot.realtime")
router = APIRouter(tags=["realtime"])

AUTH_TIMEOUT_S = 5.0
KEEPALIVE_EVERY_S = 4.0
USAGE_EVERY_S = 10.0
SEND_AUDIO_TIMEOUT_S = 2.0
FRAME_HEADER = struct.Struct("<BI")
PCM_BYTES = AudioFormat().sample_rate * AudioFormat().frame_ms // 1000 * 2  # 640
FRAME_BYTES = FRAME_HEADER.size + PCM_BYTES
FRAME_MS = AudioFormat().frame_ms
SOURCES: dict[int, AudioSource] = {0: "system", 1: "microphone"}
SEQ_COLUMNS: tuple[tuple[AudioSource, str], ...] = (
    ("system", "last_seq_system"),
    ("microphone", "last_seq_microphone"),
)


class Close:
    UNAUTHORIZED = 4401
    QUOTA = 4402
    BAD_FRAME = 4400
    NOT_FOUND = 4404
    CONFLICT = 4409
    TRY_AGAIN = 1013
    PROVIDER = 1011


class ActiveStreams:
    """Per-user concurrency guard and per-meeting last-seen frame sequence (single process;
    moves to Redis when scaling out)."""

    def __init__(self) -> None:
        self._counts: dict[str, int] = {}
        self.last_seq: dict[tuple[str, AudioSource], int] = {}

    def acquire(self, user_id: str, limit: int) -> bool:
        if self._counts.get(user_id, 0) >= limit:
            return False
        self._counts[user_id] = self._counts.get(user_id, 0) + 1
        return True

    def release(self, user_id: str) -> None:
        n = self._counts.get(user_id, 0) - 1
        if n <= 0:
            self._counts.pop(user_id, None)
        else:
            self._counts[user_id] = n


def parse_frame(data: bytes) -> tuple[AudioSource, int, bytes]:
    if len(data) != FRAME_BYTES:
        raise ValueError(f"frame must be {FRAME_BYTES} bytes")
    source_id, seq = FRAME_HEADER.unpack_from(data)
    if source_id not in SOURCES:
        raise ValueError("unknown audio source")
    return SOURCES[source_id], seq, data[FRAME_HEADER.size :]


class StreamSession:
    def __init__(
        self,
        ws: WebSocket,
        *,
        meeting: Meeting,
        user: User,
        provider: StreamingSTTProvider,
        remaining_seconds: float,
        last_seq: dict[tuple[str, AudioSource], int] | None = None,
    ) -> None:
        self.ws = ws
        # Shared across reconnects of the same meeting: replayed frames are dropped.
        self.last_seq = last_seq if last_seq is not None else {}
        self.duplicates = 0
        self.meeting = meeting
        self.user = user
        self.provider = provider
        # Metering in integer frames avoids float drift at quota boundaries.
        self.remaining_frames = int(remaining_seconds * 1000) // FRAME_MS
        self.seq = 0
        self.paused = False
        self.frames: dict[AudioSource, int] = {"system": 0, "microphone": 0}
        self.stt: dict[AudioSource, StreamingSTTSession] = {}
        self.forwarders: list[asyncio.Task[None]] = []
        # Provider timestamps are relative to each STT connection; offset them by the audio
        # position (frame seq) where that connection started, so times stay meeting-relative
        # across reconnects and server restarts.
        self.base_ms: dict[AudioSource, int] = {}
        self.last_audio: dict[AudioSource, float] = {}
        self.send_lock = asyncio.Lock()
        self.close_code = 1000
        self.close_reason = ""

    @property
    def used_frames(self) -> int:
        return sum(self.frames.values())

    @property
    def used(self) -> float:
        return self.used_frames * FRAME_MS / 1000

    @property
    def remaining(self) -> float:
        return self.remaining_frames * FRAME_MS / 1000

    def seconds(self, source: AudioSource) -> float:
        return self.frames[source] * FRAME_MS / 1000

    async def emit(self, type_: EventType, payload: dict[str, Any] | None = None) -> None:
        env = Envelope(type=type_, meeting_id=self.meeting.id, seq=self.seq, payload=payload or {})
        self.seq += 1
        async with self.send_lock:
            with contextlib.suppress(WebSocketDisconnect, RuntimeError):
                await self.ws.send_text(env.model_dump_json())

    async def error(self, code: str, message: str, *, retryable: bool) -> None:
        await self.emit(EventType.ERROR, {"code": code, "message": message, "retryable": retryable})

    async def stt_for(self, source: AudioSource, seq: int) -> StreamingSTTSession:
        session = self.stt.get(source)
        if session is None:
            session = await self.provider.open(language=self.meeting.language, source=source)
            self.stt[source] = session
            self.base_ms[source] = seq * FRAME_MS
            self.forwarders.append(asyncio.create_task(self.forward(source, session)))
        return session

    async def forward(self, source: AudioSource, session: StreamingSTTSession) -> None:
        try:
            async for seg in session.results():
                kind = EventType.TRANSCRIPT_FINAL if seg.is_final else EventType.TRANSCRIPT_PARTIAL
                base = self.base_ms.get(source, 0)
                shifted = seg.model_copy(
                    update={"start_ms": seg.start_ms + base, "end_ms": seg.end_ms + base}
                )
                await self.emit(kind, shifted.model_dump())
        except STTError as e:
            log.warning("stt session failed", extra={"source": source, "retryable": e.retryable})
            await self.error("stt_error", str(e), retryable=e.retryable)
            self.stt.pop(source, None)  # reopened on the next frame

    async def handle_audio(self, data: bytes) -> bool:
        try:
            source, seq, pcm = parse_frame(data)
        except ValueError as e:
            self.close_code, self.close_reason = Close.BAD_FRAME, str(e)
            return False
        if self.paused:
            return True  # client should not send while paused; never forward
        key = (self.meeting.id, source)
        if seq <= self.last_seq.get(key, -1):
            self.duplicates += 1  # replay after reconnect: already transcribed and billed
            return True
        if self.used_frames + 1 > self.remaining_frames:
            await self.error("quota_exceeded", "monthly audio minutes exhausted", retryable=False)
            self.close_code, self.close_reason = Close.QUOTA, "quota exceeded"
            return False
        try:
            session = await self.stt_for(source, seq)
            await asyncio.wait_for(session.send_audio(pcm), SEND_AUDIO_TIMEOUT_S)
        except TimeoutError:
            await self.error("backpressure", "speech-to-text is not keeping up", retryable=True)
            self.close_code, self.close_reason = Close.TRY_AGAIN, "backpressure"
            return False
        except STTError as e:
            await self.error("stt_error", str(e), retryable=e.retryable)
            self.stt.pop(source, None)
            if not e.retryable:
                self.close_code, self.close_reason = Close.PROVIDER, "provider error"
                return False
            return True
        self.frames[source] += 1
        self.last_seq[key] = seq
        self.last_audio[source] = time.monotonic()
        return True

    async def handle_control(self, text: str) -> bool:
        try:
            msg = json.loads(text)
        except json.JSONDecodeError:
            return True
        kind = msg.get("type")
        if kind == EventType.CAPTURE_PAUSED:
            self.paused = True
            await self.emit(EventType.CAPTURE_PAUSED)
        elif kind == EventType.CAPTURE_RESUMED:
            self.paused = False
            await self.emit(EventType.CAPTURE_RESUMED)
        elif kind == EventType.CAPTURE_STOPPED:
            return False
        return True

    async def background(self) -> None:
        last_usage = time.monotonic()
        while True:
            await asyncio.sleep(1.0)
            now = time.monotonic()
            for source, session in list(self.stt.items()):
                if self.paused or now - self.last_audio.get(source, 0) >= KEEPALIVE_EVERY_S:
                    await session.keepalive()
            if now - last_usage >= USAGE_EVERY_S:
                last_usage = now
                await self.usage_event()

    async def usage_event(self) -> None:
        await self.emit(
            EventType.USAGE_UPDATED,
            {
                "session_seconds": round(self.used, 2),
                "remaining_seconds": round(max(0.0, self.remaining - self.used), 2),
            },
        )

    async def run(self) -> None:
        await self.emit(
            EventType.CAPTURE_STARTED,
            {
                "audio_format": AudioFormat().model_dump(),
                "provider": self.provider.name,
                "remaining_seconds": round(self.remaining, 2),
            },
        )
        bg = asyncio.create_task(self.background())
        try:
            while True:
                msg = await self.ws.receive()
                if msg["type"] == "websocket.disconnect":
                    break
                if msg.get("bytes") is not None:
                    if not await self.handle_audio(msg["bytes"]):
                        break
                elif msg.get("text") is not None and not await self.handle_control(msg["text"]):
                    break
        except WebSocketDisconnect:
            pass
        finally:
            bg.cancel()
            # Flush providers so final segments reach the client before we close.
            for session in list(self.stt.values()):
                with contextlib.suppress(Exception):
                    await session.close()
            if self.forwarders:
                await asyncio.wait(self.forwarders, timeout=6)
            await self.usage_event()
            await self.emit(EventType.CAPTURE_STOPPED, {"session_seconds": round(self.used, 2)})


async def _persist_usage(
    sm: async_sessionmaker[AsyncSession], s: StreamSession, provider: str
) -> None:
    async with sm() as db:
        for source in s.frames:
            secs = s.seconds(source)
            if secs > 0:
                db.add(
                    UsageRecord(
                        user_id=s.user.id,
                        meeting_id=s.meeting.id,
                        kind="audio_seconds",
                        source=source,
                        provider=provider,
                        quantity=secs,
                    )
                )
        m = await db.get(Meeting, s.meeting.id)
        if m is not None:
            m.audio_seconds += s.used
            for source, column in SEQ_COLUMNS:
                seen = s.last_seq.get((s.meeting.id, source))
                if seen is not None:
                    current = getattr(m, column)
                    setattr(m, column, seen if current is None else max(current, seen))
        await db.commit()


@router.websocket("/meetings/{meeting_id}/stream")
async def stream(ws: WebSocket, meeting_id: str) -> None:
    app = ws.app
    settings: Settings = app.state.settings
    tokens: TokenService = app.state.tokens
    sm: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    active: ActiveStreams = app.state.active_streams
    provider: StreamingSTTProvider = app.state.stt_provider

    await ws.accept()
    try:
        first = await asyncio.wait_for(ws.receive_json(), AUTH_TIMEOUT_S)
        claims = tokens.verify(str(first.get("token", "")), "stream")
        if first.get("type") != "auth" or claims.get("mid") != meeting_id:
            raise TokenError("token not valid for this meeting")
    except (TimeoutError, TokenError, ValueError, KeyError, WebSocketDisconnect):
        with contextlib.suppress(Exception):
            await ws.close(Close.UNAUTHORIZED, "unauthorized")
        return

    async with sm() as db:
        user = await db.get(User, claims["sub"])
        meeting = await db.get(Meeting, meeting_id)
        if user is None or not user.is_active:
            await ws.close(Close.UNAUTHORIZED, "unauthorized")
            return
        if meeting is None or meeting.user_id != user.id or meeting.status != "active":
            await ws.close(Close.NOT_FOUND, "meeting not found or finished")
            return
        usage = await summary(db, settings, user)

    if usage.remaining_seconds <= 0:
        await ws.close(Close.QUOTA, "quota exceeded")
        return
    if not active.acquire(user.id, settings.max_concurrent_sessions_per_user):
        await ws.close(Close.CONFLICT, "another capture session is active")
        return
    # Seed replay protection from the database (survives server restarts).
    for source, column in SEQ_COLUMNS:
        persisted: int | None = getattr(meeting, column)
        if persisted is not None:
            key = (meeting.id, source)
            active.last_seq[key] = max(active.last_seq.get(key, -1), persisted)

    session = StreamSession(
        ws,
        meeting=meeting,
        user=user,
        provider=provider,
        remaining_seconds=usage.remaining_seconds,
        last_seq=active.last_seq,
    )
    try:
        await session.run()
    finally:
        active.release(user.id)
        # Shielded: an abrupt disconnect must not lose metered usage.
        persist = asyncio.ensure_future(_persist_usage(sm, session, provider.name))
        try:
            await asyncio.shield(persist)
        except asyncio.CancelledError:
            await asyncio.wait([persist], timeout=5)
            raise
        except Exception:
            log.exception("failed to persist usage", extra={"meeting_id": meeting_id})
        with contextlib.suppress(Exception):
            await ws.close(session.close_code, session.close_reason)
        log.info(
            "stream closed",
            extra={
                "meeting_id": meeting_id,
                "seconds": round(session.used, 2),
                "close_code": session.close_code,
            },
        )
