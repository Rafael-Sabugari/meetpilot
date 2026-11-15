"""Speech-to-text provider abstraction. Vendors are adapters behind this interface; API keys
stay server-side. The gateway opens one streaming session per audio source."""

from collections.abc import AsyncIterator
from typing import Protocol

from app.realtime.events import AudioSource, TranscriptSegment


class STTError(Exception):
    """Provider failure surfaced to the user as a retryable error (never includes audio/text)."""

    def __init__(self, message: str, *, retryable: bool = True) -> None:
        super().__init__(message)
        self.retryable = retryable


class StreamingSTTSession(Protocol):
    async def send_audio(self, pcm: bytes) -> None: ...

    async def keepalive(self) -> None:
        """Keep the provider connection open while the user has paused."""
        ...

    def results(self) -> AsyncIterator[TranscriptSegment]:
        """Partial and final segments until the session closes. Raises STTError on failure."""
        ...

    async def close(self) -> None:
        """Flush pending audio (final results still arrive on `results()`), then close."""
        ...


class StreamingSTTProvider(Protocol):
    name: str

    async def open(self, *, language: str, source: AudioSource) -> StreamingSTTSession: ...
