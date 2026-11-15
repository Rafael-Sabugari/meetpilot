"""Live assistance over the realtime stream: auto suggestions and translation."""

import struct
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.core.config import Settings
from app.main import create_app
from app.providers.llm.base import LLMError
from app.providers.llm.fake import FakeLLMProvider
from app.providers.stt.fake import FakeSTTProvider
from tests.conftest import register_and_login

PCM = b"\x10\x00" * 320
FINAL_FRAMES = 100  # fake STT emits one final per 2 s of audio


def frame(source: int, seq: int) -> bytes:
    return struct.pack("<BI", source, seq) + PCM


def make_client(tmp_path: Path, llm: FakeLLMProvider, **overrides: Any) -> TestClient:
    s = Settings(
        env="test",
        jwt_secret="k" * 40,
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'live.db'}",
        copilot_auto_min_interval_seconds=0,
        **overrides,
    )
    return TestClient(create_app(s, stt_provider=FakeSTTProvider(), llm_provider=llm))


def run_meeting(
    client: TestClient,
    auth: dict[str, str],
    *,
    language: str = "pt-BR",
    settings: dict[str, Any] | None = None,
    system_lines: int = 7,
    mic_lines: int = 0,
) -> list[dict[str, Any]]:
    m = client.post("/api/v1/meetings", json={"language": language}, headers=auth).json()
    events: list[dict[str, Any]] = []
    with client.websocket_connect(m["stream"]["stream_path"]) as ws:
        ws.send_json({"type": "auth", "token": m["stream"]["stream_token"]})
        events.append(ws.receive_json())
        if settings is not None:
            ws.send_json({"type": "copilot.settings", **settings})
        for i in range(system_lines * FINAL_FRAMES):
            ws.send_bytes(frame(0, i))
        for i in range(mic_lines * FINAL_FRAMES):
            ws.send_bytes(frame(1, i))
        ws.send_json({"type": "capture.stopped"})
        try:
            while True:
                events.append(ws.receive_json())
        except WebSocketDisconnect:
            pass
    return events


def of(events: list[dict[str, Any]], kind: str) -> list[dict[str, Any]]:
    return [e["payload"] for e in events if e["type"] == kind]


def test_directed_questions_get_automatic_suggestions(tmp_path: Path) -> None:
    llm = FakeLLMProvider()
    with make_client(tmp_path, llm) as c:
        auth = register_and_login(c)
        events = run_meeting(c, auth)
        detected = of(events, "question.detected")
        assert [d["text"] for d in detected] == [
            "Você pode explicar como funciona a integração com o banco?",
            "Qual é a sua experiência com Kubernetes?",
        ], "only questions directed at the user (default mode); not 'Certo?' nor general ones"
        assert all(d["directed"] for d in detected)
        started = of(events, "answer.started")
        completed = of(events, "answer.completed")
        assert [a["question_id"] for a in completed] == [s["question_id"] for s in started]
        assert [a["question_id"] for a in completed] == [d["question_id"] for d in detected]
        assert len({d["question_id"] for d in detected}) == 2
        assert len(completed) == 2
        first = completed[0]
        assert first["question"] == detected[0]["text"]
        assert first["answer"] and first["grounding"] in (
            "from_conversation",
            "general_suggestion",
            "needs_confirmation",
        )
        # The LLM saw the earlier conversation as context.
        assert "O módulo de pagamentos está quase pronto." in llm.calls[0]["input_text"]
        usage = c.get("/api/v1/usage", headers=auth).json()
        assert usage["copilot_requests_used"] == 2
        # Answers arrive before the stream is closed.
        types = [e["type"] for e in events]
        assert types.index("answer.completed") < types.index("capture.stopped")


def test_all_mode_includes_general_questions_and_off_disables(tmp_path: Path) -> None:
    llm = FakeLLMProvider()
    with make_client(tmp_path, llm) as c:
        auth = register_and_login(c)
        all_mode = run_meeting(c, auth, settings={"auto_suggestions": "all"})
        texts = [d["text"] for d in of(all_mode, "question.detected")]
        assert "Qual é o prazo para a entrega do módulo?" in texts
        assert "Certo?" not in texts
        off = run_meeting(c, auth, settings={"auto_suggestions": "off"})
        assert of(off, "question.detected") == []
        assert of(off, "answer.started") == []


