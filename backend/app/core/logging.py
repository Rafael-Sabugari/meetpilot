"""Operational logging that cannot leak meeting content or secrets.

Policy (docs/SECURITY_PRIVACY.md): transcripts, audio, prompts, model outputs and documents
never appear in standard application logs. Structured `extra` fields with sensitive names
are replaced, and credential-looking substrings in messages are masked.
"""

import json
import logging
import re
import sys
from typing import Any

SENSITIVE_KEYS = frozenset(
    {
        "audio",
        "pcm",
        "transcript",
        "text",
        "content",
        "prompt",
        "completion",
        "answer",
        "document",
        "password",
        "token",
        "access_token",
        "refresh_token",
        "authorization",
        "api_key",
        "secret",
    }
)
REDACTED = "[REDACTED]"

_SECRET_PATTERNS = [
    re.compile(r"(?i)(bearer\s+)[a-z0-9._\-]+"),
    re.compile(r"(?i)\b(sk|pk|rk)[-_][a-z0-9_\-]{8,}"),
    re.compile(r"(?i)(api[_-]?key|token|secret|password)(\s*[=:]\s*)\S+"),
]

_STD_ATTRS = set(vars(logging.makeLogRecord({})).keys()) | {"message", "asctime"}


def mask_secrets(message: str) -> str:
    out = _SECRET_PATTERNS[0].sub(r"\1" + REDACTED, message)
    out = _SECRET_PATTERNS[1].sub(REDACTED, out)
    out = _SECRET_PATTERNS[2].sub(r"\1\2" + REDACTED, out)
    return out


def redact(value: Any, key: str | None = None) -> Any:
    if key is not None and key.lower() in SENSITIVE_KEYS:
        return REDACTED
    if isinstance(value, dict):
        return {k: redact(v, str(k)) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [redact(v) for v in value]
    if isinstance(value, bytes | bytearray | memoryview):
        return f"<{len(value)} bytes>"
    if isinstance(value, str):
        return mask_secrets(value)
    return value


class JsonRedactingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "level": record.levelname,
            "logger": record.name,
            "msg": mask_secrets(record.getMessage()),
        }
        for k, v in vars(record).items():
            if k not in _STD_ATTRS and not k.startswith("_"):
                payload[k] = redact(v, k)
        if record.exc_info:
            payload["exc_type"] = record.exc_info[0].__name__ if record.exc_info[0] else None
        return json.dumps(payload, default=str)


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonRedactingFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
