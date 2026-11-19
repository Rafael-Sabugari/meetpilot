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
        **({"copilot_utterance_gap_seconds": 0} | overrides),
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
        by_text = {d["text"]: d for d in detected}
        assert set(by_text) == {
            "Você pode explicar como funciona a integração com o banco?",
            "Qual é o prazo para a entrega do módulo?",
            "Qual é a sua experiência com Kubernetes?",
        }, "questions asked to the user; not 'Certo?' nor statements"
        assert all(d["directed"] for d in detected)
        # Clear cases are settled by the rules for free; unclear ones by the AI check.
        assert by_text["Você pode explicar como funciona a integração com o banco?"]["via"] == (
            "rules"
        )
        assert by_text["Qual é o prazo para a entrega do módulo?"]["via"] == "ai"
        started = of(events, "answer.started")
        completed = of(events, "answer.completed")
        assert sorted(a["question_id"] for a in completed) == sorted(
            s["question_id"] for s in started
        )
        assert sorted(a["question_id"] for a in completed) == sorted(
            d["question_id"] for d in detected
        )
        assert len({d["question_id"] for d in detected}) == 3
        assert len(completed) == 3
        completed.sort(key=lambda a: a["question_id"] != detected[0]["question_id"])
        first = completed[0]
        assert first["question"] == detected[0]["text"]
        assert first["answer"] and first["grounding"] in (
            "from_conversation",
            "general_suggestion",
            "needs_confirmation",
        )
        # The LLM saw the earlier conversation as context.
        first_answer = next(x for x in llm.calls if x["schema_name"] == "copilot_answer")
        assert "O módulo de pagamentos está quase pronto." in first_answer["input_text"]
        usage = c.get("/api/v1/usage", headers=auth).json()
        assert usage["copilot_requests_used"] == 3
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
        assert {d["text"] for d in of(events, "question.detected")} == {
            "Can you explain how the bank integration works?",
            "What is the deadline for the module?",
            "What is your experience with Kubernetes?",
        }
        first_answer = next(x for x in llm.calls if x["schema_name"] == "copilot_answer")
        assert "English" in first_answer["instructions"]


def test_deferred_answer_keeps_the_detected_question_id(tmp_path: Path) -> None:
    """With a long minimum interval the second question waits; its answer must still carry
    the id announced in question.detected so the overlay can match them."""
    llm = FakeLLMProvider()
    s = Settings(
        env="test",
        jwt_secret="k" * 40,
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'live.db'}",
        copilot_auto_min_interval_seconds=3600,
        copilot_utterance_gap_seconds=0,
    )
    with TestClient(create_app(s, stt_provider=FakeSTTProvider(), llm_provider=llm)) as c:
        auth = register_and_login(c)
        events = run_meeting(c, auth)
        detected = {d["question_id"] for d in of(events, "question.detected")}
        answered = [a["question_id"] for a in of(events, "answer.completed")]
        assert len(answered) == 1, "rate limit holds the second question"
        assert set(answered) <= detected


def test_questions_without_question_mark_are_found_by_the_ai_check(tmp_path: Path) -> None:
    """STT may drop the "?": "E o prazo da integração, como fica" is still a question."""
    llm = FakeLLMProvider()
    with make_client(tmp_path, llm) as c:
        auth = register_and_login(c)
        events = run_meeting(c, auth, system_lines=8)
        found = {d["text"]: d for d in of(events, "question.detected")}
        assert found["E o prazo da integração, como fica"]["via"] == "ai"
        checks = [x for x in llm.calls if x["schema_name"] == "question_check"]
        # Lines the rules could not settle go to the AI, which judges by meaning; lines the
        # rules settled (and tag questions like "Certo?") do not.
        checked = {x["input_text"].split("</transcript>")[0].splitlines()[-1] for x in checks}
        assert "Others: E o prazo da integração, como fica" in checked
        assert "Others: Qual é o prazo para a entrega do módulo?" in checked
        assert "Others: Bom dia a todos, vamos começar a reunião." in checked
        assert "Others: Certo?" not in checked
        assert "Others: Você pode explicar como funciona a integração com o banco?" not in checked
        rows = c.get("/api/v1/usage", headers=auth).json()
        assert rows["copilot_requests_used"] == 4, "checks do not use the copilot quota"


def test_user_context_reaches_answers_and_checks_but_is_not_stored(tmp_path: Path) -> None:
    llm = FakeLLMProvider()
    about = "Sou o Rafael, dev backend com 5 anos de Python e 2 de Kubernetes."
    with make_client(tmp_path, llm) as c:
        auth = register_and_login(c)
        events = run_meeting(c, auth, settings={"user_context": about}, system_lines=7)
        answers = of(events, "answer.completed")
        assert answers and all(a["grounding"] == "from_user_context" for a in answers)
        answer_calls = [x for x in llm.calls if x["schema_name"] == "copilot_answer"]
        assert all(f"<user_context>\n{about}" in x["input_text"] for x in answer_calls)
        check_calls = [x for x in llm.calls if x["schema_name"] == "question_check"]
        assert check_calls and all("<user_context>" in x["input_text"] for x in check_calls)
    db = (tmp_path / "live.db").read_bytes()
    assert b"Kubernetes" not in db and b"Rafael" not in db, "context is never persisted"


def test_user_context_is_limited(tmp_path: Path) -> None:
    llm = FakeLLMProvider()
    with make_client(tmp_path, llm) as c:
        auth = register_and_login(c)
        events = run_meeting(c, auth, settings={"user_context": "x" * 60_001}, system_lines=1)
        assert of(events, "error")[0]["code"] == "bad_settings"


def test_off_mode_spends_nothing_on_checks(tmp_path: Path) -> None:
    llm = FakeLLMProvider()
    with make_client(tmp_path, llm) as c:
        auth = register_and_login(c)
        run_meeting(c, auth, settings={"auto_suggestions": "off"}, system_lines=8)
        assert llm.calls == []


def test_auto_answers_also_come_in_the_users_language(tmp_path: Path) -> None:
    llm = FakeLLMProvider()
    with make_client(tmp_path, llm) as c:
        auth = register_and_login(c)
        events = run_meeting(c, auth, settings={"auto_suggestions": "all", "translate_to": "en"})
        completed = of(events, "answer.completed")
        assert completed
        # The answer streams first (answer.delta), arrives whole, then its translation.
        assert of(events, "answer.delta")
        translated = of(events, "answer.translated")
        assert {t["question_id"] for t in translated} == {a["question_id"] for a in completed}
        assert all(t["answer_translation"].startswith("(tradução de teste)") for t in translated)
        names = [e["type"] for e in events]
        assert names.index("answer.delta") < names.index("answer.completed")
        assert names.index("answer.completed") < names.index("answer.translated")

        events = run_meeting(c, auth, settings={"auto_suggestions": "all", "translate_to": None})
        assert of(events, "answer.completed") and not of(events, "answer.translated")


def test_later_auto_answers_see_the_earlier_suggestions(tmp_path: Path) -> None:
    llm = FakeLLMProvider()
    with make_client(tmp_path, llm) as c:
        auth = register_and_login(c)
        events = run_meeting(c, auth, settings={"auto_suggestions": "all"})
        assert len(of(events, "answer.completed")) >= 2
        answers = [x for x in llm.calls if x["schema_name"] == "copilot_answer"]
        assert "<earlier_suggestions>" not in answers[0]["input_text"]
        assert "<earlier_suggestions>" in answers[-1]["input_text"]
