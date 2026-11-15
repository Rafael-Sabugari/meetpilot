"""Smoke test against the real Deepgram API using your own key.

    MEETPILOT_DEEPGRAM_API_KEY=... uv run python scripts/deepgram_smoke.py speech.wav pt-BR

The WAV must be 16 kHz, mono, 16-bit PCM (e.g. produced by `mp-audio-probe --wav`).
Prints partial and final transcripts. Sends audio in real time (20 ms frames).
"""

import asyncio
import os
import sys
import wave

from app.providers.stt.base import STTError
from app.providers.stt.deepgram import DeepgramProvider


async def main(path: str, language: str) -> int:
    key = os.environ.get("MEETPILOT_DEEPGRAM_API_KEY")
    if not key:
        print("Set MEETPILOT_DEEPGRAM_API_KEY", file=sys.stderr)
        return 2
    with wave.open(path, "rb") as w:
        if (w.getframerate(), w.getnchannels(), w.getsampwidth()) != (16000, 1, 2):
            print("WAV must be 16 kHz mono 16-bit", file=sys.stderr)
            return 2
        pcm = w.readframes(w.getnframes())

    provider = DeepgramProvider(
        api_key=key, url="wss://api.deepgram.com/v1/listen", model="nova-3", mip_opt_out=True
    )
    session = await provider.open(language=language, source="system")

    async def show() -> None:
        try:
            async for seg in session.results():
                tag = "FINAL  " if seg.is_final else "partial"
                print(f"[{tag}] {seg.start_ms / 1000:6.2f}s  {seg.text}")
        except STTError as e:
            print(f"error: {e}", file=sys.stderr)

    printer = asyncio.create_task(show())
    for i in range(0, len(pcm), 640):
        await session.send_audio(pcm[i : i + 640])
        await asyncio.sleep(0.02)
    await session.close()
    await printer
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    sys.exit(asyncio.run(main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "pt-BR")))
