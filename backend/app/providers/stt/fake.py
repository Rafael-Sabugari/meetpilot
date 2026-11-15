"""Deterministic local STT for development and tests. Emits clearly-labelled placeholder text
based on the amount of audio received; it does not recognize speech."""

import asyncio
from collections.abc import AsyncIterator

from app.providers.stt.base import STTError
from app.realtime.events import AudioSource, TranscriptSegment

FRAME_MS = 20

# Scripted meeting lines (placeholder content; the app labels the provider as "fake").
# Includes questions directed at the user, a general question and a tag question, so the
# live-assistance features can be exercised end to end.
SCRIPT: dict[str, dict[str, list[str]]] = {
    "pt": {
        "system": [
            "Bom dia a todos, vamos começar a reunião.",
            "O módulo de pagamentos está quase pronto.",
            "Você pode explicar como funciona a integração com o banco?",
            "Certo?",
            "Qual é o prazo para a entrega do módulo?",
            "Precisamos fechar os testes até sexta-feira.",
            "Qual é a sua experiência com Kubernetes?",
            "E o prazo da integração, como fica",
        ],
        "microphone": [
            "Claro, posso explicar.",
            "A integração usa a API do banco com autenticação mútua.",
            "Acho que conseguimos entregar na sexta.",
        ],
    },
    "en": {
        "system": [
            "Good morning everyone, let's get started.",
            "The payments module is almost ready.",
            "Can you explain how the bank integration works?",
            "Right?",
            "What is the deadline for the module?",
            "We need to finish testing by Friday.",
            "What is your experience with Kubernetes?",
            "And the deadline for the integration, how does that look",
        ],
        "microphone": [
            "Sure, I can explain.",
            "The integration uses the bank API with mutual TLS.",
            "I think we can deliver on Friday.",
        ],
    },
}
PARTIAL_EVERY = 25  # frames (0.5 s)
FINAL_EVERY = 100  # frames (2 s)


class FakeSTTSession:
    def __init__(self, language: str, source: AudioSource, fail_after_frames: int | None) -> None:
        self.language = language
        self.source = source
        self.frames = 0
        self.keepalives = 0
        self._segment = 0
        self._segment_start = 0
        self._queue: asyncio.Queue[TranscriptSegment | STTError | None] = asyncio.Queue()
        self._fail_after = fail_after_frames
        self._closed = False

    async def send_audio(self, pcm: bytes) -> None:
        if self._closed:
            raise STTError("session closed")
        self.frames += 1
        if self._fail_after is not None and self.frames >= self._fail_after:
            await self._queue.put(STTError("fake provider failure"))
            return
        in_segment = self.frames - self._segment_start
        if in_segment % PARTIAL_EVERY == 0 and in_segment % FINAL_EVERY != 0:
            await self._queue.put(self._make(final=False))
        if in_segment % FINAL_EVERY == 0:
            await self._queue.put(self._make(final=True))
            self._segment += 1
            self._segment_start = self.frames

    def _make(self, *, final: bool) -> TranscriptSegment:
        label = "pt" if self.language.startswith("pt") else "en"
        lines = SCRIPT[label][self.source]
        text = lines[self._segment % len(lines)]
        if not final:
            words = text.split()
            text = " ".join(words[: max(1, len(words) * 3 // 5)]) + " …"
        return TranscriptSegment(
            segment_id=f"{self.source}-{self._segment}",
            source=self.source,
            text=text,
            language=self.language,
            start_ms=self._segment_start * FRAME_MS,
            end_ms=self.frames * FRAME_MS,
            is_final=final,
        )

    async def keepalive(self) -> None:
        self.keepalives += 1

    async def results(self) -> AsyncIterator[TranscriptSegment]:
        while True:
            item = await self._queue.get()
            if item is None:
                return
            if isinstance(item, STTError):
                raise item
            yield item

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self.frames - self._segment_start > 0:
            await self._queue.put(self._make(final=True))
        await self._queue.put(None)


class FakeSTTProvider:
    name = "fake"

    def __init__(self, fail_after_frames: int | None = None) -> None:
        self.fail_after_frames = fail_after_frames
        self.sessions: list[FakeSTTSession] = []

    async def open(self, *, language: str, source: AudioSource) -> FakeSTTSession:
        s = FakeSTTSession(language, source, self.fail_after_frames)
        self.sessions.append(s)
        return s
