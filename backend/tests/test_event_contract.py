"""The desktop (TypeScript) and backend (Python) must agree on realtime event names."""

import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.realtime.events import AudioFormat, Envelope, EventType

TS_FILE = Path(__file__).resolve().parents[2] / "packages/shared-types/src/realtime.ts"


def _ts_event_types() -> set[str]:
    src = TS_FILE.read_text(encoding="utf-8")
    block = re.search(r"REALTIME_EVENT_TYPES\s*=\s*\[(.*?)\]\s*as const", src, re.S)
    assert block, "REALTIME_EVENT_TYPES not found"
    return set(re.findall(r'"([a-z.]+)"', block.group(1)))


def test_event_names_match_typescript() -> None:
    assert _ts_event_types() == {e.value for e in EventType}


def test_schema_version_matches_typescript() -> None:
    src = TS_FILE.read_text(encoding="utf-8")
    assert "EVENT_SCHEMA_VERSION = 1" in src


def test_envelope_validates_and_rejects_unknown_fields() -> None:
    env = Envelope(type=EventType.TRANSCRIPT_PARTIAL, meeting_id="m1", seq=0)
    assert env.v == 1
    with pytest.raises(ValidationError):
        Envelope.model_validate({"type": "transcript.partial", "meeting_id": "m", "seq": 0, "x": 1})
    with pytest.raises(ValidationError):
        Envelope.model_validate({"type": "bot.joined", "meeting_id": "m", "seq": 0})


def test_audio_format_matches_desktop_pipeline() -> None:
    # crates/audio-capture: TARGET_SAMPLE_RATE=16000, mono, 20 ms PCM16 frames.
    f = AudioFormat()
    assert (f.encoding, f.sample_rate, f.channels, f.frame_ms) == ("pcm_s16le", 16000, 1, 20)
    assert f.sample_rate * f.frame_ms // 1000 * 2 == 640  # bytes per frame
