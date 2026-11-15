import json
import logging

from app.core.logging import REDACTED, JsonRedactingFormatter, mask_secrets, redact


def _format(msg: str, **extra: object) -> dict[str, object]:
    record = logging.makeLogRecord({"name": "t", "levelname": "INFO", "msg": msg, **extra})
    out: dict[str, object] = json.loads(JsonRedactingFormatter().format(record))
    return out


def test_transcript_and_audio_fields_are_redacted() -> None:
    out = _format("segment", transcript="my salary is 10k", audio=b"\x00\x01", meeting_id="m1")
    assert out["transcript"] == REDACTED
    assert out["audio"] == REDACTED
    assert out["meeting_id"] == "m1"


def test_nested_sensitive_fields_are_redacted() -> None:
    assert redact({"payload": {"text": "hello", "seq": 1}}) == {
        "payload": {"text": REDACTED, "seq": 1}
    }


def test_bytes_are_summarized_never_dumped() -> None:
    assert redact(b"\x00" * 640) == "<640 bytes>"


def test_secrets_in_messages_are_masked() -> None:
    assert "abc.def" not in mask_secrets("Authorization: Bearer abc.def")
    assert "sk-live1234567890" not in mask_secrets("using sk-live1234567890 for call")
    assert "hunter2" not in mask_secrets("password=hunter2")
    out = _format("token=xyz123 failed")
    assert "xyz123" not in str(out["msg"])