def test_translation_of_final_lines_once_each(tmp_path: Path) -> None:
    llm = FakeLLMProvider()
    with make_client(tmp_path, llm) as c:
        auth = register_and_login(c)
        events = run_meeting(
            c,
            auth,
            settings={"auto_suggestions": "off", "translate_to": "en"},
            system_lines=3,
            mic_lines=2,
        )
        finals = of(events, "transcript.final")
        translations = of(events, "translation.updated")
        assert {t["segment_id"] for t in translations} == {f["segment_id"] for f in finals}
        assert len(translations) == len(finals), "no segment translated twice"
        assert all(t["target_language"] == "en" for t in translations)
        assert all(t["text"].startswith("(tradução de teste)") for t in translations)
        n = sum(1 for call in llm.calls if call["schema_name"] == "translation")
        assert n == len(finals), "partials are never translated"


def test_no_translation_when_target_is_the_meeting_language(tmp_path: Path) -> None:
    llm = FakeLLMProvider()
    with make_client(tmp_path, llm) as c:
        auth = register_and_login(c)
        events = run_meeting(
            c, auth, settings={"auto_suggestions": "off", "translate_to": "pt-BR"}, system_lines=2
        )
        assert of(events, "translation.updated") == []


def test_auto_answers_respect_the_monthly_quota(tmp_path: Path) -> None:
    llm = FakeLLMProvider()
    with make_client(tmp_path, llm, copilot_requests_per_month=1) as c:
        auth = register_and_login(c)
        events = run_meeting(c, auth)
        assert len(of(events, "answer.completed")) == 1
        failed = of(events, "answer.failed")
        assert len(failed) == 1 and failed[0]["code"] == "copilot_quota"
        assert failed[0]["retryable"] is False


def test_llm_failure_is_reported_and_not_billed(tmp_path: Path) -> None:
    llm = FakeLLMProvider(fail=LLMError("the AI took too long to answer", retryable=True))
    with make_client(tmp_path, llm) as c:
        auth = register_and_login(c)
        events = run_meeting(c, auth, system_lines=3)
        failed = of(events, "answer.failed")
        assert failed and failed[0]["code"] == "llm_error" and failed[0]["retryable"] is True
        assert c.get("/api/v1/usage", headers=auth).json()["copilot_requests_used"] == 0


def test_invalid_settings_are_rejected_without_closing(tmp_path: Path) -> None:
    llm = FakeLLMProvider()
    with make_client(tmp_path, llm) as c:
        auth = register_and_login(c)
        events = run_meeting(c, auth, settings={"auto_suggestions": "sometimes"}, system_lines=1)
        errors = of(events, "error")
        assert errors and errors[0]["code"] == "bad_settings"
        assert events[-1]["type"] == "capture.stopped"


def test_finishing_a_meeting_forgets_its_live_context(tmp_path: Path) -> None:
    llm = FakeLLMProvider()
    with make_client(tmp_path, llm) as c:
        auth = register_and_login(c)
        run_meeting(c, auth, system_lines=2)
        mid = c.get("/api/v1/meetings", headers=auth).json()[0]["id"]
        assert mid in c.app.state.active_streams.contexts  # type: ignore[attr-defined]
        c.post(f"/api/v1/meetings/{mid}/finish", headers=auth)
        assert mid not in c.app.state.active_streams.contexts  # type: ignore[attr-defined]


@pytest.mark.parametrize("language", ["en"])
def test_english_meeting(tmp_path: Path, language: str) -> None:
    llm = FakeLLMProvider()
    with make_client(tmp_path, llm) as c:
        auth = register_and_login(c)
        events = run_meeting(c, auth, language=language)
        assert [d["text"] for d in of(events, "question.detected")] == [
            "Can you explain how the bank integration works?",
            "What is your experience with Kubernetes?",
        ]
        assert "English" in llm.calls[0]["instructions"]


def test_deferred_answer_keeps_the_detected_question_id(tmp_path: Path) -> None:
    """With a long minimum interval the second question waits; its answer must still carry
    the id announced in question.detected so the overlay can match them."""
    llm = FakeLLMProvider()
    s = Settings(
        env="test",
        jwt_secret="k" * 40,
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'live.db'}",
        copilot_auto_min_interval_seconds=3600,
    )
    with TestClient(create_app(s, stt_provider=FakeSTTProvider(), llm_provider=llm)) as c:
        auth = register_and_login(c)
        events = run_meeting(c, auth)
        detected = {d["question_id"] for d in of(events, "question.detected")}
        answered = [a["question_id"] for a in of(events, "answer.completed")]
        assert len(answered) == 1, "rate limit holds the second question"
        assert set(answered) <= detected
