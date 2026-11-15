"""Speech-to-text provider abstraction. Vendors are adapters behind this interface;
API keys stay server-side. Concrete adapters (e.g. Deepgram) arrive in the next slice
after verifying current APIs, pricing and data-retention terms."""

from collections.abc import AsyncIterator
from typing import Protocol

from app.realtime.events import AudioFormat, TranscriptSegment


class StreamingSTTSession(Protocol):
    async def send_audio(self, pcm: bytes) -> None: ...

    def results(self) -> AsyncIterator[TranscriptSegment]: ...

    async def close(self) -> None: ...


class StreamingSTTProvider(Protocol):
    name: str

    async def open(
        self, *, audio_format: AudioFormat, language: str | None, source: str
    ) -> StreamingSTTSession: ...
